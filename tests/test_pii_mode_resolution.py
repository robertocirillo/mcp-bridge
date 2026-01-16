import pytest


@pytest.mark.asyncio
async def test_pii_mode_shared_default_inherits_to_input_when_input_mode_omitted(monkeypatch):
    """If the user provides only `pii.mode`, input should inherit it (strategy 3)."""

    import app.core.session_manager as sm
    from app.models.config import GuardrailsSettings, LLMProvider, PiiSettings, SessionConfig

    class DummySettings:
        MAX_ACTIVE_SESSIONS = 10
        SESSION_TIMEOUT = 3600

    class DummyWrapper:
        def __init__(self, *args, **kwargs):
            self.pii_mode_set = None
            self.pii_input_mode_set = None

        def set_context(self, *, tenant_id=None, run_id=None, session_id=None):
            self.tenant_id = tenant_id
            self.run_id = run_id
            self.session_id = session_id

        def set_pii_mode(self, mode):
            self.pii_mode_set = mode

        def set_pii_input_mode(self, mode):
            self.pii_input_mode_set = mode

        async def initialize(self):
            return None

        async def close(self):
            return None

    monkeypatch.setattr(sm, "settings", DummySettings)
    monkeypatch.setattr(sm, "MCPWrapper", DummyWrapper)

    manager = sm.SessionManager()

    # User specifies only `mode=redact` (legacy behavior) and expects it to apply as the default.
    cfg = SessionConfig(
        llm_provider=LLMProvider(provider="ollama", model="llama3.1latest", temperature=0),
        mcp_servers={},
        guardrails=GuardrailsSettings(pii=PiiSettings(mode="redact")),
    )

    session_id = await manager.create_session(cfg, tenant_id="default", run_id="r1")
    session = await manager.get_session(session_id, tenant_id="default")

    # Output inherits shared mode.
    assert session.wrapper.pii_mode_set == "redact"
    # Input inherits shared mode (so it won't unexpectedly default to 'block').
    assert session.wrapper.pii_input_mode_set == "redact"


@pytest.mark.asyncio
async def test_pii_output_mode_override_does_not_change_input_default(monkeypatch):
    """output_mode overrides only output; input still uses shared mode unless input_mode is provided."""

    import app.core.session_manager as sm
    from app.models.config import GuardrailsSettings, LLMProvider, PiiSettings, SessionConfig

    class DummySettings:
        MAX_ACTIVE_SESSIONS = 10
        SESSION_TIMEOUT = 3600

    class DummyWrapper:
        def __init__(self, *args, **kwargs):
            self.pii_mode_set = None
            self.pii_input_mode_set = None

        def set_context(self, *, tenant_id=None, run_id=None, session_id=None):
            self.tenant_id = tenant_id
            self.run_id = run_id
            self.session_id = session_id

        def set_pii_mode(self, mode):
            self.pii_mode_set = mode

        def set_pii_input_mode(self, mode):
            self.pii_input_mode_set = mode

        async def initialize(self):
            return None

        async def close(self):
            return None

    monkeypatch.setattr(sm, "settings", DummySettings)
    monkeypatch.setattr(sm, "MCPWrapper", DummyWrapper)

    manager = sm.SessionManager()

    # Shared mode is block, but output is explicitly overridden to redact.
    cfg = SessionConfig(
        llm_provider=LLMProvider(provider="ollama", model="llama3.1latest", temperature=0),
        mcp_servers={},
        guardrails=GuardrailsSettings(pii=PiiSettings(mode="block", output_mode="redact")),
    )

    session_id = await manager.create_session(cfg, tenant_id="default", run_id="r1")
    session = await manager.get_session(session_id, tenant_id="default")

    # Output uses the explicit override.
    assert session.wrapper.pii_mode_set == "redact"
    # Input still inherits the shared mode (block) because input_mode was not provided.
    assert session.wrapper.pii_input_mode_set == "block"


@pytest.mark.asyncio
async def test_pii_input_mode_override_wins_over_shared_mode(monkeypatch):
    """input_mode overrides input even when a shared mode is set."""

    import app.core.session_manager as sm
    from app.models.config import GuardrailsSettings, LLMProvider, PiiSettings, SessionConfig

    class DummySettings:
        MAX_ACTIVE_SESSIONS = 10
        SESSION_TIMEOUT = 3600

    class DummyWrapper:
        def __init__(self, *args, **kwargs):
            self.pii_mode_set = None
            self.pii_input_mode_set = None

        def set_context(self, *, tenant_id=None, run_id=None, session_id=None):
            self.tenant_id = tenant_id
            self.run_id = run_id
            self.session_id = session_id

        def set_pii_mode(self, mode):
            self.pii_mode_set = mode

        def set_pii_input_mode(self, mode):
            self.pii_input_mode_set = mode

        async def initialize(self):
            return None

        async def close(self):
            return None

    monkeypatch.setattr(sm, "settings", DummySettings)
    monkeypatch.setattr(sm, "MCPWrapper", DummyWrapper)

    manager = sm.SessionManager()

    cfg = SessionConfig(
        llm_provider=LLMProvider(provider="ollama", model="llama3.1latest", temperature=0),
        mcp_servers={},
        guardrails=GuardrailsSettings(pii=PiiSettings(mode="redact", input_mode="off")),
    )

    session_id = await manager.create_session(cfg, tenant_id="default", run_id="r1")
    session = await manager.get_session(session_id, tenant_id="default")

    # Output inherits shared mode.
    assert session.wrapper.pii_mode_set == "redact"
    # Input uses explicit override.
    assert session.wrapper.pii_input_mode_set == "off"
