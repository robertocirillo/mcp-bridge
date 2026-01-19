import pytest


EMAIL = "a@b.com"
IBAN = "IT60X0542811101000000123456"


def _build_test_app(monkeypatch):
    """Build a FastAPI app using the REAL sessions/query routers.

    Constraints from the sprint:
    - Exercise the real HTTP routes: POST /sessions and POST /sessions/{id}/query.
    - No real MCP servers.
    - Monkeypatch at wrapper/client level to simulate a tool call result containing PII.

    Notes:
    - We keep the real `MCPWrapper._wrap_tool_result()` implementation.
    - We bypass real dependency imports (mcp-use/langchain) and network calls.
    - We bypass after_model output guardrails for these tests so that any redaction
      observed in the response is attributable to tool-result redaction.
    """

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.dependencies import get_session_manager
    from app.core.session_manager import SessionManager

    # Fresh in-memory session manager for each test.
    mgr = SessionManager()
    monkeypatch.setattr("app.api.dependencies._session_manager", mgr, raising=False)

    # Patch MCPWrapper internals to avoid importing heavy deps.
    from app.core.mcp_wrapper import MCPWrapper, _GuardedMCPClient

    monkeypatch.setattr(MCPWrapper, "_import_dependencies", lambda self: None)

    async def _noop_initialize(self):
        # Build a guarded client around a deterministic stub.
        if getattr(self, "_initialized", False):
            return

        class _BaseClientStub:
            async def call_tool(self, name: str, *args, **kwargs):
                # Return a nested object containing PII.
                return {
                    "email": EMAIL,
                    "iban": IBAN,
                    "nested": {
                        "message": f"contact {EMAIL} and use {IBAN}",
                        "list": [EMAIL, {"iban": IBAN}],
                    },
                }

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
                # Simulate a single tool invocation.
                tool_out = await self.client.call_tool("fake_tool")
                return f"TOOL_OUTPUT: {tool_out}"

        self._agent = _AgentStub(self._client)
        self._initialized = True

    monkeypatch.setattr(MCPWrapper, "initialize", _noop_initialize)

    async def _identity_after(self, ctx, output):
        return output

    monkeypatch.setattr(MCPWrapper, "_run_after_model_guardrails", _identity_after)

    # Build FastAPI app with real routers and real paths.
    from app.api.routes.sessions import router as sessions_router
    from app.api.routes.queries import router as queries_router

    app = FastAPI()
    app.include_router(sessions_router, prefix="/sessions")
    app.include_router(queries_router, prefix="/sessions")
    app.dependency_overrides[get_session_manager] = lambda: mgr

    return TestClient(app), mgr


def _create_session(client, guardrails: dict):
    payload = {
        "llm_provider": {"provider": "ollama", "model": "dummy", "temperature": 0},
        "mcp_servers": {},
        "guardrails": guardrails,
    }
    r = client.post("/sessions", json=payload)
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def _execute_query(client, session_id: str, query: str):
    return client.post(f"/sessions/{session_id}/query", json={"query": query})


def test_http_contract_tool_result_redaction_when_pii_mode_redact(monkeypatch):
    client, _mgr = _build_test_app(monkeypatch)

    session_id = _create_session(client, {"pii": {"mode": "redact"}})
    r = _execute_query(client, session_id, "please call the tool")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["session_id"] == session_id

    # Tool results should be redacted.
    assert EMAIL not in body["result"]
    assert IBAN not in body["result"]
    assert "[MCP_BRIDGE_REDACTED_EMAIL]" in body["result"]
    assert "[MCP_BRIDGE_REDACTED_IBAN]" in body["result"]


def test_http_contract_tool_result_no_redaction_when_guardrails_global_off(monkeypatch):
    client, _mgr = _build_test_app(monkeypatch)

    session_id = _create_session(
        client,
        {"enabled": False, "pii": {"mode": "redact"}},
    )
    r = _execute_query(client, session_id, "please call the tool")

    assert r.status_code == 200, r.text
    body = r.json()

    # Global off => no tool-result redaction.
    assert EMAIL in body["result"]
    assert IBAN in body["result"]
    assert "[MCP_BRIDGE_REDACTED_EMAIL]" not in body["result"]
    assert "[MCP_BRIDGE_REDACTED_IBAN]" not in body["result"]


def test_http_contract_tool_result_no_redaction_when_pii_output_mode_off(monkeypatch):
    client, _mgr = _build_test_app(monkeypatch)

    session_id = _create_session(
        client,
        {"pii": {"mode": "redact", "output_mode": "off"}},
    )
    r = _execute_query(client, session_id, "please call the tool")

    assert r.status_code == 200, r.text
    body = r.json()

    # output_mode=off => no output redaction and no tool-result redaction.
    assert EMAIL in body["result"]
    assert IBAN in body["result"]
    assert "[MCP_BRIDGE_REDACTED_EMAIL]" not in body["result"]
    assert "[MCP_BRIDGE_REDACTED_IBAN]" not in body["result"]



def test_http_contract_tool_result_block_when_pii_output_mode_block(monkeypatch):
    client, _mgr = _build_test_app(monkeypatch)

    # Strategy 3: shared default is redact, but output_mode overrides to block.
    session_id = _create_session(
        client,
        {"pii": {"mode": "redact", "output_mode": "block"}},
    )
    r = _execute_query(client, session_id, "please call the tool")

    assert r.status_code == 403
    detail = r.json()["detail"]

    assert detail["code"] == "PII_DETECTED"
    assert detail["operation"] == "execute_query"
    assert detail["session_id"] == session_id

    # New behavior (P1): block on tool results when output_mode resolves to block.
    assert detail.get("phase") == "tool_result"
    assert detail.get("rule") == "pii"
    assert detail.get("tool_name") == "fake_tool"

    # Helpful diagnostics for clients.
    details = detail.get("details") or {}
    assert "email" in (details.get("types") or [])
    assert "iban" in (details.get("types") or [])
