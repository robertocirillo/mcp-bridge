import pytest

from app.core.mcp_wrapper import (
    MCPToolNotAllowedError,
    GuardrailViolationError,
    _GuardedMCPSession,
    GuardrailContext,
    MCPWrapper,
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
