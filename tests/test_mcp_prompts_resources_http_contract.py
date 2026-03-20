import asyncio
import base64
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest


def _build_test_api(
    monkeypatch,
    *,
    get_session_style: str = "positional",
    prompt_signature: str = "standard",
    capability_mode: str = "normal",
    session_materialization: str = "eager",
    agent_supports_server_name: bool = True,
    task_creation_mode: str = "normal",
    task_status_mode: str = "normal",
):
    from fastapi import FastAPI

    from app.api.dependencies import get_session_manager
    from app.core.mcp_wrapper import MCPWrapper, _GuardedMCPClient
    from app.core.session_manager import SessionManager
    from config import settings

    mgr = SessionManager()
    monkeypatch.setattr("app.api.dependencies._session_manager", mgr, raising=False)
    monkeypatch.setattr(MCPWrapper, "_import_dependencies", lambda self: None)
    monkeypatch.setattr(settings.multi_tenancy, "enabled", True, raising=False)
    monkeypatch.setattr(settings.multi_tenancy, "require_header", False, raising=False)
    monkeypatch.setattr(settings.multi_tenancy, "default_tenant_id", "default", raising=False)

    class _ProtocolSessionStub:
        def __init__(self, session_stub):
            self.session_stub = session_stub
            self.wrapper = session_stub.wrapper
            self._tasks = {}
            self._task_counter = 0

        async def send_request(self, request, result_type):
            from mcp.shared.exceptions import McpError
            from mcp.types import ErrorData

            payload = request.model_dump(by_alias=True, exclude_none=False)
            self.session_stub.protocol_calls.append(payload)
            method = payload["method"]
            params = payload.get("params") or {}

            if method == "tools/call":
                name = params["name"]
                arguments = dict(params.get("arguments") or {})
                if name != "simulate-research-query":
                    raise AssertionError(f"Unexpected task-aware tool call for {name}")

                if task_creation_mode == "real_invalid_task_creation_result" and "topic" not in arguments:
                    raise McpError(
                        ErrorData(
                            code=-32602,
                            message=(
                                "Invalid task creation result: ["
                                "{\"expected\":\"object\",\"code\":\"invalid_type\",\"path\":[\"task\"],"
                                "\"message\":\"Invalid input: expected object, received undefined\"}]"
                            ),
                        )
                    )

                if "task" not in params:
                    return result_type.model_validate(
                        {
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        "MCP error -32601: Tool simulate-research-query requires task "
                                        "augmentation (taskSupport: 'required')"
                                    ),
                                }
                            ],
                            "isError": True,
                        }
                    )

                self._task_counter += 1
                task_id = f"{self.session_stub.server_name}-task-{self._task_counter}"
                self.session_stub.task_calls.append(
                    {
                        "name": name,
                        "arguments": arguments,
                        "task": dict(params.get("task") or {}),
                    }
                )
                self._tasks[task_id] = {
                    "name": name,
                    "arguments": arguments,
                }
                return result_type.model_validate(
                    {
                        "task": {
                            "taskId": task_id,
                            "status": "working",
                            "pollInterval": 0,
                        }
                    }
                )

            if method == "tasks/result":
                task_id = params["taskId"]
                task = self._tasks[task_id]
                name = task["name"]
                arguments = task["arguments"]

                await self.wrapper._handle_runtime_message(
                    SimpleNamespace(
                        method="notifications/tasks/status",
                        params={
                            "taskId": task_id,
                            "status": "working",
                            "pollInterval": 0,
                            "statusMessage": "Task is working",
                        },
                    )
                )

                if task_status_mode == "pause_after_working":
                    await asyncio.sleep(0.05)

                if arguments.get("ambiguous") is True:
                    await self.wrapper._handle_runtime_message(
                        SimpleNamespace(
                            method="notifications/tasks/status",
                            params={
                                "taskId": task_id,
                                "status": "input_required",
                                "pollInterval": 0,
                                "statusMessage": "Task requires additional input",
                            },
                        )
                    )
                    if task_status_mode == "pause_on_input_required":
                        await asyncio.sleep(0.05)
                    resume = await self.wrapper._handle_protocol_elicitation(
                        SimpleNamespace(
                            request_id=f"task-{task_id}",
                            meta={"io.modelcontextprotocol/related-task": {"taskId": task_id}},
                        ),
                        SimpleNamespace(
                            message="Please disambiguate the research query",
                            requestedSchema={
                                "type": "object",
                                "properties": {
                                    "topic": {"type": "string"},
                                    "region": {"type": "string"},
                                },
                                "required": ["topic"],
                            },
                            meta={"io.modelcontextprotocol/related-task": {"taskId": task_id}},
                        ),
                    )
                    if resume.action == "decline":
                        raise McpError(ErrorData(code=-32603, message="Elicitation declined"))
                    if resume.action == "cancel":
                        raise McpError(ErrorData(code=-32800, message="Task cancelled"))
                    return result_type.model_validate(
                        {
                            "tool": name,
                            "server": self.session_stub.server_name,
                            "arguments": arguments,
                            "resolved_with": resume.content,
                        }
                    )

                return result_type.model_validate(
                    {
                        "tool": name,
                        "server": self.session_stub.server_name,
                        "arguments": arguments,
                        "status": "completed",
                    }
                )

            raise AssertionError(f"Unsupported raw MCP method: {method}")

    class _SessionStub:
        def __init__(self, server_name: str, wrapper):
            self.server_name = server_name
            self.wrapper = wrapper
            self.prompt_calls = []
            self.resource_calls = []
            self.tool_calls = []
            self.task_calls = []
            self.protocol_calls = []
            self.capability_mode = capability_mode
            self.connector = SimpleNamespace(
                client_session=_ProtocolSessionStub(self),
                capabilities={"tasks": {"requests": {"tools": {"call": {}}}}},
            )

        def __getattribute__(self, item):
            capability_state = object.__getattribute__(self, "capability_mode")
            if capability_state == "missing_list_prompts" and item == "list_prompts":
                raise AttributeError(item)
            if prompt_signature == "keyword_name" and item == "get_prompt":
                raise AttributeError(item)
            return object.__getattribute__(self, item)

        async def list_prompts(self):
            return {
                "prompts": [
                    {
                        "name": f"{self.server_name}-welcome",
                        "description": f"Prompt for {self.server_name}",
                        "arguments": [
                            {
                                "name": "topic",
                                "description": "Topic to discuss",
                                "required": True,
                            }
                        ],
                    }
                ]
            }

        async def get_prompt(self, name: str, arguments: dict | None = None):
            self.prompt_calls.append({"name": name, "arguments": dict(arguments or {})})
            if (arguments or {}).get("explode_internal_typeerror"):
                return 1 + "x"
            return {
                "description": f"Rendered {name} on {self.server_name}",
                "messages": [
                    {
                        "role": "user",
                        "content": {
                            "type": "text",
                            "text": f"{self.server_name}:{name}:{(arguments or {}).get('topic', '')}",
                        },
                    }
                ],
            }

        async def render_prompt(self, *, name: str, arguments: dict | None = None):
            self.prompt_calls.append({"name": name, "arguments": dict(arguments or {})})
            return {
                "description": f"Rendered {name} on {self.server_name}",
                "messages": [
                    {
                        "role": "user",
                        "content": {
                            "type": "text",
                            "text": f"{self.server_name}:{name}:{(arguments or {}).get('topic', '')}",
                        },
                    }
                ],
            }

        async def list_resources(self):
            return {
                "resources": [
                    {
                        "uri": f"memo://{self.server_name}/guide",
                        "name": f"{self.server_name} guide",
                        "description": f"Guide for {self.server_name}",
                        "mimeType": "text/plain",
                        "size": 12,
                    }
                ]
            }

        async def list_tools(self):
            return [
                {
                    "name": "plain-tool",
                    "description": "Standard MCP tool",
                    "inputSchema": {"type": "object"},
                },
                {
                    "name": "simulate-research-query",
                    "description": "Task-aware research simulation tool",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "topic": {"type": "string"},
                            "ambiguous": {"type": "boolean"},
                        },
                        "required": ["topic"],
                    },
                    "execution": {"taskSupport": "required"},
                },
            ]

        async def read_resource(self, uri: str):
            self.resource_calls.append({"uri": uri})
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "text/plain",
                        "text": f"text:{self.server_name}:{uri}",
                    },
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "structuredContent": {"server": self.server_name, "uri": uri},
                    },
                    {
                        "uri": uri,
                        "mimeType": "application/octet-stream",
                        "blob": b"\x00\x01\x02",
                    },
                ]
            }

        async def call_tool(self, name: str, arguments: dict | None = None):
            tool_arguments = dict(arguments or {})
            self.tool_calls.append({"name": name, "arguments": tool_arguments})

            if name == "plain-tool":
                return {
                    "tool": name,
                    "server": self.server_name,
                    "arguments": tool_arguments,
                    "status": "completed",
                }

            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "MCP error -32601: Tool simulate-research-query requires task "
                            "augmentation (taskSupport: 'required')"
                        ),
                    }
                ],
                "isError": True,
            }

    class _BaseClientStub:
        def __init__(self, server_names, wrapper):
            self.sessions = {
                server_name: _SessionStub(server_name, wrapper)
                for server_name in server_names
            }
            self.active_sessions = (
                dict(self.sessions)
                if session_materialization == "eager"
                else {}
            )

        def activate_session(self, server_name: str):
            session = self.sessions[server_name]
            self.active_sessions[server_name] = session
            return session

        def _require_active_session(self, server_name: str):
            session = self.active_sessions.get(server_name)
            if session is None:
                raise RuntimeError(f"No session exists for server '{server_name}'")
            return session

        def __getattribute__(self, item):
            if item == "get_session":
                if get_session_style == "positional":
                    return object.__getattribute__(self, "get_session_positional")
                if get_session_style == "keyword_only":
                    return object.__getattribute__(self, "get_session_keyword_only")
                if get_session_style == "name_kw":
                    return object.__getattribute__(self, "get_session_name_kw")
                if get_session_style == "sync_positional":
                    return object.__getattribute__(self, "get_session_sync_positional")
                if get_session_style == "sync_keyword_only":
                    return object.__getattribute__(self, "get_session_sync_keyword_only")
                if get_session_style == "sync_name_kw":
                    return object.__getattribute__(self, "get_session_sync_name_kw")
                raise AssertionError(f"Unsupported get_session_style: {get_session_style}")
            return object.__getattribute__(self, item)

        async def get_session_positional(self, server_name: str):
            return self._require_active_session(server_name)

        async def get_session_keyword_only(self, *, server_name: str):
            return self._require_active_session(server_name)

        async def get_session_name_kw(self, *, name: str):
            server_name = name
            return self._require_active_session(server_name)

        def get_session_sync_positional(self, server_name: str):
            return self._require_active_session(server_name)

        def get_session_sync_keyword_only(self, *, server_name: str):
            return self._require_active_session(server_name)

        def get_session_sync_name_kw(self, *, name: str):
            server_name = name
            return self._require_active_session(server_name)

        async def create_session(self, server_name: str, auto_initialize: bool = True):
            return self.activate_session(server_name)

        async def create_all_sessions(self, auto_initialize: bool = True):
            for server_name in self.sessions:
                self.activate_session(server_name)
            return dict(self.active_sessions)

        async def get_all_active_sessions(self):
            return dict(self.active_sessions)

        async def close_all_sessions(self):
            return None

    class _AgentStub:
        def __init__(self, wrapper):
            self.wrapper = wrapper
            self.steps_used = 0
            self.last_server_used = None

        def _mark_server(self, server_name: str | None):
            if server_name is not None:
                self.last_server_used = server_name
                self.wrapper._base_client.activate_session(server_name)
            elif len(self.wrapper.mcp_servers) == 1:
                self.last_server_used = next(iter(self.wrapper.mcp_servers))
                self.wrapper._base_client.activate_session(self.last_server_used)
            else:
                self.last_server_used = None

        async def run_with_server_name(self, query: str, max_steps=None, server_name=None):
            self.steps_used = 2
            self._mark_server(server_name)
            return f"QUERY:{query}"

        async def run_without_server_name(self, query: str, max_steps=None):
            self.steps_used = 2
            self._mark_server(getattr(self.wrapper, "_active_server_name", None))
            return f"QUERY:{query}"

        def __getattribute__(self, item):
            if item == "run":
                if agent_supports_server_name:
                    return object.__getattribute__(self, "run_with_server_name")
                return object.__getattribute__(self, "run_without_server_name")
            return object.__getattribute__(self, item)

    async def _stub_initialize(self):
        if getattr(self, "_initialized", False):
            return

        base_client = _BaseClientStub(self.mcp_servers.keys(), self)
        self._base_client = base_client
        self._client = _GuardedMCPClient(base_client, self)
        self._agent = _AgentStub(self)
        self._initialized = True

    monkeypatch.setattr(MCPWrapper, "initialize", _stub_initialize)

    from app.api.routes.queries import router as queries_router
    from app.api.routes.sessions import router as sessions_router

    app = FastAPI()
    app.include_router(sessions_router, prefix="/sessions")
    app.include_router(queries_router, prefix="/sessions")
    app.dependency_overrides[get_session_manager] = lambda: mgr

    return app, mgr


def _build_test_app(
    monkeypatch,
    *,
    get_session_style: str = "positional",
    prompt_signature: str = "standard",
    capability_mode: str = "normal",
    session_materialization: str = "eager",
    agent_supports_server_name: bool = True,
    task_creation_mode: str = "normal",
    task_status_mode: str = "normal",
):
    from fastapi.testclient import TestClient

    app, mgr = _build_test_api(
        monkeypatch,
        get_session_style=get_session_style,
        prompt_signature=prompt_signature,
        capability_mode=capability_mode,
        session_materialization=session_materialization,
        agent_supports_server_name=agent_supports_server_name,
        task_creation_mode=task_creation_mode,
        task_status_mode=task_status_mode,
    )
    return TestClient(app), mgr


def _create_session(client, *, tenant_id: str, mcp_servers: dict):
    payload = {
        "llm_provider": {"provider": "ollama", "model": "dummy", "temperature": 0},
        "mcp_servers": mcp_servers,
    }
    response = client.post(
        "/sessions",
        json=payload,
        headers={"X-Tenant-Id": tenant_id},
    )
    assert response.status_code == 200, response.text
    return response.json()["session_id"]


async def _create_session_async(client, *, tenant_id: str, mcp_servers: dict):
    payload = {
        "llm_provider": {"provider": "ollama", "model": "dummy", "temperature": 0},
        "mcp_servers": mcp_servers,
    }
    response = await client.post(
        "/sessions",
        json=payload,
        headers={"X-Tenant-Id": tenant_id},
    )
    assert response.status_code == 200, response.text
    return response.json()["session_id"]


def _server_config():
    return {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-everything"],
    }


async def _wait_for_operation_status(
    client,
    *,
    session_id: str,
    operation_id: str,
    tenant_id: str,
    expected_status: str,
    timeout: float = 2.0,
):
    deadline = time.monotonic() + timeout
    last_body = None
    while time.monotonic() < deadline:
        response = await client.get(
            f"/sessions/{session_id}/query-operations/{operation_id}",
            headers={"X-Tenant-Id": tenant_id},
        )
        assert response.status_code == 200, response.text
        last_body = response.json()
        if last_body["status"] == expected_status:
            return last_body
        await asyncio.sleep(0.01)

    raise AssertionError(f"Timed out waiting for status {expected_status!r}: {last_body}")


def test_prompt_list_and_render_routes(monkeypatch):
    client, mgr = _build_test_app(monkeypatch)
    session_id = _create_session(
        client,
        tenant_id="tenant-a",
        mcp_servers={"alpha": _server_config(), "beta": _server_config()},
    )

    list_response = client.get(
        f"/sessions/{session_id}/prompts",
        params={"server_name": "alpha"},
        headers={"X-Tenant-Id": "tenant-a"},
    )
    assert list_response.status_code == 200, list_response.text
    list_body = list_response.json()
    assert list_body["server_name"] == "alpha"
    assert list_body["prompts"][0]["name"] == "alpha-welcome"
    assert list_body["prompts"][0]["arguments"][0]["name"] == "topic"

    render_response = client.post(
        f"/sessions/{session_id}/prompts/beta-welcome/render",
        json={"server_name": "beta", "arguments": {"topic": "bridges"}},
        headers={"X-Tenant-Id": "tenant-a"},
    )
    assert render_response.status_code == 200, render_response.text
    render_body = render_response.json()
    assert render_body["server_name"] == "beta"
    assert render_body["prompt_name"] == "beta-welcome"
    assert render_body["messages"][0]["content"]["text"] == "beta:beta-welcome:bridges"

    import asyncio

    session_data = asyncio.run(mgr.get_session(session_id, tenant_id="tenant-a"))
    beta_session = session_data.wrapper._base_client.sessions["beta"]
    assert beta_session.prompt_calls == [
        {"name": "beta-welcome", "arguments": {"topic": "bridges"}}
    ]


def test_capability_lookup_supports_sync_get_session(monkeypatch):
    client, _mgr = _build_test_app(monkeypatch, get_session_style="sync_positional")
    session_id = _create_session(
        client,
        tenant_id="tenant-a",
        mcp_servers={"everything": _server_config()},
    )

    response = client.get(
        f"/sessions/{session_id}/prompts",
        headers={"X-Tenant-Id": "tenant-a"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["server_name"] == "everything"
    assert response.json()["prompts"][0]["name"] == "everything-welcome"


def test_capability_lookup_supports_async_get_session(monkeypatch):
    client, _mgr = _build_test_app(monkeypatch, get_session_style="keyword_only")
    session_id = _create_session(
        client,
        tenant_id="tenant-a",
        mcp_servers={"everything": _server_config()},
    )

    response = client.get(
        f"/sessions/{session_id}/prompts",
        headers={"X-Tenant-Id": "tenant-a"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["server_name"] == "everything"
    assert response.json()["prompts"][0]["name"] == "everything-welcome"


def test_capabilities_materialize_server_session_on_first_access_without_warmup(monkeypatch):
    client, mgr = _build_test_app(
        monkeypatch,
        get_session_style="keyword_only",
        session_materialization="lazy",
    )
    session_id = _create_session(
        client,
        tenant_id="tenant-a",
        mcp_servers={"everything": _server_config()},
    )

    prompts_response = client.get(
        f"/sessions/{session_id}/prompts",
        headers={"X-Tenant-Id": "tenant-a"},
    )
    assert prompts_response.status_code == 200, prompts_response.text
    assert prompts_response.json()["server_name"] == "everything"

    render_response = client.post(
        f"/sessions/{session_id}/prompts/everything-welcome/render",
        json={"arguments": {"topic": "lazy init"}},
        headers={"X-Tenant-Id": "tenant-a"},
    )
    assert render_response.status_code == 200, render_response.text
    assert render_response.json()["messages"][0]["content"]["text"] == "everything:everything-welcome:lazy init"

    list_resources_response = client.get(
        f"/sessions/{session_id}/resources",
        headers={"X-Tenant-Id": "tenant-a"},
    )
    assert list_resources_response.status_code == 200, list_resources_response.text
    assert list_resources_response.json()["server_name"] == "everything"

    read_resource_response = client.post(
        f"/sessions/{session_id}/resources/read",
        json={"uri": "memo://everything/guide"},
        headers={"X-Tenant-Id": "tenant-a"},
    )
    assert read_resource_response.status_code == 200, read_resource_response.text
    assert read_resource_response.json()["contents"][0]["text"] == "text:everything:memo://everything/guide"

    session_data = asyncio.run(mgr.get_session(session_id, tenant_id="tenant-a"))
    assert "everything" in session_data.wrapper._base_client.active_sessions


def test_resource_list_and_read_routes_expose_text_structured_and_binary(monkeypatch):
    client, _mgr = _build_test_app(monkeypatch)
    session_id = _create_session(
        client,
        tenant_id="tenant-a",
        mcp_servers={"alpha": _server_config()},
    )

    list_response = client.get(
        f"/sessions/{session_id}/resources",
        headers={"X-Tenant-Id": "tenant-a"},
    )
    assert list_response.status_code == 200, list_response.text
    list_body = list_response.json()
    assert list_body["server_name"] == "alpha"
    assert list_body["resources"][0]["uri"] == "memo://alpha/guide"

    read_response = client.post(
        f"/sessions/{session_id}/resources/read",
        json={"uri": "memo://alpha/guide"},
        headers={"X-Tenant-Id": "tenant-a"},
    )
    assert read_response.status_code == 200, read_response.text
    read_body = read_response.json()
    assert read_body["server_name"] == "alpha"
    assert read_body["contents"][0]["text"] == "text:alpha:memo://alpha/guide"
    assert read_body["contents"][1]["structured"] == {
        "server": "alpha",
        "uri": "memo://alpha/guide",
    }
    assert read_body["contents"][2]["blob_base64"] == base64.b64encode(b"\x00\x01\x02").decode("ascii")


def test_resource_read_route_supports_real_mcp_read_resource_result(monkeypatch):
    from mcp import types

    client, mgr = _build_test_app(monkeypatch)
    session_id = _create_session(
        client,
        tenant_id="tenant-a",
        mcp_servers={"everything": _server_config()},
    )

    session_data = asyncio.run(mgr.get_session(session_id, tenant_id="tenant-a"))
    session = session_data.wrapper._base_client.sessions["everything"]

    async def _read_resource_runtime_shape(uri: str):
        return types.ReadResourceResult(
            contents=[
                types.TextResourceContents(
                    uri=uri,
                    mimeType="text/markdown",
                    text="# Structure",
                ),
                types.BlobResourceContents(
                    uri=uri,
                    mimeType="application/octet-stream",
                    blob=base64.b64encode(b"\x00\x01").decode("ascii"),
                ),
            ]
        )

    session.read_resource = _read_resource_runtime_shape

    read_response = client.post(
        f"/sessions/{session_id}/resources/read",
        json={"uri": "demo://resource/static/document/structure.md"},
        headers={"X-Tenant-Id": "tenant-a"},
    )
    assert read_response.status_code == 200, read_response.text
    read_body = read_response.json()
    assert read_body["server_name"] == "everything"
    assert read_body["contents"][0]["uri"] == "demo://resource/static/document/structure.md"
    assert read_body["contents"][0]["text"] == "# Structure"
    assert read_body["contents"][1]["blob_base64"] == base64.b64encode(b"\x00\x01").decode("ascii")


def test_server_name_resolution_for_new_capabilities(monkeypatch):
    client, _mgr = _build_test_app(monkeypatch)

    multi_session_id = _create_session(
        client,
        tenant_id="tenant-a",
        mcp_servers={"alpha": _server_config(), "beta": _server_config()},
    )
    multi_response = client.get(
        f"/sessions/{multi_session_id}/prompts",
        headers={"X-Tenant-Id": "tenant-a"},
    )
    assert multi_response.status_code == 400
    multi_detail = multi_response.json()["detail"]
    assert multi_detail["code"] == "MCP_CONFIGURATION_ERROR"
    assert "server_name is required" in multi_detail["message"]

    single_session_id = _create_session(
        client,
        tenant_id="tenant-a",
        mcp_servers={"solo": _server_config()},
    )
    single_response = client.get(
        f"/sessions/{single_session_id}/prompts",
        headers={"X-Tenant-Id": "tenant-a"},
    )
    assert single_response.status_code == 200, single_response.text
    assert single_response.json()["server_name"] == "solo"


def test_new_capability_routes_enforce_tenant_ownership(monkeypatch):
    client, _mgr = _build_test_app(monkeypatch)
    session_id = _create_session(
        client,
        tenant_id="tenant-a",
        mcp_servers={"alpha": _server_config()},
    )

    response = client.get(
        f"/sessions/{session_id}/resources",
        headers={"X-Tenant-Id": "tenant-b"},
    )
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["code"] == "MCP_SESSION_NOT_FOUND"
    assert detail["operation"] == "list_resources"


def test_existing_query_flow_still_works(monkeypatch):
    client, _mgr = _build_test_app(monkeypatch)
    session_id = _create_session(
        client,
        tenant_id="tenant-a",
        mcp_servers={},
    )

    response = client.post(
        f"/sessions/{session_id}/query",
        json={"query": "hello"},
        headers={"X-Tenant-Id": "tenant-a"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["session_id"] == session_id
    assert body["result"] == "QUERY:hello"
    assert body["steps_used"] == 2
    assert body["has_mcp_servers"] is False


def test_sync_query_route_supports_agent_runtime_without_server_name_kwarg(monkeypatch):
    client, _mgr = _build_test_app(
        monkeypatch,
        agent_supports_server_name=False,
    )
    session_id = _create_session(
        client,
        tenant_id="tenant-a",
        mcp_servers={"everything": _server_config()},
    )

    response = client.post(
        f"/sessions/{session_id}/query",
        json={"query": "hello", "server_name": "everything"},
        headers={"X-Tenant-Id": "tenant-a"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result"] == "QUERY:hello"
    assert body["server_used"] == "everything"
    assert body["has_mcp_servers"] is True


def test_query_operation_create_returns_queued_state(monkeypatch):
    from app.core.mcp_wrapper import MCPWrapper

    async def _run_query(self, query: str, max_steps=None, server_name=None):
        await asyncio.sleep(0.05)
        self._steps_used = 3
        self._last_server_used = server_name
        return f"ASYNC:{query}"

    monkeypatch.setattr(MCPWrapper, "run_query", _run_query)

    client, _mgr = _build_test_app(monkeypatch)
    session_id = _create_session(
        client,
        tenant_id="tenant-a",
        mcp_servers={"alpha": _server_config()},
    )

    response = client.post(
        f"/sessions/{session_id}/query-operations",
        json={"query": "hello async", "server_name": "alpha"},
        headers={"X-Tenant-Id": "tenant-a", "X-Run-Id": "run-op-1"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["session_id"] == session_id
    assert body["status"] == "queued"
    assert body["operation_id"]
    assert body["metadata"]["request"] == {
        "query": "hello async",
        "max_steps": None,
        "server_name": "alpha",
    }
    assert body["result"] is None
    assert body["error"] is None
    assert body["requires_input"] is False
    assert body["pending_interaction"] is None


@pytest.mark.asyncio
async def test_query_operation_polling_reaches_completed_with_result(monkeypatch):
    from app.core.mcp_wrapper import MCPWrapper
    from httpx import ASGITransport, AsyncClient

    release_query = asyncio.Event()

    async def _run_query(self, query: str, max_steps=None, server_name=None):
        await release_query.wait()
        self._steps_used = 4
        self._last_server_used = server_name or "alpha"
        return f"ASYNC:{query}"

    monkeypatch.setattr(MCPWrapper, "run_query", _run_query)

    app, _mgr = _build_test_api(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        session_id = await _create_session_async(
            client,
            tenant_id="tenant-a",
            mcp_servers={"alpha": _server_config()},
        )

        create_response = await client.post(
            f"/sessions/{session_id}/query-operations",
            json={"query": "poll me", "server_name": "alpha", "max_steps": 7},
            headers={"X-Tenant-Id": "tenant-a"},
        )
        assert create_response.status_code == 200, create_response.text
        operation_id = create_response.json()["operation_id"]

        running_body = None
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            poll_response = await client.get(
                f"/sessions/{session_id}/query-operations/{operation_id}",
                headers={"X-Tenant-Id": "tenant-a"},
            )
            assert poll_response.status_code == 200, poll_response.text
            running_body = poll_response.json()
            if running_body["status"] == "running":
                break
            await asyncio.sleep(0.01)

        assert running_body is not None
        assert running_body["status"] == "running"
        assert running_body["result"] is None

        release_query.set()

        final_body = None
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            poll_response = await client.get(
                f"/sessions/{session_id}/query-operations/{operation_id}",
                headers={"X-Tenant-Id": "tenant-a"},
            )
            assert poll_response.status_code == 200, poll_response.text
            final_body = poll_response.json()
            if final_body["status"] == "completed":
                break
            await asyncio.sleep(0.01)

        assert final_body is not None
        assert final_body["status"] == "completed"
        assert final_body["result"]["result"] == "ASYNC:poll me"
        assert final_body["result"]["steps_used"] == 4
        assert final_body["result"]["server_used"] == "alpha"
        assert final_body["result"]["has_mcp_servers"] is True
        assert final_body["error"] is None


@pytest.mark.asyncio
async def test_query_operation_supports_agent_runtime_without_server_name_kwarg(monkeypatch):
    from httpx import ASGITransport, AsyncClient

    app, _mgr = _build_test_api(
        monkeypatch,
        agent_supports_server_name=False,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        session_id = await _create_session_async(
            client,
            tenant_id="tenant-a",
            mcp_servers={"everything": _server_config()},
        )

        create_response = await client.post(
            f"/sessions/{session_id}/query-operations",
            json={"query": "poll me", "server_name": "everything"},
            headers={"X-Tenant-Id": "tenant-a"},
        )
        assert create_response.status_code == 200, create_response.text
        operation_id = create_response.json()["operation_id"]

        final_body = await _wait_for_operation_status(
            client,
            session_id=session_id,
            operation_id=operation_id,
            tenant_id="tenant-a",
            expected_status="completed",
        )
        assert final_body["error"] is None
        assert final_body["result"]["result"] == "QUERY:poll me"
        assert final_body["result"]["server_used"] == "everything"


@pytest.mark.asyncio
async def test_run_query_uses_scoped_runtime_for_multi_server_target_when_agent_kwarg_is_unsupported(monkeypatch):
    from app.core.mcp_wrapper import MCPWrapper

    monkeypatch.setattr(MCPWrapper, "_import_dependencies", lambda self: None)

    wrapper = MCPWrapper(
        llm_provider="ollama",
        model="dummy",
        temperature=0,
        mcp_servers={"alpha": _server_config(), "beta": _server_config()},
    )
    wrapper._initialized = True

    class _AgentWithoutServerName:
        steps_used = 1
        last_server_used = None

        async def run(self, query: str, max_steps=None):
            raise AssertionError("Primary agent should not be used for targeted multi-server runs")

    class _ScopedAgent:
        def __init__(self, server_name: str):
            self.server_name = server_name
            self.steps_used = 4
            self.last_server_used = server_name
            self.calls = []

        async def run(self, query: str, max_steps=None):
            self.calls.append({"query": query, "max_steps": max_steps})
            return f"SCOPED:{self.server_name}:{query}:{max_steps}"

    wrapper._agent = _AgentWithoutServerName()
    scoped_agents = []

    @asynccontextmanager
    async def _stub_temporary_query_agent(self, *, server_name: str):
        agent = _ScopedAgent(server_name)
        scoped_agents.append(agent)
        yield agent

    monkeypatch.setattr(MCPWrapper, "_temporary_query_agent", _stub_temporary_query_agent)

    result = await wrapper.run_query("hello", max_steps=3, server_name="beta")

    assert result == "SCOPED:beta:hello:3"
    assert len(scoped_agents) == 1
    assert scoped_agents[0].calls == [{"query": "hello", "max_steps": 3}]
    assert wrapper.steps_used == 4
    assert wrapper.last_server_used == "beta"


@pytest.mark.asyncio
async def test_query_operation_failure_serializes_error(monkeypatch):
    from app.core.mcp_wrapper import MCPWrapper
    from httpx import ASGITransport, AsyncClient

    release_query = asyncio.Event()

    async def _run_query(self, query: str, max_steps=None, server_name=None):
        await release_query.wait()
        raise ValueError("bad query payload")

    monkeypatch.setattr(MCPWrapper, "run_query", _run_query)

    app, _mgr = _build_test_api(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        session_id = await _create_session_async(
            client,
            tenant_id="tenant-a",
            mcp_servers={"alpha": _server_config()},
        )

        create_response = await client.post(
            f"/sessions/{session_id}/query-operations",
            json={"query": "explode", "server_name": "alpha"},
            headers={"X-Tenant-Id": "tenant-a"},
        )
        assert create_response.status_code == 200, create_response.text
        operation_id = create_response.json()["operation_id"]

        running_body = None
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            poll_response = await client.get(
                f"/sessions/{session_id}/query-operations/{operation_id}",
                headers={"X-Tenant-Id": "tenant-a"},
            )
            assert poll_response.status_code == 200, poll_response.text
            running_body = poll_response.json()
            if running_body["status"] == "running":
                break
            await asyncio.sleep(0.01)

        assert running_body is not None
        assert running_body["status"] == "running"

        release_query.set()

        final_body = None
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            poll_response = await client.get(
                f"/sessions/{session_id}/query-operations/{operation_id}",
                headers={"X-Tenant-Id": "tenant-a"},
            )
            assert poll_response.status_code == 200, poll_response.text
            final_body = poll_response.json()
            if final_body["status"] == "failed":
                break
            await asyncio.sleep(0.01)

        assert final_body is not None
        assert final_body["status"] == "failed"
        assert final_body["result"] is None
        assert final_body["error"]["code"] == "MCP_SCHEMA_ERROR"
        assert final_body["error"]["message"] == "bad query payload"


@pytest.mark.asyncio
async def test_direct_tool_invocation_completed(monkeypatch):
    from httpx import ASGITransport, AsyncClient

    app, mgr = _build_test_api(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        session_id = await _create_session_async(
            client,
            tenant_id="tenant-a",
            mcp_servers={"everything": _server_config()},
        )

        create_response = await client.post(
            f"/sessions/{session_id}/query-operations",
            json={
                "server_name": "everything",
                "tool_name": "plain-tool",
                "arguments": {"topic": "bridges"},
            },
            headers={"X-Tenant-Id": "tenant-a"},
        )
        assert create_response.status_code == 200, create_response.text
        create_body = create_response.json()
        assert create_body["metadata"]["request"] == {
            "server_name": "everything",
            "tool_name": "plain-tool",
            "arguments": {"topic": "bridges"},
        }

        completed_body = await _wait_for_operation_status(
            client,
            session_id=session_id,
            operation_id=create_body["operation_id"],
            tenant_id="tenant-a",
            expected_status="completed",
        )
        assert completed_body["result"]["steps_used"] == 0
        assert completed_body["result"]["server_used"] == "everything"
        assert completed_body["result"]["result"] == {
            "tool": "plain-tool",
            "server": "everything",
            "arguments": {"topic": "bridges"},
            "status": "completed",
        }

        session_data = await mgr.get_session(session_id, tenant_id="tenant-a")
        session = session_data.wrapper._base_client.sessions["everything"]
        assert session.tool_calls == [
            {
                "name": "plain-tool",
                "arguments": {"topic": "bridges"},
            }
        ]
        assert session.task_calls == []


@pytest.mark.asyncio
async def test_task_required_direct_tool_invocation_completed(monkeypatch):
    from httpx import ASGITransport, AsyncClient

    app, mgr = _build_test_api(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        session_id = await _create_session_async(
            client,
            tenant_id="tenant-a",
            mcp_servers={"everything": _server_config()},
        )

        create_response = await client.post(
            f"/sessions/{session_id}/query-operations",
            json={
                "server_name": "everything",
                "tool_name": "simulate-research-query",
                "arguments": {"ambiguous": False, "topic": "bridges"},
            },
            headers={"X-Tenant-Id": "tenant-a"},
        )
        assert create_response.status_code == 200, create_response.text

        completed_body = await _wait_for_operation_status(
            client,
            session_id=session_id,
            operation_id=create_response.json()["operation_id"],
            tenant_id="tenant-a",
            expected_status="completed",
        )
        assert completed_body["result"]["result"] == {
            "tool": "simulate-research-query",
            "server": "everything",
            "arguments": {"ambiguous": False, "topic": "bridges"},
            "status": "completed",
        }

        session_data = await mgr.get_session(session_id, tenant_id="tenant-a")
        session = session_data.wrapper._base_client.sessions["everything"]
        assert session.tool_calls == []
        assert session.task_calls == [
            {
                "name": "simulate-research-query",
                "arguments": {"ambiguous": False, "topic": "bridges"},
                "task": {},
            }
        ]
        assert [call["method"] for call in session.protocol_calls] == ["tools/call", "tasks/result"]


@pytest.mark.asyncio
async def test_task_required_direct_tool_invocation_validates_arguments_before_task_creation(monkeypatch):
    from httpx import ASGITransport, AsyncClient

    app, mgr = _build_test_api(monkeypatch, task_creation_mode="real_invalid_task_creation_result")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        session_id = await _create_session_async(
            client,
            tenant_id="tenant-a",
            mcp_servers={"everything": _server_config()},
        )

        create_response = await client.post(
            f"/sessions/{session_id}/query-operations",
            json={
                "server_name": "everything",
                "tool_name": "simulate-research-query",
                "arguments": {"ambiguous": True},
            },
            headers={"X-Tenant-Id": "tenant-a"},
        )
        assert create_response.status_code == 200, create_response.text

        failed_body = await _wait_for_operation_status(
            client,
            session_id=session_id,
            operation_id=create_response.json()["operation_id"],
            tenant_id="tenant-a",
            expected_status="failed",
        )
        assert failed_body["error"]["code"] == "MCP_SCHEMA_ERROR"
        assert "topic" in failed_body["error"]["message"]

        session_data = await mgr.get_session(session_id, tenant_id="tenant-a")
        session = session_data.wrapper._base_client.sessions["everything"]
        assert session.protocol_calls == []
        assert session.task_calls == []
        assert session.tool_calls == []


@pytest.mark.asyncio
async def test_direct_tool_invocation_reaches_input_required(monkeypatch):
    from httpx import ASGITransport, AsyncClient

    app, _mgr = _build_test_api(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        session_id = await _create_session_async(
            client,
            tenant_id="tenant-a",
            mcp_servers={"everything": _server_config()},
        )

        create_response = await client.post(
            f"/sessions/{session_id}/query-operations",
            json={
                "server_name": "everything",
                "tool_name": "simulate-research-query",
                "arguments": {"ambiguous": True, "topic": "python"},
            },
            headers={"X-Tenant-Id": "tenant-a"},
        )
        assert create_response.status_code == 200, create_response.text
        operation_id = create_response.json()["operation_id"]

        input_required_body = await _wait_for_operation_status(
            client,
            session_id=session_id,
            operation_id=operation_id,
            tenant_id="tenant-a",
            expected_status="input-required",
        )
        assert input_required_body["requires_input"] is True
        assert input_required_body["pending_interaction"]["kind"] == "elicitation"
        assert input_required_body["pending_interaction"]["message"] == "Please disambiguate the research query"
        assert input_required_body["pending_interaction"]["requested_schema"] == {
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "region": {"type": "string"},
            },
            "required": ["topic"],
        }
        assert input_required_body["pending_interaction"]["details"]["task_id"].startswith("everything-task-")


@pytest.mark.asyncio
async def test_task_status_working_keeps_operation_running_without_pending_interaction(monkeypatch):
    from httpx import ASGITransport, AsyncClient

    app, _mgr = _build_test_api(monkeypatch, task_status_mode="pause_after_working")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        session_id = await _create_session_async(
            client,
            tenant_id="tenant-a",
            mcp_servers={"everything": _server_config()},
        )

        create_response = await client.post(
            f"/sessions/{session_id}/query-operations",
            json={
                "server_name": "everything",
                "tool_name": "simulate-research-query",
                "arguments": {"ambiguous": False, "topic": "python"},
            },
            headers={"X-Tenant-Id": "tenant-a"},
        )
        assert create_response.status_code == 200, create_response.text
        operation_id = create_response.json()["operation_id"]

        await asyncio.sleep(0.01)
        running_response = await client.get(
            f"/sessions/{session_id}/query-operations/{operation_id}",
            headers={"X-Tenant-Id": "tenant-a"},
        )
        assert running_response.status_code == 200, running_response.text
        running_body = running_response.json()
        assert running_body["status"] == "running"
        assert running_body["requires_input"] is False
        assert running_body["pending_interaction"] is None

        completed_body = await _wait_for_operation_status(
            client,
            session_id=session_id,
            operation_id=operation_id,
            tenant_id="tenant-a",
            expected_status="completed",
        )
        assert completed_body["result"]["result"]["status"] == "completed"


@pytest.mark.asyncio
async def test_task_status_input_required_creates_provisional_pending_interaction(monkeypatch):
    from httpx import ASGITransport, AsyncClient

    app, _mgr = _build_test_api(monkeypatch, task_status_mode="pause_on_input_required")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        session_id = await _create_session_async(
            client,
            tenant_id="tenant-a",
            mcp_servers={"everything": _server_config()},
        )

        create_response = await client.post(
            f"/sessions/{session_id}/query-operations",
            json={
                "server_name": "everything",
                "tool_name": "simulate-research-query",
                "arguments": {"ambiguous": True, "topic": "python"},
            },
            headers={"X-Tenant-Id": "tenant-a"},
        )
        assert create_response.status_code == 200, create_response.text
        operation_id = create_response.json()["operation_id"]

        input_required_body = await _wait_for_operation_status(
            client,
            session_id=session_id,
            operation_id=operation_id,
            tenant_id="tenant-a",
            expected_status="input-required",
        )
        assert input_required_body["requires_input"] is True
        assert input_required_body["pending_interaction"]["message"] == "Task requires additional input"
        assert input_required_body["pending_interaction"]["requested_schema"] is None
        assert input_required_body["pending_interaction"]["details"]["source"] == "task-status-notification"
        assert input_required_body["pending_interaction"]["details"]["provisional"] is True
        assert input_required_body["pending_interaction"]["details"]["task_id"].startswith("everything-task-")
        assert input_required_body["pending_interaction"]["interaction_id"].startswith("task-status:")


@pytest.mark.asyncio
async def test_task_status_input_required_resume_before_elicitation_still_completes(monkeypatch):
    from httpx import ASGITransport, AsyncClient

    app, _mgr = _build_test_api(monkeypatch, task_status_mode="pause_on_input_required")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        session_id = await _create_session_async(
            client,
            tenant_id="tenant-a",
            mcp_servers={"everything": _server_config()},
        )

        create_response = await client.post(
            f"/sessions/{session_id}/query-operations",
            json={
                "server_name": "everything",
                "tool_name": "simulate-research-query",
                "arguments": {"ambiguous": True, "topic": "python"},
            },
            headers={"X-Tenant-Id": "tenant-a"},
        )
        assert create_response.status_code == 200, create_response.text
        operation_id = create_response.json()["operation_id"]

        input_required_body = await _wait_for_operation_status(
            client,
            session_id=session_id,
            operation_id=operation_id,
            tenant_id="tenant-a",
            expected_status="input-required",
        )
        interaction_id = input_required_body["pending_interaction"]["interaction_id"]
        assert input_required_body["pending_interaction"]["details"]["provisional"] is True

        resume_response = await client.post(
            f"/sessions/{session_id}/query-operations/{operation_id}/resume",
            json={
                "action": "accept",
                "interaction_id": interaction_id,
                "content": {"topic": "climate", "region": "eu"},
            },
            headers={"X-Tenant-Id": "tenant-a"},
        )
        assert resume_response.status_code == 200, resume_response.text
        assert resume_response.json()["status"] == "running"

        completed_body = await _wait_for_operation_status(
            client,
            session_id=session_id,
            operation_id=operation_id,
            tenant_id="tenant-a",
            expected_status="completed",
        )
        assert completed_body["result"]["result"] == {
            "tool": "simulate-research-query",
            "server": "everything",
            "arguments": {"ambiguous": True, "topic": "python"},
            "resolved_with": {"topic": "climate", "region": "eu"},
        }


@pytest.mark.asyncio
async def test_direct_tool_invocation_resume_accept_completes(monkeypatch):
    from httpx import ASGITransport, AsyncClient

    app, mgr = _build_test_api(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        session_id = await _create_session_async(
            client,
            tenant_id="tenant-a",
            mcp_servers={"everything": _server_config()},
        )

        create_response = await client.post(
            f"/sessions/{session_id}/query-operations",
            json={
                "server_name": "everything",
                "tool_name": "simulate-research-query",
                "arguments": {"ambiguous": True, "topic": "python"},
            },
            headers={"X-Tenant-Id": "tenant-a"},
        )
        operation_id = create_response.json()["operation_id"]

        input_required_body = await _wait_for_operation_status(
            client,
            session_id=session_id,
            operation_id=operation_id,
            tenant_id="tenant-a",
            expected_status="input-required",
        )
        interaction_id = input_required_body["pending_interaction"]["interaction_id"]
        assert input_required_body["pending_interaction"]["details"]["task_id"].startswith("everything-task-")
        assert input_required_body["pending_interaction"]["details"]["provisional"] is False

        resume_response = await client.post(
            f"/sessions/{session_id}/query-operations/{operation_id}/resume",
            json={
                "action": "accept",
                "interaction_id": interaction_id,
                "content": {"topic": "climate", "region": "eu"},
            },
            headers={"X-Tenant-Id": "tenant-a"},
        )
        assert resume_response.status_code == 200, resume_response.text

        completed_body = await _wait_for_operation_status(
            client,
            session_id=session_id,
            operation_id=operation_id,
            tenant_id="tenant-a",
            expected_status="completed",
        )
        assert completed_body["requires_input"] is False
        assert completed_body["pending_interaction"] is None
        assert completed_body["result"]["result"] == {
            "tool": "simulate-research-query",
            "server": "everything",
            "arguments": {"ambiguous": True, "topic": "python"},
            "resolved_with": {"topic": "climate", "region": "eu"},
        }

        session_data = await mgr.get_session(session_id, tenant_id="tenant-a")
        session = session_data.wrapper._base_client.sessions["everything"]
        assert session.tool_calls == []
        assert [call["method"] for call in session.protocol_calls] == ["tools/call", "tasks/result"]


@pytest.mark.asyncio
async def test_direct_tool_invocation_resume_cancel_cancels(monkeypatch):
    from httpx import ASGITransport, AsyncClient

    app, _mgr = _build_test_api(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        session_id = await _create_session_async(
            client,
            tenant_id="tenant-a",
            mcp_servers={"everything": _server_config()},
        )

        create_response = await client.post(
            f"/sessions/{session_id}/query-operations",
            json={
                "server_name": "everything",
                "tool_name": "simulate-research-query",
                "arguments": {"ambiguous": True, "topic": "python"},
            },
            headers={"X-Tenant-Id": "tenant-a"},
        )
        operation_id = create_response.json()["operation_id"]

        input_required_body = await _wait_for_operation_status(
            client,
            session_id=session_id,
            operation_id=operation_id,
            tenant_id="tenant-a",
            expected_status="input-required",
        )

        cancel_response = await client.post(
            f"/sessions/{session_id}/query-operations/{operation_id}/resume",
            json={
                "action": "cancel",
                "interaction_id": input_required_body["pending_interaction"]["interaction_id"],
            },
            headers={"X-Tenant-Id": "tenant-a"},
        )
        assert cancel_response.status_code == 200, cancel_response.text
        assert cancel_response.json()["status"] == "cancelled"

        cancelled_body = await _wait_for_operation_status(
            client,
            session_id=session_id,
            operation_id=operation_id,
            tenant_id="tenant-a",
            expected_status="cancelled",
        )
        assert cancelled_body["error"]["code"] == "MCP_QUERY_OPERATION_CANCELLED"


@pytest.mark.asyncio
async def test_direct_tool_invocation_resume_decline_fails(monkeypatch):
    from httpx import ASGITransport, AsyncClient

    app, _mgr = _build_test_api(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        session_id = await _create_session_async(
            client,
            tenant_id="tenant-a",
            mcp_servers={"everything": _server_config()},
        )

        create_response = await client.post(
            f"/sessions/{session_id}/query-operations",
            json={
                "server_name": "everything",
                "tool_name": "simulate-research-query",
                "arguments": {"ambiguous": True, "topic": "python"},
            },
            headers={"X-Tenant-Id": "tenant-a"},
        )
        operation_id = create_response.json()["operation_id"]

        input_required_body = await _wait_for_operation_status(
            client,
            session_id=session_id,
            operation_id=operation_id,
            tenant_id="tenant-a",
            expected_status="input-required",
        )

        decline_response = await client.post(
            f"/sessions/{session_id}/query-operations/{operation_id}/resume",
            json={
                "action": "decline",
                "interaction_id": input_required_body["pending_interaction"]["interaction_id"],
            },
            headers={"X-Tenant-Id": "tenant-a"},
        )
        assert decline_response.status_code == 200, decline_response.text

        failed_body = await _wait_for_operation_status(
            client,
            session_id=session_id,
            operation_id=operation_id,
            tenant_id="tenant-a",
            expected_status="failed",
        )
        assert failed_body["error"]["code"] == "MCP_ELICITATION_DECLINED"


def test_direct_tool_invocation_routes_enforce_tenant_isolation(monkeypatch):
    client, _mgr = _build_test_app(monkeypatch)
    session_id = _create_session(
        client,
        tenant_id="tenant-a",
        mcp_servers={"everything": _server_config()},
    )

    create_response = client.post(
        f"/sessions/{session_id}/query-operations",
        json={
            "server_name": "everything",
            "tool_name": "simulate-research-query",
            "arguments": {"ambiguous": False, "topic": "bridges"},
        },
        headers={"X-Tenant-Id": "tenant-a"},
    )
    assert create_response.status_code == 200, create_response.text
    operation_id = create_response.json()["operation_id"]

    wrong_tenant_get = client.get(
        f"/sessions/{session_id}/query-operations/{operation_id}",
        headers={"X-Tenant-Id": "tenant-b"},
    )
    assert wrong_tenant_get.status_code == 404
    assert wrong_tenant_get.json()["detail"]["code"] == "MCP_SESSION_NOT_FOUND"

    wrong_tenant_resume = client.post(
        f"/sessions/{session_id}/query-operations/{operation_id}/resume",
        json={"action": "cancel"},
        headers={"X-Tenant-Id": "tenant-b"},
    )
    assert wrong_tenant_resume.status_code == 404
    assert wrong_tenant_resume.json()["detail"]["code"] == "MCP_SESSION_NOT_FOUND"


def test_query_operation_query_payload_still_uses_legacy_request_shape(monkeypatch):
    from app.core.mcp_wrapper import MCPWrapper

    async def _run_query(self, query: str, max_steps=None, server_name=None):
        self._steps_used = 2
        self._last_server_used = server_name or "alpha"
        return f"QUERY:{query}"

    monkeypatch.setattr(MCPWrapper, "run_query", _run_query)

    client, _mgr = _build_test_app(monkeypatch)
    session_id = _create_session(
        client,
        tenant_id="tenant-a",
        mcp_servers={"alpha": _server_config()},
    )

    response = client.post(
        f"/sessions/{session_id}/query-operations",
        json={"query": "still query", "server_name": "alpha"},
        headers={"X-Tenant-Id": "tenant-a"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["metadata"]["request"] == {
        "query": "still query",
        "max_steps": None,
        "server_name": "alpha",
    }


def test_sync_query_route_still_works_after_direct_tool_support(monkeypatch):
    client, _mgr = _build_test_app(monkeypatch)
    session_id = _create_session(
        client,
        tenant_id="tenant-a",
        mcp_servers={"everything": _server_config()},
    )

    response = client.post(
        f"/sessions/{session_id}/query",
        json={"query": "legacy path", "server_name": "everything"},
        headers={"X-Tenant-Id": "tenant-a"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result"] == "QUERY:legacy path"
    assert body["server_used"] == "everything"


@pytest.mark.asyncio
async def test_query_operation_elicitation_reaches_input_required_and_resumes_to_completed(monkeypatch):
    from app.core.mcp_wrapper import MCPWrapper
    from httpx import ASGITransport, AsyncClient

    requested_schema = {
        "type": "object",
        "properties": {
            "item_name": {"type": "string"},
            "quantity": {"type": "integer"},
        },
        "required": ["item_name", "quantity"],
    }

    async def _run_query(self, query: str, max_steps=None, server_name=None):
        self._steps_used = 1
        self._active_server_name = server_name or "alpha"
        resume = await self._handle_runtime_elicitation(
            SimpleNamespace(request_id="req-1"),
            SimpleNamespace(
                message="Please provide purchase details",
                requestedSchema=requested_schema,
            ),
        )
        self._steps_used = 5
        self._last_server_used = server_name or "alpha"
        return f"ASYNC:{query}:{resume.content['item_name']}:{resume.content['quantity']}"

    monkeypatch.setattr(MCPWrapper, "run_query", _run_query)

    app, _mgr = _build_test_api(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        session_id = await _create_session_async(
            client,
            tenant_id="tenant-a",
            mcp_servers={"alpha": _server_config()},
        )

        create_response = await client.post(
            f"/sessions/{session_id}/query-operations",
            json={"query": "buy apples", "server_name": "alpha"},
            headers={"X-Tenant-Id": "tenant-a"},
        )
        assert create_response.status_code == 200, create_response.text
        operation_id = create_response.json()["operation_id"]

        input_required_body = await _wait_for_operation_status(
            client,
            session_id=session_id,
            operation_id=operation_id,
            tenant_id="tenant-a",
            expected_status="input-required",
        )
        assert input_required_body["requires_input"] is True
        assert input_required_body["pending_interaction"]["kind"] == "elicitation"
        assert input_required_body["pending_interaction"]["message"] == "Please provide purchase details"
        assert input_required_body["pending_interaction"]["requested_schema"] == requested_schema
        assert input_required_body["pending_interaction"]["actions"] == ["accept", "decline", "cancel"]

        interaction_id = input_required_body["pending_interaction"]["interaction_id"]
        resume_response = await client.post(
            f"/sessions/{session_id}/query-operations/{operation_id}/resume",
            json={
                "action": "accept",
                "interaction_id": interaction_id,
                "content": {"item_name": "apples", "quantity": 3},
            },
            headers={"X-Tenant-Id": "tenant-a"},
        )
        assert resume_response.status_code == 200, resume_response.text
        assert resume_response.json()["status"] in {"running", "completed"}

        completed_body = await _wait_for_operation_status(
            client,
            session_id=session_id,
            operation_id=operation_id,
            tenant_id="tenant-a",
            expected_status="completed",
        )
        assert completed_body["requires_input"] is False
        assert completed_body["pending_interaction"] is None
        assert completed_body["result"]["result"] == "ASYNC:buy apples:apples:3"


@pytest.mark.asyncio
async def test_query_operation_elicitation_decline_fails_operation(monkeypatch):
    from app.core.mcp_wrapper import MCPWrapper
    from httpx import ASGITransport, AsyncClient

    async def _run_query(self, query: str, max_steps=None, server_name=None):
        self._active_server_name = server_name or "alpha"
        await self._handle_runtime_elicitation(
            SimpleNamespace(request_id="req-2"),
            SimpleNamespace(message="Need approval", requestedSchema=None),
        )
        return f"ASYNC:{query}"

    monkeypatch.setattr(MCPWrapper, "run_query", _run_query)

    app, _mgr = _build_test_api(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        session_id = await _create_session_async(
            client,
            tenant_id="tenant-a",
            mcp_servers={"alpha": _server_config()},
        )

        create_response = await client.post(
            f"/sessions/{session_id}/query-operations",
            json={"query": "needs approval", "server_name": "alpha"},
            headers={"X-Tenant-Id": "tenant-a"},
        )
        assert create_response.status_code == 200, create_response.text
        operation_id = create_response.json()["operation_id"]

        input_required_body = await _wait_for_operation_status(
            client,
            session_id=session_id,
            operation_id=operation_id,
            tenant_id="tenant-a",
            expected_status="input-required",
        )

        decline_response = await client.post(
            f"/sessions/{session_id}/query-operations/{operation_id}/resume",
            json={
                "action": "decline",
                "interaction_id": input_required_body["pending_interaction"]["interaction_id"],
            },
            headers={"X-Tenant-Id": "tenant-a"},
        )
        assert decline_response.status_code == 200, decline_response.text

        failed_body = await _wait_for_operation_status(
            client,
            session_id=session_id,
            operation_id=operation_id,
            tenant_id="tenant-a",
            expected_status="failed",
        )
        assert failed_body["error"]["code"] == "MCP_ELICITATION_DECLINED"
        assert failed_body["requires_input"] is False
        assert failed_body["pending_interaction"] is None


@pytest.mark.asyncio
async def test_query_operation_elicitation_cancel_cancels_operation(monkeypatch):
    from app.core.mcp_wrapper import MCPWrapper
    from httpx import ASGITransport, AsyncClient

    async def _run_query(self, query: str, max_steps=None, server_name=None):
        self._active_server_name = server_name or "alpha"
        await self._handle_runtime_elicitation(
            SimpleNamespace(request_id="req-3"),
            SimpleNamespace(message="Need confirmation", requestedSchema=None),
        )
        return f"ASYNC:{query}"

    monkeypatch.setattr(MCPWrapper, "run_query", _run_query)

    app, _mgr = _build_test_api(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        session_id = await _create_session_async(
            client,
            tenant_id="tenant-a",
            mcp_servers={"alpha": _server_config()},
        )

        create_response = await client.post(
            f"/sessions/{session_id}/query-operations",
            json={"query": "cancel me", "server_name": "alpha"},
            headers={"X-Tenant-Id": "tenant-a"},
        )
        assert create_response.status_code == 200, create_response.text
        operation_id = create_response.json()["operation_id"]

        input_required_body = await _wait_for_operation_status(
            client,
            session_id=session_id,
            operation_id=operation_id,
            tenant_id="tenant-a",
            expected_status="input-required",
        )

        cancel_response = await client.post(
            f"/sessions/{session_id}/query-operations/{operation_id}/resume",
            json={
                "action": "cancel",
                "interaction_id": input_required_body["pending_interaction"]["interaction_id"],
            },
            headers={"X-Tenant-Id": "tenant-a"},
        )
        assert cancel_response.status_code == 200, cancel_response.text
        assert cancel_response.json()["status"] == "cancelled"
        assert cancel_response.json()["error"]["code"] == "MCP_QUERY_OPERATION_CANCELLED"


def test_query_operation_routes_enforce_tenant_isolation(monkeypatch):
    from app.core.mcp_wrapper import MCPWrapper

    async def _run_query(self, query: str, max_steps=None, server_name=None):
        await asyncio.sleep(0.02)
        self._steps_used = 1
        self._last_server_used = server_name
        return f"ASYNC:{query}"

    monkeypatch.setattr(MCPWrapper, "run_query", _run_query)

    client, _mgr = _build_test_app(monkeypatch)
    session_id = _create_session(
        client,
        tenant_id="tenant-a",
        mcp_servers={"alpha": _server_config()},
    )

    create_response = client.post(
        f"/sessions/{session_id}/query-operations",
        json={"query": "owned", "server_name": "alpha"},
        headers={"X-Tenant-Id": "tenant-a"},
    )
    assert create_response.status_code == 200, create_response.text
    operation_id = create_response.json()["operation_id"]

    wrong_tenant_create = client.post(
        f"/sessions/{session_id}/query-operations",
        json={"query": "forbidden", "server_name": "alpha"},
        headers={"X-Tenant-Id": "tenant-b"},
    )
    assert wrong_tenant_create.status_code == 404
    assert wrong_tenant_create.json()["detail"]["code"] == "MCP_SESSION_NOT_FOUND"

    wrong_tenant_poll = client.get(
        f"/sessions/{session_id}/query-operations/{operation_id}",
        headers={"X-Tenant-Id": "tenant-b"},
    )
    assert wrong_tenant_poll.status_code == 404
    assert wrong_tenant_poll.json()["detail"]["code"] == "MCP_SESSION_NOT_FOUND"


@pytest.mark.asyncio
async def test_query_operation_resume_route_enforces_tenant_isolation(monkeypatch):
    from app.core.mcp_wrapper import MCPWrapper
    from httpx import ASGITransport, AsyncClient

    async def _run_query(self, query: str, max_steps=None, server_name=None):
        self._active_server_name = server_name or "alpha"
        await self._handle_runtime_elicitation(
            SimpleNamespace(request_id="req-4"),
            SimpleNamespace(message="Need tenant-owned input", requestedSchema=None),
        )
        return f"ASYNC:{query}"

    monkeypatch.setattr(MCPWrapper, "run_query", _run_query)

    app, _mgr = _build_test_api(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        session_id = await _create_session_async(
            client,
            tenant_id="tenant-a",
            mcp_servers={"alpha": _server_config()},
        )

        create_response = await client.post(
            f"/sessions/{session_id}/query-operations",
            json={"query": "owned elicitation", "server_name": "alpha"},
            headers={"X-Tenant-Id": "tenant-a"},
        )
        assert create_response.status_code == 200, create_response.text
        operation_id = create_response.json()["operation_id"]

        input_required_body = await _wait_for_operation_status(
            client,
            session_id=session_id,
            operation_id=operation_id,
            tenant_id="tenant-a",
            expected_status="input-required",
        )

        wrong_tenant_resume = await client.post(
            f"/sessions/{session_id}/query-operations/{operation_id}/resume",
            json={
                "action": "accept",
                "interaction_id": input_required_body["pending_interaction"]["interaction_id"],
                "content": {"value": "nope"},
            },
            headers={"X-Tenant-Id": "tenant-b"},
        )
        assert wrong_tenant_resume.status_code == 404
        assert wrong_tenant_resume.json()["detail"]["code"] == "MCP_SESSION_NOT_FOUND"


@pytest.mark.asyncio
async def test_query_operation_resume_rejects_invalid_payload_and_expired_elicitation(monkeypatch):
    from app.core.mcp_wrapper import MCPWrapper
    from httpx import ASGITransport, AsyncClient

    async def _run_query(self, query: str, max_steps=None, server_name=None):
        self._active_server_name = server_name or "alpha"
        resume = await self._handle_runtime_elicitation(
            SimpleNamespace(request_id="req-5"),
            SimpleNamespace(message="Need form data", requestedSchema={"type": "object"}),
        )
        self._last_server_used = server_name or "alpha"
        return f"ASYNC:{query}:{resume.content['value']}"

    monkeypatch.setattr(MCPWrapper, "run_query", _run_query)

    app, _mgr = _build_test_api(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        session_id = await _create_session_async(
            client,
            tenant_id="tenant-a",
            mcp_servers={"alpha": _server_config()},
        )

        create_response = await client.post(
            f"/sessions/{session_id}/query-operations",
            json={"query": "validate me", "server_name": "alpha"},
            headers={"X-Tenant-Id": "tenant-a"},
        )
        assert create_response.status_code == 200, create_response.text
        operation_id = create_response.json()["operation_id"]

        input_required_body = await _wait_for_operation_status(
            client,
            session_id=session_id,
            operation_id=operation_id,
            tenant_id="tenant-a",
            expected_status="input-required",
        )
        interaction_id = input_required_body["pending_interaction"]["interaction_id"]

        invalid_resume = await client.post(
            f"/sessions/{session_id}/query-operations/{operation_id}/resume",
            json={"action": "accept", "interaction_id": interaction_id},
            headers={"X-Tenant-Id": "tenant-a"},
        )
        assert invalid_resume.status_code == 400
        assert invalid_resume.json()["detail"]["code"] == "MCP_QUERY_OPERATION_RESUME_INVALID"

        valid_resume = await client.post(
            f"/sessions/{session_id}/query-operations/{operation_id}/resume",
            json={
                "action": "accept",
                "interaction_id": interaction_id,
                "content": {"value": "ok"},
            },
            headers={"X-Tenant-Id": "tenant-a"},
        )
        assert valid_resume.status_code == 200, valid_resume.text

        await _wait_for_operation_status(
            client,
            session_id=session_id,
            operation_id=operation_id,
            tenant_id="tenant-a",
            expected_status="completed",
        )

        expired_resume = await client.post(
            f"/sessions/{session_id}/query-operations/{operation_id}/resume",
            json={
                "action": "accept",
                "interaction_id": interaction_id,
                "content": {"value": "stale"},
            },
            headers={"X-Tenant-Id": "tenant-a"},
        )
        assert expired_resume.status_code == 409
        assert expired_resume.json()["detail"]["code"] == "MCP_QUERY_OPERATION_ELICITATION_EXPIRED"


def test_capability_fallback_supports_keyword_signatures_without_typeerror_probing(monkeypatch):
    client, _mgr = _build_test_app(
        monkeypatch,
        get_session_style="keyword_only",
        prompt_signature="keyword_name",
    )
    session_id = _create_session(
        client,
        tenant_id="tenant-a",
        mcp_servers={"alpha": _server_config()},
    )

    response = client.post(
        f"/sessions/{session_id}/prompts/alpha-welcome/render",
        json={"arguments": {"topic": "hardening"}},
        headers={"X-Tenant-Id": "tenant-a"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["messages"][0]["content"]["text"] == "alpha:alpha-welcome:hardening"


def test_capability_not_supported_returns_precise_501(monkeypatch):
    client, _mgr = _build_test_app(monkeypatch, capability_mode="missing_list_prompts")
    session_id = _create_session(
        client,
        tenant_id="tenant-a",
        mcp_servers={"alpha": _server_config()},
    )

    response = client.get(
        f"/sessions/{session_id}/prompts",
        headers={"X-Tenant-Id": "tenant-a"},
    )
    assert response.status_code == 501
    detail = response.json()["detail"]
    assert detail["code"] == "MCP_CAPABILITY_NOT_SUPPORTED"
    assert detail["operation"] == "list_prompts"
    assert detail["capability"] == "list_prompts"


def test_internal_typeerror_from_runtime_is_not_treated_as_signature_mismatch(monkeypatch):
    client, _mgr = _build_test_app(monkeypatch)
    session_id = _create_session(
        client,
        tenant_id="tenant-a",
        mcp_servers={"alpha": _server_config()},
    )

    response = client.post(
        f"/sessions/{session_id}/prompts/alpha-welcome/render",
        json={"arguments": {"explode_internal_typeerror": True}},
        headers={"X-Tenant-Id": "tenant-a"},
    )
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["code"] == "MCP_UPSTREAM_ERROR"
    assert detail["operation"] == "render_prompt"
