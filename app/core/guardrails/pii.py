from __future__ import annotations

import re
from typing import Any, Dict

from app.core.guardrails.runner import GuardrailExecutionContext
from app.core.runtime.errors import GuardrailViolationError
from app.core.guardrails.bias import _extract_final_answer

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_RE = re.compile(r"\b(?:\+?\d[\d\s().-]{6,}\d)\b")
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b", re.IGNORECASE)


def _detect_pii(text: str) -> Dict[str, int]:
    return {
        "email": len(_EMAIL_RE.findall(text)),
        "phone": len(_PHONE_RE.findall(text)),
        "iban": len(_IBAN_RE.findall(text)),
    }


def redact_pii(text: str) -> str:
    redacted = _EMAIL_RE.sub("[MCP_BRIDGE_REDACTED_EMAIL]", text)
    redacted = _IBAN_RE.sub("[MCP_BRIDGE_REDACTED_IBAN]", redacted)
    redacted = _PHONE_RE.sub("[MCP_BRIDGE_REDACTED_PHONE]", redacted)
    return redacted


def _redact_pii_in_obj(value: Any) -> Any:
    if value is None:
        return value
    if isinstance(value, str):
        return redact_pii(value)
    if isinstance(value, list):
        return [_redact_pii_in_obj(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_pii_in_obj(item) for item in value)
    if isinstance(value, dict):
        return {key: _redact_pii_in_obj(item) for key, item in value.items()}
    return value


def _detect_pii_in_obj(value: Any) -> Dict[str, int]:
    counts = {"email": 0, "phone": 0, "iban": 0}
    if value is None:
        return counts

    if isinstance(value, str):
        found = _detect_pii(value)
        for key in counts:
            counts[key] += int(found.get(key, 0) or 0)
        return counts

    if isinstance(value, (list, tuple)):
        for item in value:
            found = _detect_pii_in_obj(item)
            for key in counts:
                counts[key] += int(found.get(key, 0) or 0)
        return counts

    if isinstance(value, dict):
        for item in value.values():
            found = _detect_pii_in_obj(item)
            for key in counts:
                counts[key] += int(found.get(key, 0) or 0)
        return counts

    return counts


def make_pii_after_model_guardrail(*, mode: str = "redact"):
    normalized_mode = (mode or "redact").strip().lower()
    if normalized_mode not in {"off", "redact", "block"}:
        normalized_mode = "redact"

    async def _guardrail(ctx: GuardrailExecutionContext, output: Any) -> Any:
        if output is None:
            return output

        text = _extract_final_answer(str(output))
        counts = _detect_pii(text)
        present = [key for key, value in counts.items() if value > 0]
        if not present or normalized_mode == "off":
            return output

        if normalized_mode == "block":
            raise GuardrailViolationError(
                code="PII_DETECTED",
                message="PII detected in model output",
                phase="after_model",
                rule="pii",
                tenant_id=getattr(ctx, "tenant_id", None),
                run_id=getattr(ctx, "run_id", None),
                session_id=getattr(ctx, "session_id", None),
                details={
                    "types": present,
                    "counts": counts,
                    "mode": "block",
                },
            )

        return redact_pii(text)

    return _guardrail


def make_pii_before_model_guardrail(*, mode: str = "block"):
    normalized_mode = (mode or "block").strip().lower()
    if normalized_mode not in {"off", "redact", "block"}:
        normalized_mode = "block"

    def _guardrail(ctx: GuardrailExecutionContext) -> GuardrailExecutionContext:
        if normalized_mode == "off":
            return ctx

        query = getattr(ctx, "query", None)
        if not query:
            return ctx

        text = str(query)
        counts = _detect_pii(text)
        present = [key for key, value in counts.items() if value > 0]
        if not present:
            return ctx

        if normalized_mode == "block":
            raise GuardrailViolationError(
                code="PII_DETECTED",
                message="PII detected in user input",
                phase="before_model",
                rule="pii",
                tenant_id=getattr(ctx, "tenant_id", None),
                run_id=getattr(ctx, "run_id", None),
                session_id=getattr(ctx, "session_id", None),
                details={
                    "types": present,
                    "counts": counts,
                    "mode": "block",
                },
            )

        redacted = redact_pii(text)
        return GuardrailExecutionContext(
            tenant_id=getattr(ctx, "tenant_id", None),
            run_id=getattr(ctx, "run_id", None),
            session_id=getattr(ctx, "session_id", None),
            query=redacted,
            server_name=getattr(ctx, "server_name", None),
            tool_name=getattr(ctx, "tool_name", None),
            arguments=getattr(ctx, "arguments", {}) or {},
        )

    return _guardrail
