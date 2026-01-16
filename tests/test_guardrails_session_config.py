import pytest

from app.core.session_manager import SessionManager
from app.models.config import SessionConfig


class DummyWrapper:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.guardrails_enabled = True
        self.pii_input_mode = None
        self.pii_mode = None
        self.context = {}

    def set_context(self, *, tenant_id=None, run_id=None, session_id=None):
        self.context = {"tenant_id": tenant_id, "run_id": run_id, "session_id": session_id}

    def set_guardrails_enabled(self, enabled: bool):
        self.guardrails_enabled = bool(enabled)

    def set_pii_input_mode(self, mode):
        self.pii_input_mode = mode

    def set_pii_mode(self, mode):
        self.pii_mode = mode

    async def initialize(self):
        return None

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_session_manager_applies_global_guardrails_disable(monkeypatch):
    import app.core.session_manager as sm

    monkeypatch.setattr(sm, "MCPWrapper", DummyWrapper)

    mgr = SessionManager()
    cfg = SessionConfig.model_validate(
        {
            "llm_provider": {"provider": "ollama", "model": "llama3.1", "temperature": 0},
            "mcp_servers": {},
            "guardrails": {"enabled": False},
        }
    )

    session_id = await mgr.create_session(cfg, tenant_id="t1", run_id="r1")
    sd = await mgr.get_session(session_id, tenant_id="t1")
    assert sd.wrapper.guardrails_enabled is False

    # PII modes should not be forced when global is disabled
    assert sd.wrapper.pii_input_mode is None
    assert sd.wrapper.pii_mode is None


@pytest.mark.asyncio
async def test_session_manager_strategy3_mode_applies_to_input_and_output(monkeypatch):
    import app.core.session_manager as sm

    monkeypatch.setattr(sm, "MCPWrapper", DummyWrapper)

    mgr = SessionManager()
    cfg = SessionConfig.model_validate(
        {
            "llm_provider": {"provider": "ollama", "model": "llama3.1", "temperature": 0},
            "mcp_servers": {},
            "guardrails": {"pii": {"mode": "redact"}},
        }
    )

    session_id = await mgr.create_session(cfg, tenant_id="t1", run_id="r1")
    sd = await mgr.get_session(session_id, tenant_id="t1")
    assert sd.wrapper.guardrails_enabled is True
    assert sd.wrapper.pii_input_mode == "redact"
    assert sd.wrapper.pii_mode == "redact"


@pytest.mark.asyncio
async def test_session_manager_strategy3_overrides_input_and_output(monkeypatch):
    import app.core.session_manager as sm

    monkeypatch.setattr(sm, "MCPWrapper", DummyWrapper)

    mgr = SessionManager()
    cfg = SessionConfig.model_validate(
        {
            "llm_provider": {"provider": "ollama", "model": "llama3.1", "temperature": 0},
            "mcp_servers": {},
            "guardrails": {
                "pii": {
                    "mode": "redact",
                    "input_mode": "block",
                    "output_mode": "off",
                }
            },
        }
    )

    session_id = await mgr.create_session(cfg, tenant_id="t1", run_id="r1")
    sd = await mgr.get_session(session_id, tenant_id="t1")
    assert sd.wrapper.guardrails_enabled is True
    assert sd.wrapper.pii_input_mode == "block"
    assert sd.wrapper.pii_mode == "off"
