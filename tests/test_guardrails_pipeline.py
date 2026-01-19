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


def _make_wrapper(disallowed=None):
    # Bypass MCPWrapper.__init__ to avoid importing runtime deps (mcp-use / providers)
    w = object.__new__(MCPWrapper)
    w.disallowed_tools = disallowed
    w.tenant_id = "t1"
    w.run_id = "r1"
    w.session_id = "s1"
    w.before_model_guardrails = []
    w.after_model_guardrails = []
    w.guardrails_enabled = True
    w.pii_mode = "redact"
    w._pii_after_model_guardrail = None
    w._pii_before_model_guardrail = None
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
async def test_guardrails_global_disable_skips_pipelines():
    w = _make_wrapper([])

    called = {"before": 0, "after": 0}

    def b(ctx: GuardrailContext):
        called["before"] += 1
        return GuardrailContext(**{**ctx.__dict__, "query": "MODIFIED"})

    def a(ctx: GuardrailContext, out: str):
        called["after"] += 1
        return "MODIFIED"

    w.before_model_guardrails = [b]
    w.after_model_guardrails = [a]
    w.guardrails_enabled = False

    ctx = GuardrailContext(query="hello")
    ctx2 = await w._run_before_model_guardrails(ctx)
    assert ctx2.query == "hello"
    out = await w._run_after_model_guardrails(ctx2, "world")
    assert out == "world"
    assert called == {"before": 0, "after": 0}


@pytest.mark.asyncio
async def test_guardrail_can_block_by_raising():
    w = _make_wrapper([])

    def blocker(ctx: GuardrailContext):
        raise GuardrailViolationError(message="blocked", phase="before_model", rule="test")

    w.before_model_guardrails = [blocker]

    ctx = GuardrailContext(query="hello")
    with pytest.raises(GuardrailViolationError):
        await w._run_before_model_guardrails(ctx)


def test_pii_before_model_redact_rewrites_query():
    gr = make_pii_before_model_guardrail(mode="redact")
    ctx = GuardrailContext(
        tenant_id="t1",
        run_id="r1",
        session_id="s1",
        query="Email john.doe@example.com IBAN IT60X0542811101000000123456",
    )

    out_ctx = gr(ctx)

    assert "john.doe@example.com" not in (out_ctx.query or "")
    assert "IT60X0542811101000000123456" not in (out_ctx.query or "")
    assert "[MCP_BRIDGE_REDACTED_EMAIL]" in (out_ctx.query or "")
    assert "[MCP_BRIDGE_REDACTED_IBAN]" in (out_ctx.query or "")


def test_pii_before_model_block_raises_structured_guardrail_violation():
    gr = make_pii_before_model_guardrail(mode="block")
    ctx = GuardrailContext(
        tenant_id="t1",
        run_id="r1",
        session_id="s1",
        query="Email me at test@example.com",
    )

    with pytest.raises(GuardrailViolationError) as exc:
        gr(ctx)

    err = exc.value
    assert err.code == "PII_DETECTED"
    assert err.phase == "before_model"
    assert err.rule == "pii"
    assert "email" in err.details.get("types", [])


@pytest.mark.asyncio
async def test_pii_after_model_redacts_email_phone_iban():
    gr = make_pii_after_model_guardrail(mode="redact")
    ctx = GuardrailContext(tenant_id="t1", run_id="r1", session_id="s1", query="q")

    raw = (
        "Contact: john.doe@example.com, phone +39 333 1234567, "
        "IBAN IT60X0542811101000000123456"
    )
    out = await gr(ctx, raw)

    assert "john.doe@example.com" not in out
    assert "333 1234567" not in out
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
async def test_tool_result_pii_is_redacted_when_output_mode_is_redact():
    w = _make_wrapper([])
    w.guardrails_enabled = True
    w.pii_mode = "redact"

    class DummySession:
        async def call_tool(self, name, *args, **kwargs):
            return {
                "text": "email a@b.com ; iban IT60X0542811101000000123456",
                "nested": ["a@b.com"],
            }

    guarded = _GuardedMCPSession(DummySession(), w)
    out = await guarded.call_tool("filesystem.read_file")

    assert out["text"] == "email [MCP_BRIDGE_REDACTED_EMAIL] ; iban [MCP_BRIDGE_REDACTED_IBAN]"
    assert out["nested"] == ["[MCP_BRIDGE_REDACTED_EMAIL]"]


@pytest.mark.asyncio
async def test_tool_result_pii_is_not_redacted_when_guardrails_disabled():
    w = _make_wrapper([])
    w.guardrails_enabled = False
    w.pii_mode = "redact"

    class DummySession:
        async def call_tool(self, name, *args, **kwargs):
            return "email a@b.com"

    guarded = _GuardedMCPSession(DummySession(), w)
    out = await guarded.call_tool("filesystem.read_file")

    assert out == "email a@b.com"



