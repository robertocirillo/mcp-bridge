import pytest


def _build_test_app(monkeypatch):
    """Build a minimal FastAPI app with real route wiring.

    This is a *contract* test for mcp-bridge, not mcp-use:
    we assert that session-scoped `disallowed_tools` produces the correct
    structured HTTP 403 (`detail.code = MCP_TOOL_NOT_ALLOWED`) when a tool
    call is attempted.

    Constraints:
    - Exercise real HTTP routes: POST /sessions + POST /queries/{id}/query
    - No real MCP servers
    - Monkeypatch at wrapper/client level to simulate a tool call
    """

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.dependencies import get_session_manager
    from app.core.session_manager import SessionManager

    # Fresh in-memory session manager for each test
    mgr = SessionManager()
    monkeypatch.setattr("app.api.dependencies._session_manager", mgr, raising=False)

    # Patch MCPWrapper internals to avoid importing heavy deps and to avoid
    # contacting any real MCP servers.
    from app.core.mcp_wrapper import MCPWrapper, _GuardedMCPClient

    monkeypatch.setattr(MCPWrapper, "_import_dependencies", lambda self: None)

    async def _stub_initialize(self):
        if getattr(self, "_initialized", False):
            return

        class _BaseClientStub:
            async def call_tool(self, name: str, *args, **kwargs):
                # Should not be reached when the tool is disallowed,
                # but return something deterministic anyway.
                return {"ok": True}

            async def close_all_sessions(self):
                return None

        base_client = _BaseClientStub()
        self._client = _GuardedMCPClient(base_client, self)

        class _AgentStub:
            def __init__(self, client):
                self.client = client
                self.steps_used = 1
                self.last_server_used = None

            async def run(self, **kwargs):
                # Deterministically attempt exactly one tool call.
                # The tool name is expected to be blocked by disallowed_tools.
                await self.client.call_tool("fake_tool")
                return "SHOULD_NOT_HAPPEN"

        self._agent = _AgentStub(self._client)
        self._initialized = True

    monkeypatch.setattr(MCPWrapper, "initialize", _stub_initialize)

    # Build the FastAPI app with the real routers
    from app.api.routes.sessions import router as sessions_router
    from app.api.routes.queries import router as queries_router

    app = FastAPI()
    app.include_router(sessions_router, prefix="/sessions")
    app.include_router(queries_router, prefix="/queries")
    app.dependency_overrides[get_session_manager] = lambda: mgr

    return TestClient(app), mgr


def _create_session(client, disallowed_tools):
    payload = {
        "llm_provider": {"provider": "ollama", "model": "dummy", "temperature": 0},
        "mcp_servers": {},
        "disallowed_tools": disallowed_tools,
    }
    r = client.post("/sessions", json=payload)
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def _execute_query(client, session_id: str, query: str):
    return client.post(f"/queries/{session_id}/query", json={"query": query})


def test_http_contract_disallowed_tools_returns_structured_403(monkeypatch):
    client, _mgr = _build_test_app(monkeypatch)

    session_id = _create_session(client, ["fake_tool"])
    r = _execute_query(client, session_id, "please call the tool")

    assert r.status_code == 403
    detail = r.json()["detail"]

    assert detail["code"] == "MCP_TOOL_NOT_ALLOWED"
    assert detail["operation"] == "execute_query"
    assert detail["session_id"] == session_id
    assert detail.get("tool_name") == "fake_tool"
