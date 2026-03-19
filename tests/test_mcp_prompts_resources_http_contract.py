import asyncio
import base64
import time

import pytest


def _build_test_api(
    monkeypatch,
    *,
    get_session_style: str = "positional",
    prompt_signature: str = "standard",
    capability_mode: str = "normal",
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

    class _SessionStub:
        def __init__(self, server_name: str):
            self.server_name = server_name
            self.prompt_calls = []
            self.resource_calls = []
            self.capability_mode = capability_mode

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

    class _BaseClientStub:
        def __init__(self, server_names):
            self.sessions = {
                server_name: _SessionStub(server_name)
                for server_name in server_names
            }

        def __getattribute__(self, item):
            if item == "get_session":
                if get_session_style == "positional":
                    return object.__getattribute__(self, "get_session_positional")
                if get_session_style == "keyword_only":
                    return object.__getattribute__(self, "get_session_keyword_only")
                if get_session_style == "name_kw":
                    return object.__getattribute__(self, "get_session_name_kw")
                raise AssertionError(f"Unsupported get_session_style: {get_session_style}")
            return object.__getattribute__(self, item)

        async def get_session_positional(self, server_name: str):
            return self.sessions[server_name]

        async def get_session_keyword_only(self, *, server_name: str):
            return self.sessions[server_name]

        async def get_session_name_kw(self, *, name: str):
            server_name = name
            return self.sessions[server_name]

        async def close_all_sessions(self):
            return None

    class _AgentStub:
        def __init__(self, wrapper):
            self.wrapper = wrapper
            self.steps_used = 0
            self.last_server_used = None

        async def run(self, query: str, max_steps=None, server_name=None):
            self.steps_used = 2
            if server_name is not None:
                self.last_server_used = server_name
            elif len(self.wrapper.mcp_servers) == 1:
                self.last_server_used = next(iter(self.wrapper.mcp_servers))
            else:
                self.last_server_used = None
            return f"QUERY:{query}"

    async def _stub_initialize(self):
        if getattr(self, "_initialized", False):
            return

        base_client = _BaseClientStub(self.mcp_servers.keys())
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
):
    from fastapi.testclient import TestClient

    app, mgr = _build_test_api(
        monkeypatch,
        get_session_style=get_session_style,
        prompt_signature=prompt_signature,
        capability_mode=capability_mode,
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
