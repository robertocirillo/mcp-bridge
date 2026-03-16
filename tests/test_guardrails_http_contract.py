import pytest


def _build_test_app(monkeypatch):
    """Build a minimal FastAPI app with real route wiring.

    We keep HTTP shapes real (FastAPI dependency injection + route handlers),
    but we monkeypatch the MCPWrapper implementation used by SessionManager so
    no real LLM / MCP calls are performed.
    """

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.dependencies import get_session_manager
    from app.core.session_manager import SessionManager

    # Fresh in-memory session manager for each test
    mgr = SessionManager()
    monkeypatch.setattr("app.api.dependencies._session_manager", mgr, raising=False)

    # --- Dummy wrapper (guardrails + deterministic output) ---
    from app.core.mcp_wrapper import (
        GuardrailContext,
        GuardrailViolationError,
        make_bias_after_model_guardrail,
        make_pii_after_model_guardrail,
        make_pii_before_model_guardrail,
    )

    class DummyMCPWrapper:
        def __init__(
            self,
            llm_provider: str,
            model: str,
            api_key=None,
            base_url=None,
            temperature: float = 0.0,
            max_tokens=None,
            mcp_servers=None,
            max_steps: int = 30,
            verbose: bool = False,
            sandbox: bool = False,
            sandbox_options=None,
            disallowed_tools=None,
            use_server_manager: bool = False,
        ):
            self.llm_provider = llm_provider
            self.model = model
            self.mcp_servers = mcp_servers or {}
            self.has_mcp_servers = bool(self.mcp_servers)
            self.max_steps = max_steps
            self.verbose = verbose
            self.disallowed_tools = disallowed_tools

            # request context
            self.tenant_id = None
            self.run_id = None
            self.session_id = None

            # guardrails
            self.before_model_guardrails = []
            self.after_model_guardrails = []
            self.guardrails_enabled = True
            self._pii_before_model_guardrail = None
            self._pii_after_model_guardrail = None
            self._bias_after_model_guardrail = None

            # observability for tests
            self.last_processed_query = None
            self.steps_used = 1
            self.last_server_used = None

        def set_context(self, *, tenant_id=None, run_id=None, session_id=None):
            self.tenant_id = tenant_id
            self.run_id = run_id
            self.session_id = session_id

        async def initialize(self):
            return None

        async def close(self):
            return None

        def set_guardrails_enabled(self, enabled: bool):
            self.guardrails_enabled = bool(enabled)

        def set_pii_input_mode(self, mode):
            normalized = (mode or "block").strip().lower()
            if normalized not in {"off", "redact", "block"}:
                normalized = "block"
            # uninstall
            if normalized == "off":
                if self._pii_before_model_guardrail is not None:
                    self.before_model_guardrails = [
                        gr for gr in self.before_model_guardrails if gr is not self._pii_before_model_guardrail
                    ]
                self._pii_before_model_guardrail = None
                return
            new_gr = make_pii_before_model_guardrail(mode=normalized)
            if self._pii_before_model_guardrail is not None:
                self.before_model_guardrails = [
                    new_gr if gr is self._pii_before_model_guardrail else gr
                    for gr in self.before_model_guardrails
                ]
            else:
                self.before_model_guardrails.append(new_gr)
            self._pii_before_model_guardrail = new_gr

        def set_pii_mode(self, mode):
            normalized = (mode or "redact").strip().lower()
            if normalized not in {"off", "redact", "block"}:
                normalized = "redact"
            # uninstall
            if normalized == "off":
                if self._pii_after_model_guardrail is not None:
                    self.after_model_guardrails = [
                        gr for gr in self.after_model_guardrails if gr is not self._pii_after_model_guardrail
                    ]
                self._pii_after_model_guardrail = None
                return
            new_gr = make_pii_after_model_guardrail(mode=normalized)
            if self._pii_after_model_guardrail is not None:
                self.after_model_guardrails = [
                    new_gr if gr is self._pii_after_model_guardrail else gr
                    for gr in self.after_model_guardrails
                ]
            else:
                self.after_model_guardrails.append(new_gr)
            self._pii_after_model_guardrail = new_gr

        def set_bias_mode(self, mode):
            normalized = (mode or "off").strip().lower()
            if normalized not in {"off", "block"}:
                normalized = "off"
            # uninstall
            if normalized == "off":
                if self._bias_after_model_guardrail is not None:
                    self.after_model_guardrails = [
                        gr for gr in self.after_model_guardrails if gr is not self._bias_after_model_guardrail
                    ]
                self._bias_after_model_guardrail = None
                return
            new_gr = make_bias_after_model_guardrail(mode=normalized)
            if self._bias_after_model_guardrail is not None:
                self.after_model_guardrails = [
                    new_gr if gr is self._bias_after_model_guardrail else gr
                    for gr in self.after_model_guardrails
                ]
            else:
                self.after_model_guardrails.append(new_gr)
            self._bias_after_model_guardrail = new_gr

        async def _run_before(self, ctx: GuardrailContext) -> GuardrailContext:
            if self.guardrails_enabled is False:
                return ctx
            for gr in self.before_model_guardrails:
                ctx = gr(ctx)
            return ctx

        async def _run_after(self, ctx: GuardrailContext, out: str) -> str:
            if self.guardrails_enabled is False:
                return out
            for gr in self.after_model_guardrails:
                out = await gr(ctx, out)
            return out

        async def run_query(self, query: str, max_steps=None, server_name=None) -> str:
            ctx = GuardrailContext(
                tenant_id=self.tenant_id,
                run_id=self.run_id,
                session_id=self.session_id,
                query=query,
                server_name=server_name,
            )

            try:
                ctx = await self._run_before(ctx)
            except GuardrailViolationError:
                raise

            processed = ctx.query or ""
            self.last_processed_query = processed

            if not processed.strip():
                raise ValueError("Empty query not allowed")

            # Deterministic "model output" that includes some PII so output redaction can be tested.
            # It also echoes the processed query so the bias detector can be triggered deterministically.
            output = (
                f"PROCESSED_QUERY: {processed}\n"
                "MODEL_OUTPUT: email test@example.com ; iban IT60X0542811101000000123456"
            )
            return await self._run_after(ctx, output)

    # Patch SessionManager to use DummyMCPWrapper instead of the real one.
    monkeypatch.setattr("app.core.session_manager.MCPWrapper", DummyMCPWrapper)

    # Build the FastAPI app with the real routers
    from app.api.routes.sessions import router as sessions_router
    from app.api.routes.queries import router as queries_router

    app = FastAPI()
    app.include_router(sessions_router, prefix="/sessions")
    app.include_router(queries_router, prefix="/queries")
    app.dependency_overrides[get_session_manager] = lambda: mgr

    return TestClient(app), mgr


def _create_session(client, guardrails: dict | None):
    payload = {
        "llm_provider": {"provider": "ollama", "model": "dummy", "temperature": 0},
        "mcp_servers": {},
    }
    if guardrails is not None:
        payload["guardrails"] = guardrails
    r = client.post("/sessions", json=payload)
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def _execute_query(client, session_id: str, query: str):
    return client.post(f"/queries/{session_id}/query", json={"query": query})


def test_http_contract_pii_redact_allows_and_redacts_input(monkeypatch):
    client, _mgr = _build_test_app(monkeypatch)

    session_id = _create_session(client, {"pii": {"mode": "redact"}})
    q = "email john.doe@example.com ; iban IT60X0542811101000000123456"
    r = _execute_query(client, session_id, q)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["session_id"] == session_id

    # Input was redacted (Strategy 3: mode applies to input)
    assert "john.doe@example.com" not in body["result"]
    assert "IT60X0542811101000000123456" not in body["result"]
    assert "[MCP_BRIDGE_REDACTED_EMAIL]" in body["result"]
    assert "[MCP_BRIDGE_REDACTED_IBAN]" in body["result"]

    # Output redaction is also active (default when mode=redact)
    assert "test@example.com" not in body["result"]


def test_http_contract_pii_input_block_returns_structured_403(monkeypatch):
    client, _mgr = _build_test_app(monkeypatch)

    session_id = _create_session(
        client,
        {"pii": {"input_mode": "block", "mode": "redact"}},
    )
    q = "email john.doe@example.com ; iban IT60X0542811101000000123456"
    r = _execute_query(client, session_id, q)

    assert r.status_code == 403
    detail = r.json()["detail"]
    assert detail["code"] == "PII_DETECTED"
    assert detail["phase"] == "before_model"
    assert detail["rule"] == "pii"
    assert detail["operation"] == "execute_query"
    assert detail["session_id"] == session_id


def test_http_contract_guardrails_global_off_bypasses_everything(monkeypatch):
    client, _mgr = _build_test_app(monkeypatch)

    session_id = _create_session(
        client,
        {"enabled": False, "pii": {"mode": "block"}},
    )
    q = "email john.doe@example.com ; iban IT60X0542811101000000123456"
    r = _execute_query(client, session_id, q)

    assert r.status_code == 200, r.text
    body = r.json()

    # Global off: no redaction and no blocking.
    assert "john.doe@example.com" in body["result"]
    assert "IT60X0542811101000000123456" in body["result"]
    assert "[MCP_BRIDGE_REDACTED_EMAIL]" not in body["result"]
    assert "[MCP_BRIDGE_REDACTED_IBAN]" not in body["result"]


def test_http_contract_pii_input_mode_off_skips_before_model(monkeypatch):
    client, mgr = _build_test_app(monkeypatch)

    session_id = _create_session(
        client,
        {"pii": {"input_mode": "off", "mode": "redact"}},
    )

    q = "email john.doe@example.com ; iban IT60X0542811101000000123456"
    r = _execute_query(client, session_id, q)
    assert r.status_code == 200, r.text

    # The response may be redacted by after_model, so assert directly on runtime state.
    wrapper = mgr._sessions[session_id].wrapper  # type: ignore[attr-defined]
    assert getattr(wrapper, "last_processed_query", None) == q


def test_http_contract_pii_output_mode_off_skips_after_model(monkeypatch):
    client, _mgr = _build_test_app(monkeypatch)

    session_id = _create_session(
        client,
        {"pii": {"mode": "redact", "output_mode": "off"}},
    )

    r = _execute_query(client, session_id, "hello")
    assert r.status_code == 200, r.text
    body = r.json()

    # After-model PII redaction is OFF => tool/model output retains raw PII.
    assert "test@example.com" in body["result"]
    assert "IT60X0542811101000000123456" in body["result"]


def test_http_contract_bias_output_block_returns_structured_403(monkeypatch):
    client, _mgr = _build_test_app(monkeypatch)

    class AlwaysDetect:
        def detect(self, text: str):
            from app.core.mcp_wrapper import BiasDetectionResult

            if "BIAS_TEST" in text:
                return BiasDetectionResult(detected=True, categories=["test"], findings=["synthetic"])
            return BiasDetectionResult(detected=False)

    from app.core.mcp_wrapper import get_bias_detector, set_bias_detector

    previous = get_bias_detector()
    set_bias_detector(AlwaysDetect())

    try:
        session_id = _create_session(
            client,
            {
                "enabled": True,
                "bias": {"mode": "off", "output_mode": "block", "base_url": None},
            },
        )

        r = _execute_query(client, session_id, "BIAS_TEST")

        assert r.status_code == 403
        detail = r.json()["detail"]
        assert detail["code"] == "BIAS_DETECTED"
        assert detail["operation"] == "execute_query"
        assert detail["session_id"] == session_id
        assert detail["phase"] == "after_model"
        assert detail["rule"] == "bias"
        assert detail.get("guardrail") == "bias"
    finally:
        set_bias_detector(previous)


def test_http_contract_bias_global_off_bypasses_detector(monkeypatch):
    client, _mgr = _build_test_app(monkeypatch)

    class AlwaysDetect:
        def detect(self, text: str):
            from app.core.mcp_wrapper import BiasDetectionResult

            return BiasDetectionResult(detected=True, categories=["test"], findings=["synthetic"])

    from app.core.mcp_wrapper import get_bias_detector, set_bias_detector

    previous = get_bias_detector()
    set_bias_detector(AlwaysDetect())

    try:
        session_id = _create_session(
            client,
            {
                "enabled": False,
                "bias": {"output_mode": "block", "base_url": None},
            },
        )

        r = _execute_query(client, session_id, "BIAS_TEST")
        assert r.status_code == 200, r.text
    finally:
        set_bias_detector(previous)


def test_http_contract_bias_off_does_not_block(monkeypatch):
    client, _mgr = _build_test_app(monkeypatch)

    class AlwaysDetect:
        def detect(self, text: str):
            from app.core.mcp_wrapper import BiasDetectionResult

            return BiasDetectionResult(detected=True, categories=["test"], findings=["synthetic"])

    from app.core.mcp_wrapper import get_bias_detector, set_bias_detector

    previous = get_bias_detector()
    set_bias_detector(AlwaysDetect())

    try:
        session_id = _create_session(
            client,
            {
                "enabled": True,
                "bias": {"mode": "off", "base_url": None},
            },
        )

        r = _execute_query(client, session_id, "BIAS_TEST")
        assert r.status_code == 200, r.text
    finally:
        set_bias_detector(previous)
