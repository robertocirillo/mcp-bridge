import pytest

from app.core.sessions.manager import SessionManager
from app.models.config import SessionConfig


class DummyWrapper:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.bias_settings_kwargs = None

    def set_context(self, *, tenant_id=None, run_id=None, session_id=None):
        return None

    def set_guardrails_enabled(self, enabled: bool):
        return None

    def set_pii_input_mode(self, mode):
        return None

    def set_pii_mode(self, mode):
        return None

    def set_bias_settings(self, **kwargs):
        self.bias_settings_kwargs = kwargs

    async def initialize(self):
        return None

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_session_manager_forwards_bias_unsafe_labels(monkeypatch):
    import app.core.sessions.manager as sm

    monkeypatch.setattr(sm, "MCPWrapper", DummyWrapper)

    mgr = SessionManager()
    cfg = SessionConfig.model_validate(
        {
            "llm_provider": {"provider": "ollama", "model": "llama3.1", "temperature": 0},
            "mcp_servers": {},
            "guardrails": {
                "bias": {
                    "mode": "block",
                    "base_url": "http://bias-detector-service:9090",
                    "unsafe_labels": ["HATE"],
                    "threshold": 0.8,
                    "top_k": 5,
                }
            },
        }
    )

    session_id = await mgr.create_session(cfg, tenant_id="t1", run_id="r1")
    sd = await mgr.get_session(session_id, tenant_id="t1")
    assert sd.wrapper.bias_settings_kwargs is not None
    assert sd.wrapper.bias_settings_kwargs.get("unsafe_labels") == ["HATE"]
