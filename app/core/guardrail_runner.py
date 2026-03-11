from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Iterable, Optional, Type, Union

from app.core.mcp_audit import AuditEvent, InMemoryAuditRecorder, utc_now_iso
from app.utils.logging import get_logger

logger = get_logger(__name__)


BeforeModelGuardrail = Callable[[Any], Union[Any, Awaitable[Any]]]
AfterModelGuardrail = Callable[[Any, Any], Union[Any, Awaitable[Any]]]


@dataclass(frozen=True)
class GuardrailExecutionContext:
    tenant_id: Optional[str] = None
    run_id: Optional[str] = None
    session_id: Optional[str] = None
    query: Optional[str] = None
    server_name: Optional[str] = None
    tool_name: Optional[str] = None
    arguments: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GuardrailOutcome:
    state: str
    value: Any
    guardrail_name: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


class GuardrailRunner:
    def __init__(
        self,
        *,
        audit_recorder: Optional[InMemoryAuditRecorder],
        violation_error_cls: Type[Exception],
    ) -> None:
        self.audit_recorder = audit_recorder or InMemoryAuditRecorder()
        self.violation_error_cls = violation_error_cls

    async def before_model(
        self,
        ctx: GuardrailExecutionContext,
        guardrails: Iterable[BeforeModelGuardrail],
        *,
        enabled: bool = True,
        timeout_seconds: Optional[float] = None,
    ) -> GuardrailOutcome:
        guardrails_list = list(guardrails or [])
        if enabled is False:
            outcome = GuardrailOutcome(
                state="skipped",
                value=ctx,
                details={"reason": "guardrails_disabled", "guardrails_configured": len(guardrails_list)},
            )
            self._record(event_type="before_model_guardrail", ctx=ctx, outcome=outcome)
            return outcome

        transformed = False
        for guardrail in guardrails_list:
            guardrail_name = getattr(guardrail, "__name__", "guardrail")
            try:
                next_ctx = await self._run_guardrail(guardrail(ctx), timeout_seconds)
            except asyncio.TimeoutError:
                exc = self.violation_error_cls(
                    code="MCP_GUARDRAIL_TIMEOUT",
                    message="Guardrail timed out",
                    phase="before_model",
                    rule=guardrail_name,
                    tenant_id=ctx.tenant_id,
                    run_id=ctx.run_id,
                    session_id=ctx.session_id,
                    details={"timeout_seconds": timeout_seconds},
                )
                self._record_blocked("before_model_guardrail", ctx, exc, guardrail_name)
                raise exc
            except self.violation_error_cls as exc:
                self._record_blocked("before_model_guardrail", ctx, exc, guardrail_name)
                raise
            except Exception as exc:
                self._record_exception("before_model_guardrail", ctx, guardrail_name, exc)
                raise

            transformed = transformed or (next_ctx != ctx)
            ctx = next_ctx

        outcome = GuardrailOutcome(
            state="redacted" if transformed else ("passed" if guardrails_list else "skipped"),
            value=ctx,
            details={
                "transformed": transformed,
                "guardrails_executed": len(guardrails_list),
                **({"reason": "no_guardrails"} if not guardrails_list else {}),
            },
        )
        self._record(event_type="before_model_guardrail", ctx=ctx, outcome=outcome)
        return outcome

    async def after_model(
        self,
        ctx: GuardrailExecutionContext,
        output: Any,
        guardrails: Iterable[AfterModelGuardrail],
        *,
        enabled: bool = True,
        timeout_seconds: Optional[float] = None,
    ) -> GuardrailOutcome:
        guardrails_list = list(guardrails or [])
        if enabled is False:
            outcome = GuardrailOutcome(
                state="skipped",
                value=output,
                details={"reason": "guardrails_disabled", "guardrails_configured": len(guardrails_list)},
            )
            self._record(event_type="after_model_guardrail", ctx=ctx, outcome=outcome)
            return outcome

        transformed = False
        for guardrail in guardrails_list:
            guardrail_name = getattr(guardrail, "__name__", "guardrail")
            try:
                next_output = await self._run_guardrail(guardrail(ctx, output), timeout_seconds)
            except asyncio.TimeoutError:
                exc = self.violation_error_cls(
                    code="MCP_GUARDRAIL_TIMEOUT",
                    message="Guardrail timed out",
                    phase="after_model",
                    rule=guardrail_name,
                    tenant_id=ctx.tenant_id,
                    run_id=ctx.run_id,
                    session_id=ctx.session_id,
                    details={"timeout_seconds": timeout_seconds},
                )
                self._record_blocked("after_model_guardrail", ctx, exc, guardrail_name)
                raise exc
            except self.violation_error_cls as exc:
                self._record_blocked("after_model_guardrail", ctx, exc, guardrail_name)
                raise
            except Exception as exc:
                self._record_exception("after_model_guardrail", ctx, guardrail_name, exc)
                raise

            transformed = transformed or (next_output != output)
            output = next_output

        outcome = GuardrailOutcome(
            state="redacted" if transformed else ("passed" if guardrails_list else "skipped"),
            value=output,
            details={
                "transformed": transformed,
                "guardrails_executed": len(guardrails_list),
                **({"reason": "no_guardrails"} if not guardrails_list else {}),
            },
        )
        self._record(event_type="after_model_guardrail", ctx=ctx, outcome=outcome)
        return outcome

    def tool_result(
        self,
        ctx: GuardrailExecutionContext,
        result: Any,
        *,
        enabled: bool = True,
        pii_mode: str = "redact",
        redact_result: Callable[[Any], Any],
        detect_result_pii: Callable[[Any], Dict[str, int]],
    ) -> GuardrailOutcome:
        if enabled is False:
            outcome = GuardrailOutcome(
                state="skipped",
                value=result,
                details={"reason": "guardrails_disabled"},
            )
            self._record(event_type="tool_result_guardrail", ctx=ctx, outcome=outcome)
            return outcome

        mode = pii_mode
        if mode == "redact":
            wrapped = redact_result(result)
            transformed = wrapped != result
            outcome = GuardrailOutcome(
                state="redacted" if transformed else "passed",
                value=wrapped,
                details={"rule": "pii", "mode": mode, "transformed": transformed},
            )
            self._record(event_type="tool_result_guardrail", ctx=ctx, outcome=outcome)
            return outcome

        if mode == "block":
            try:
                counts = detect_result_pii(result)
                present = [k for k, v in counts.items() if int(v or 0) > 0]
                if not present:
                    outcome = GuardrailOutcome(
                        state="passed",
                        value=result,
                        details={"rule": "pii", "mode": mode},
                    )
                    self._record(event_type="tool_result_guardrail", ctx=ctx, outcome=outcome)
                    return outcome

                exc = self.violation_error_cls(
                    code="PII_DETECTED",
                    message="PII detected in tool result",
                    phase="tool_result",
                    rule="pii",
                    tenant_id=ctx.tenant_id,
                    run_id=ctx.run_id,
                    session_id=ctx.session_id,
                    tool_name=ctx.tool_name,
                    details={
                        "types": present,
                        "counts": counts,
                        "mode": "block",
                    },
                )
                self._record_blocked(
                    "tool_result_guardrail",
                    ctx,
                    exc,
                    "pii",
                    details_override={"rule": "pii", "mode": mode, "types": present, "counts": counts},
                )
                raise exc
            except self.violation_error_cls:
                raise
            except Exception as exc:
                self._record_exception(
                    "tool_result_guardrail",
                    ctx,
                    "pii",
                    exc,
                    details_override={"rule": "pii", "mode": mode, "error": type(exc).__name__},
                )
                raise

        outcome = GuardrailOutcome(
            state="skipped",
            value=result,
            details={"rule": "pii", "mode": mode},
        )
        self._record(event_type="tool_result_guardrail", ctx=ctx, outcome=outcome)
        return outcome

    async def _run_guardrail(self, value: Any, timeout_seconds: Optional[float]) -> Any:
        if timeout_seconds:
            return await asyncio.wait_for(self._maybe_await(value), timeout=timeout_seconds)
        return await self._maybe_await(value)

    @staticmethod
    async def _maybe_await(value: Any) -> Any:
        if inspect.isawaitable(value):
            return await value
        return value

    def _record(self, *, event_type: str, ctx: GuardrailExecutionContext, outcome: GuardrailOutcome) -> None:
        details = self._build_audit_details(
            event_type=event_type,
            ctx=ctx,
            details=outcome.details,
        )
        try:
            self.audit_recorder.record(
                AuditEvent(
                    event_type=event_type,
                    timestamp=utc_now_iso(),
                    tenant_id=ctx.tenant_id,
                    run_id=ctx.run_id,
                    session_id=ctx.session_id,
                    tool_name=ctx.tool_name,
                    outcome=outcome.state,
                    details=details,
                )
            )
        except Exception:
            logger.debug("Failed to record guardrail audit event", exc_info=True)

    def _record_blocked(
        self,
        event_type: str,
        ctx: GuardrailExecutionContext,
        exc: Exception,
        guardrail_name: str,
        *,
        details_override: Optional[Dict[str, Any]] = None,
    ) -> None:
        violation_details = dict(getattr(exc, "details", {}) or {})
        details = {
            "rule": getattr(exc, "rule", None) or guardrail_name,
            "code": getattr(exc, "code", None),
            "phase": getattr(exc, "phase", None),
            "violation_details": violation_details,
            "details": dict(violation_details),
        }
        if details_override:
            details.update(details_override)
        details.setdefault("guardrail", details.get("rule"))
        self._record(
            event_type=event_type,
            ctx=ctx,
            outcome=GuardrailOutcome(
                state="blocked",
                value=None,
                guardrail_name=guardrail_name,
                details=details,
            ),
        )

    def _record_exception(
        self,
        event_type: str,
        ctx: GuardrailExecutionContext,
        guardrail_name: str,
        exc: Exception,
        *,
        details_override: Optional[Dict[str, Any]] = None,
    ) -> None:
        details = {
            "rule": guardrail_name,
            "error": type(exc).__name__,
        }
        if details_override:
            details.update(details_override)
        details.setdefault("guardrail", details.get("rule"))
        self._record(
            event_type=event_type,
            ctx=ctx,
            outcome=GuardrailOutcome(
                state="blocked",
                value=None,
                guardrail_name=guardrail_name,
                details=details,
            ),
        )

    @staticmethod
    def _phase_for_event_type(event_type: str) -> Optional[str]:
        return {
            "before_model_guardrail": "before_model",
            "after_model_guardrail": "after_model",
            "tool_result_guardrail": "tool_result",
        }.get(event_type)

    def _build_audit_details(
        self,
        *,
        event_type: str,
        ctx: GuardrailExecutionContext,
        details: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        merged = dict(details or {})
        merged.setdefault("phase", self._phase_for_event_type(event_type))
        if ctx.server_name is not None:
            merged.setdefault("server_name", ctx.server_name)
        if event_type == "tool_result_guardrail":
            merged.setdefault("arguments_present", bool(ctx.arguments))
        return merged
