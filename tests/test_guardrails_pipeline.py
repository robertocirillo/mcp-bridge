import pytest

from app.core.mcp_wrapper import (
    MCPToolNotAllowedError,
    GuardrailViolationError,
    _GuardedMCPSession,
    GuardrailContext,
    MCPWrapper,
    make_pii_after_model_guardrail,
    make_pii_before_model_guardrail,
)


@pytest.mark.asyncio
async def test_pii_before_model_block_mode_raises_structured_guardrail_violation():
    gr = make_pii_before_model_guardrail(mode="block")
    ctx = GuardrailContext(tenant_id="t1", run_id="r1", session_id="s1", query="q")

    with pytest.raises(GuardrailViolationError) as exc:
        await gr(GuardrailContext(**{**ctx.__dict__, "query": "Email me at test@example.com"}))

    err = exc.value
    assert err.code == "PII_DETECTED"
    assert err.phase == "before_model"
    assert err.rule == "pii"
    assert "email" in err.details.get("types", [])


@pytest.mark.asyncio
async def test_pii_before_model_redact_rewrites_query_with_placeholders():
    gr = make_pii_before_model_guardrail(mode="redact")
    raw = "Contact: john.doe@example.com IBAN IT60X0542811101000000123456"
    ctx = GuardrailContext(tenant_id="t1", run_id="r1", session_id="s1", query=raw)

    ctx2 = await gr(ctx)
    assert ctx2.query is not None
    assert "john.doe@example.com" not in ctx2.query
    assert "IT60X0542811101000000123456" not in ctx2.query
    assert "[MCP_BRIDGE_REDACTED_EMAIL]" in ctx2.query
    assert "[MCP_BRIDGE_REDACTED_IBAN]" in ctx2.query


def _make_wrapper(disallowed=None):
    # Bypass MCPWrapper.__init__ to avoid importing runtime deps (mcp-use / providers)
    w = object.__new__(MCPWrapper)
    w.disallowed_tools = disallowed
    w.tenant_id = "t1"
    w.run_id = "r1"
    w.session_id = "s1"
    w.before_model_guardrails = []
    w.after_model_guardrails = []
    return w


def test_disallowed_tools_exact_match_blocks():
    w = _make_wrapper(["filesystem.read_file"])
    with pytest.raises(MCPToolNotAllowedError):
        w._enforce_tool_allowed("filesystem.read_file")


def test_disallowed_tools_wildcard_blocks():
    w = _make_wrapper(["filesystem.*"])
    with pytest.raises(MCPToolNotAllowedError):
        w._enforce_tool_allowed("filesystem.read_file")


def test_disallowed_tools_allows_other_tools():
    w = _make_wrapper(["filesystem.*"])
    w._enforce_tool_allowed("math.add")


@pytest.mark.asyncio
async def test_blocked_tool_is_never_invoked():
    called = {"value": False}

    class DummySession:
        async def call_tool(self, name, *args, **kwargs):
            called["value"] = True
            return {"ok": True}

    w = _make_wrapper(["secret.*"])
    guarded = _GuardedMCPSession(DummySession(), w)

    with pytest.raises(MCPToolNotAllowedError):
        await guarded.call_tool("secret.delete_all")

    assert called["value"] is False


@pytest.mark.asyncio
async def test_before_after_guardrail_pipeline_runs_in_order_and_supports_async():
    w = _make_wrapper([])

    seen = []

    async def g1(ctx: GuardrailContext):
        seen.append("g1")
        return GuardrailContext(**{**ctx.__dict__, "query": (ctx.query or "") + "A"})

    def g2(ctx: GuardrailContext):
        seen.append("g2")
        return GuardrailContext(**{**ctx.__dict__, "query": (ctx.query or "") + "B"})

    async def a1(ctx: GuardrailContext, out: str):
        seen.append("a1")
        return out + "X"

    def a2(ctx: GuardrailContext, out: str):
        seen.append("a2")
        return out + "Y"

    w.before_model_guardrails = [g1, g2]
    w.after_model_guardrails = [a1, a2]

    ctx = GuardrailContext(tenant_id="t1", run_id="r1", session_id="s1", query="Q")
    ctx2 = await w._run_before_model_guardrails(ctx)
    assert ctx2.query == "QAB"

    out = await w._run_after_model_guardrails(ctx2, "R")
    assert out == "RXY"
    assert seen == ["g1", "g2", "a1", "a2"]


@pytest.mark.asyncio
async def test_guardrail_can_block_by_raising():
    w = _make_wrapper([])

    def blocker(ctx: GuardrailContext):
        raise GuardrailViolationError(message="blocked", phase="before_model", rule="test")

    w.before_model_guardrails = [blocker]

    ctx = GuardrailContext(query="hello")
    with pytest.raises(GuardrailViolationError):
        await w._run_before_model_guardrails(ctx)


@pytest.mark.asyncio
async def test_pii_after_model_redacts_email_phone_iban_by_default():
    gr = make_pii_after_model_guardrail(mode="redact")
    ctx = GuardrailContext(tenant_id="t1", run_id="r1", session_id="s1", query="q")

    raw = (
        "Contact: john.doe@example.com, phone +39 333 1234567, "
        "IBAN IT60X0542811101000000123456"
    )
    out = await gr(ctx, raw)

    assert "john.doe@example.com" not in out
    assert "+39 333 1234567" not in out
    assert "IT60X0542811101000000123456" not in out

    assert "[MCP_BRIDGE_REDACTED_EMAIL]" in out
    assert "[MCP_BRIDGE_REDACTED_PHONE]" in out
    assert "[MCP_BRIDGE_REDACTED_IBAN]" in out


@pytest.mark.asyncio
async def test_pii_after_model_block_mode_raises_structured_guardrail_violation():
    gr = make_pii_after_model_guardrail(mode="block")
    ctx = GuardrailContext(tenant_id="t1", run_id="r1", session_id="s1", query="q")

    with pytest.raises(GuardrailViolationError) as exc:
        await gr(ctx, "Email me at test@example.com")

    err = exc.value
    assert err.code == "PII_DETECTED"
    assert err.phase == "after_model"
    assert err.rule == "pii"
    assert "email" in err.details.get("types", [])


@pytest.mark.asyncio
async def test_session_manager_applies_pii_input_and_output_modes(monkeypatch):
    """SessionManager should pass guardrails.pii.{input_mode,mode} to the wrapper.

    We monkeypatch MCPWrapper to avoid importing runtime deps (mcp-use/providers).
    """

    from app.models.config import SessionConfig, LLMProvider, GuardrailsSettings, PiiSettings
    import app.core.session_manager as sm

    class DummySettings:
        MAX_ACTIVE_SESSIONS = 10
        SESSION_TIMEOUT = 3600

    class DummyWrapper:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs
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
        llm_provider=LLMProvider(provider="ollama", model="mistral"),
        mcp_servers={},
        guardrails=GuardrailsSettings(
            pii=PiiSettings(input_mode="block", mode="redact")
        ),
    )

    session_id = await manager.create_session(cfg, tenant_id="t1", run_id="r1")
    session = await manager.get_session(session_id, tenant_id="t1")

    assert session.wrapper.pii_mode_set == "redact"
    assert session.wrapper.pii_input_mode_set == "block"
