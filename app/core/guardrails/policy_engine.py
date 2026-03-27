from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import Any, Callable, Dict, List, Optional, Sequence


ArgValidator = Callable[[Dict[str, Any]], Optional[str]]


@dataclass(frozen=True)
class ToolPolicy:
    """Declarative policy for a tool pattern.

    Current goals:
    - preserve current denylist semantics
    - enable allowlist and per-tool metadata incrementally
    - support lightweight argument validation hooks
    """

    pattern: str
    effect: str = "allow"  # allow | deny
    risk_class: str = "default"
    reason: Optional[str] = None
    arg_validators: Sequence[ArgValidator] = field(default_factory=tuple)

    def matches(self, tool_name: str) -> bool:
        return fnmatchcase(tool_name, self.pattern)


@dataclass(frozen=True)
class ToolInvocationContext:
    tool_name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    tenant_id: Optional[str] = None
    run_id: Optional[str] = None
    session_id: Optional[str] = None
    server_name: Optional[str] = None


@dataclass(frozen=True)
class ToolInvocationDecision:
    allowed: bool
    reason: str
    matched_policy: Optional[ToolPolicy] = None
    risk_class: str = "default"
    validation_errors: Sequence[str] = field(default_factory=tuple)


class ToolPolicyEngine:
    """Evaluate tool-level access and arguments.

    Precedence:
    1. explicit deny policy
    2. explicit allow policy
    3. allowlist default-deny when allowlist exists
    4. fallback allow
    """

    def __init__(
        self,
        *,
        allow_patterns: Optional[Sequence[str]] = None,
        deny_patterns: Optional[Sequence[str]] = None,
        policies: Optional[Sequence[ToolPolicy]] = None,
    ):
        self.allow_patterns = [p for p in (allow_patterns or []) if p]
        self.deny_patterns = [p for p in (deny_patterns or []) if p]
        self.policies = list(policies or [])

    def evaluate(self, ctx: ToolInvocationContext) -> ToolInvocationDecision:
        tool_name = ctx.tool_name

        matched_policy = self._find_matching_policy(tool_name)
        if matched_policy is not None:
            validation_errors = self._run_arg_validators(matched_policy, ctx.arguments)
            if validation_errors:
                return ToolInvocationDecision(
                    allowed=False,
                    reason="tool arguments rejected by policy",
                    matched_policy=matched_policy,
                    risk_class=matched_policy.risk_class,
                    validation_errors=tuple(validation_errors),
                )

            if matched_policy.effect == "deny":
                return ToolInvocationDecision(
                    allowed=False,
                    reason=matched_policy.reason or "blocked by tool policy",
                    matched_policy=matched_policy,
                    risk_class=matched_policy.risk_class,
                )
            return ToolInvocationDecision(
                allowed=True,
                reason=matched_policy.reason or "allowed by tool policy",
                matched_policy=matched_policy,
                risk_class=matched_policy.risk_class,
            )

        if self._matches_any(self.deny_patterns, tool_name):
            return ToolInvocationDecision(
                allowed=False,
                reason="blocked by denylist",
                risk_class="restricted",
            )

        if self.allow_patterns:
            allowed = self._matches_any(self.allow_patterns, tool_name)
            return ToolInvocationDecision(
                allowed=allowed,
                reason="allowed by allowlist" if allowed else "not present in allowlist",
                risk_class="default",
            )

        return ToolInvocationDecision(
            allowed=True,
            reason="allowed by default",
            risk_class="default",
        )

    def _find_matching_policy(self, tool_name: str) -> Optional[ToolPolicy]:
        deny_match = None
        allow_match = None
        for policy in self.policies:
            if not policy.matches(tool_name):
                continue
            if policy.effect == "deny" and deny_match is None:
                deny_match = policy
            elif policy.effect != "deny" and allow_match is None:
                allow_match = policy
        return deny_match or allow_match

    @staticmethod
    def _run_arg_validators(policy: ToolPolicy, arguments: Dict[str, Any]) -> List[str]:
        errors: List[str] = []
        for validator in policy.arg_validators:
            message = validator(arguments)
            if message:
                errors.append(str(message))
        return errors

    @staticmethod
    def _matches_any(patterns: Sequence[str], value: str) -> bool:
        for pat in patterns:
            if fnmatchcase(value, pat):
                return True
        return False
