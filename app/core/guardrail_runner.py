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
        ctx: Any,
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
                    tenant_id=getattr(ctx, "tenant_id", None),
                    run_id=getattr(ctx, "run_id", None),
                    session_id=getattr(ctx, "session_id", None),
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
        ctx: Any,
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
                    tenant_id=getattr(ctx, "tenant_id", None),
                    run_id=getattr(ctx, "run_id", None),
                    session_id=getattr(ctx, "session_id", None),
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

    async def _run_guardrail(self, value: Any, timeout_seconds: Optional[float]) -> Any:
        if timeout_seconds:
            return await asyncio.wait_for(self._maybe_await(value), timeout=timeout_seconds)
        return await self._maybe_await(value)

    @staticmethod
    async def _maybe_await(value: Any) -> Any:
        if inspect.isawaitable(value):
            return await value
        return value

    def _record(self, *, event_type: str, ctx: Any, outcome: GuardrailOutcome) -> None:
        try:
            self.audit_recorder.record(
                AuditEvent(
                    event_type=event_type,
                    timestamp=utc_now_iso(),
                    tenant_id=getattr(ctx, "tenant_id", None),
                    run_id=getattr(ctx, "run_id", None),
                    session_id=getattr(ctx, "session_id", None),
                    outcome=outcome.state,
                    details=outcome.details,
                )
            )
        except Exception:
            logger.debug("Failed to record guardrail audit event", exc_info=True)

    def _record_blocked(self, event_type: str, ctx: Any, exc: Exception, guardrail_name: str) -> None:
        details = {
            "guardrail": getattr(exc, "rule", None) or guardrail_name,
            "code": getattr(exc, "code", None),
            "phase": getattr(exc, "phase", None),
            "details": getattr(exc, "details", {}) or {},
        }
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

    def _record_exception(self, event_type: str, ctx: Any, guardrail_name: str, exc: Exception) -> None:
        self._record(
            event_type=event_type,
            ctx=ctx,
            outcome=GuardrailOutcome(
                state="blocked",
                value=None,
                guardrail_name=guardrail_name,
                details={
                    "guardrail": guardrail_name,
                    "error": type(exc).__name__,
                },
            ),
        )
