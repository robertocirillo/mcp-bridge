from __future__ import annotations

from typing import Any, Dict, Optional


class MCPToolNotAllowedError(Exception):
    """Raised when a tool call is blocked by session policy."""

    def __init__(
        self,
        tool_name: str,
        *,
        tenant_id: Optional[str] = None,
        run_id: Optional[str] = None,
        session_id: Optional[str] = None,
        reason: str = "blocked by session policy",
    ) -> None:
        # Preserve the blocked tool and execution context for audit and error reporting.
        self.tool_name = tool_name
        self.tenant_id = tenant_id
        self.run_id = run_id
        self.session_id = session_id
        self.reason = reason
        # Build a readable exception message that can be surfaced directly to callers.
        super().__init__(f"Tool '{tool_name}' not allowed: {reason}")


class GuardrailViolationError(Exception):
    """Raised when a guardrail blocks the request or output."""

    def __init__(
        self,
        *,
        http_status: int = 403,
        code: str = "GUARDRAIL_VIOLATION",
        message: str,
        phase: str,
        rule: Optional[str] = None,
        tenant_id: Optional[str] = None,
        run_id: Optional[str] = None,
        session_id: Optional[str] = None,
        tool_name: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        # Store structured metadata so API layers can convert the exception into a stable response.
        self.code = code
        self.message = message
        self.http_status = int(http_status)
        self.phase = phase
        self.rule = rule
        self.tenant_id = tenant_id
        self.run_id = run_id
        self.session_id = session_id
        self.tool_name = tool_name
        self.details = details or {}
        # Keep the base exception message short because API code exposes the richer metadata separately.
        super().__init__(message)
