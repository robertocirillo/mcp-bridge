import pytest

from app.core.mcp_policy_engine import ToolPolicy, ToolPolicyEngine, ToolInvocationContext
from app.core.mcp_wrapper import MCPToolNotAllowedError, MCPWrapper


def _make_wrapper():
    w = object.__new__(MCPWrapper)
    w.disallowed_tools = None
    w.tenant_id = "t1"
    w.run_id = "r1"
    w.session_id = "s1"
    w.before_model_guardrails = []
    w.after_model_guardrails = []
    w.guardrails_enabled = True
    w.pii_mode = "redact"
    return w


def test_tool_policy_engine_allowlist_default_denies_unlisted_tool():
    engine = ToolPolicyEngine(allow_patterns=["math.*"])
    decision = engine.evaluate(ToolInvocationContext(tool_name="filesystem.read_file"))
    assert decision.allowed is False
    assert decision.reason == "not present in allowlist"


def test_wrapper_can_block_via_allowlist_policy_engine():
    w = _make_wrapper()
    w.configure_tool_policies(allow_patterns=["math.*"])

    with pytest.raises(MCPToolNotAllowedError) as exc:
        w._enforce_tool_allowed("filesystem.read_file")

    assert "allowlist" in exc.value.reason


def test_wrapper_argument_validation_can_block_tool_call():
    w = _make_wrapper()

    def require_path(args):
        path = args.get("path")
        if not isinstance(path, str) or not path.startswith("/safe/"):
            return "path must stay under /safe"
        return None

    w.configure_tool_policies(
        policies=[
            ToolPolicy(
                pattern="filesystem.read_file",
                effect="allow",
                risk_class="high",
                arg_validators=[require_path],
            )
        ]
    )

    with pytest.raises(MCPToolNotAllowedError) as exc:
        w._enforce_tool_allowed("filesystem.read_file", {"path": "/etc/passwd"})

    assert exc.value.reason == "tool arguments rejected by policy"


def test_wrapper_records_audit_events_for_tool_policy_decisions():
    w = _make_wrapper()
    w.configure_tool_policies(deny_patterns=["secret.*"])
    w._active_server_name = "filesystem"

    with pytest.raises(MCPToolNotAllowedError):
        w._enforce_tool_allowed("secret.delete")

    events = w.get_audit_events()
    assert events
    last = events[-1]
    assert last.event_type == "tool_policy_decision"
    assert last.outcome == "blocked"
    assert last.tool_name == "secret.delete"
    assert last.details["reason"] == "blocked by denylist"
    assert last.details["server_name"] == "filesystem"
