from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from app.core.bias_detector_client import BiasDetectorClient
from app.core.exceptions import ConfigurationError
from app.core.guardrail_runner import GuardrailExecutionContext, GuardrailRunner
from app.core.mcp_audit import InMemoryAuditRecorder
from app.core.mcp_wrapper_errors import GuardrailViolationError
from app.core.mcp_wrapper_guardrails_bias import (
    make_bias_after_model_guardrail,
    make_bias_after_model_guardrail_service,
)
from app.core.mcp_wrapper_guardrails_pii import (
    _detect_pii_in_obj,
    _redact_pii_in_obj,
    make_pii_after_model_guardrail,
    make_pii_before_model_guardrail,
)

if TYPE_CHECKING:
    from app.core.mcp_wrapper import MCPWrapper

GuardrailContext = GuardrailExecutionContext


def normalize_mode(mode: Optional[str], *, default: str, allowed: set[str]) -> str:
    normalized = (mode or default).strip().lower()
    return normalized if normalized in allowed else default


def replace_guardrail(
    wrapper: MCPWrapper,
    *,
    pipeline_attr: str,
    slot_attr: str,
    new_guardrail: Any,
) -> None:
    pipeline = list(getattr(wrapper, pipeline_attr, []) or [])
    current = getattr(wrapper, slot_attr, None)
    replaced = False

    if current is not None:
        for idx, guardrail in enumerate(pipeline):
            if guardrail is current:
                replaced = True
                if new_guardrail is None:
                    del pipeline[idx]
                else:
                    pipeline[idx] = new_guardrail
                break
        if not replaced:
            pipeline = [guardrail for guardrail in pipeline if guardrail is not current]

    if not replaced and new_guardrail is not None:
        pipeline.append(new_guardrail)

    setattr(wrapper, pipeline_attr, pipeline)
    setattr(wrapper, slot_attr, new_guardrail)


def set_pii_mode(wrapper: MCPWrapper, mode: Optional[str]) -> None:
    normalized = normalize_mode(mode, default="redact", allowed={"off", "redact", "block"})
    wrapper.pii_mode = normalized
    new_guardrail = None if normalized == "off" else make_pii_after_model_guardrail(mode=normalized)
    replace_guardrail(
        wrapper,
        pipeline_attr="after_model_guardrails",
        slot_attr="_pii_after_model_guardrail",
        new_guardrail=new_guardrail,
    )


def set_pii_input_mode(wrapper: MCPWrapper, mode: Optional[str]) -> None:
    normalized = normalize_mode(mode, default="block", allowed={"off", "redact", "block"})
    wrapper.pii_input_mode = normalized
    new_guardrail = None if normalized == "off" else make_pii_before_model_guardrail(mode=normalized)
    replace_guardrail(
        wrapper,
        pipeline_attr="before_model_guardrails",
        slot_attr="_pii_before_model_guardrail",
        new_guardrail=new_guardrail,
    )


def set_bias_mode(wrapper: MCPWrapper, mode: Optional[str]) -> None:
    normalized = normalize_mode(mode, default="off", allowed={"off", "block"})
    wrapper.bias_mode = normalized
    new_guardrail = None if normalized == "off" else make_bias_after_model_guardrail(mode=normalized)
    replace_guardrail(
        wrapper,
        pipeline_attr="after_model_guardrails",
        slot_attr="_bias_after_model_guardrail",
        new_guardrail=new_guardrail,
    )


def set_bias_settings(
    wrapper: MCPWrapper,
    *,
    mode: Optional[str],
    base_url: Optional[str] = None,
    timeout_seconds: float = 5.0,
    threshold: float = 0.5,
    top_k: int = 5,
    active_categories: Optional[List[str]] = None,
    unsafe_labels: Optional[List[str]] = None,
    model_id: Optional[str] = None,
    revision: Optional[str] = None,
    return_all_scores: bool = False,
    return_char_spans: bool = False,
    checks: Optional[List[Any]] = None,
    fail_closed: bool = True,
) -> None:
    normalized = normalize_mode(mode, default="off", allowed={"off", "block"})
    wrapper.bias_mode = normalized

    if normalized == "off":
        replace_guardrail(
            wrapper,
            pipeline_attr="after_model_guardrails",
            slot_attr="_bias_after_model_guardrail",
            new_guardrail=None,
        )
        return

    if not base_url:
        set_bias_mode(wrapper, normalized)
        return

    try:
        wrapper._bias_detector_service = BiasDetectorClient(
            base_url=base_url,
            timeout_seconds=float(timeout_seconds),
        )
    except Exception as exc:
        raise ConfigurationError(f"Invalid bias-detector-service configuration: {exc}")

    new_guardrail = make_bias_after_model_guardrail_service(
        client=wrapper._bias_detector_service,
        mode=normalized,
        threshold=float(threshold),
        top_k=int(top_k),
        active_categories=active_categories,
        unsafe_labels=unsafe_labels,
        model_id=model_id,
        revision=revision,
        return_all_scores=bool(return_all_scores),
        return_char_spans=bool(return_char_spans),
        checks=checks,
        fail_closed=bool(fail_closed),
    )
    replace_guardrail(
        wrapper,
        pipeline_attr="after_model_guardrails",
        slot_attr="_bias_after_model_guardrail",
        new_guardrail=new_guardrail,
    )


def get_guardrail_runner(wrapper: MCPWrapper) -> GuardrailRunner:
    runner = getattr(wrapper, "guardrail_runner", None)
    recorder = getattr(wrapper, "audit_recorder", None)
    if recorder is None:
        recorder = InMemoryAuditRecorder()
        wrapper.audit_recorder = recorder

    if runner is None or getattr(runner, "audit_recorder", None) is not recorder:
        runner = GuardrailRunner(
            audit_recorder=recorder,
            violation_error_cls=GuardrailViolationError,
        )
        wrapper.guardrail_runner = runner
    return runner


def _build_tool_result_context(
    wrapper: MCPWrapper,
    *,
    tool_name: str,
    arguments: Optional[Dict[str, Any]] = None,
) -> GuardrailContext:
    return GuardrailContext(
        tenant_id=wrapper.tenant_id,
        run_id=wrapper.run_id,
        session_id=wrapper.session_id,
        server_name=getattr(wrapper, "_active_server_name", None),
        tool_name=tool_name,
        arguments=arguments or {},
    )


def wrap_tool_result(
    wrapper: MCPWrapper,
    tool_name: str,
    result: Any,
    *,
    arguments: Optional[Dict[str, Any]] = None,
) -> Any:
    ctx = _build_tool_result_context(wrapper, tool_name=tool_name, arguments=arguments)
    outcome = get_guardrail_runner(wrapper).tool_result(
        ctx,
        result,
        enabled=getattr(wrapper, "guardrails_enabled", True),
        pii_mode=getattr(wrapper, "pii_mode", "redact"),
        redact_result=_redact_pii_in_obj,
        detect_result_pii=_detect_pii_in_obj,
    )
    return outcome.value


async def run_before_model_guardrails(wrapper: MCPWrapper, ctx: GuardrailContext) -> GuardrailContext:
    outcome = await get_guardrail_runner(wrapper).before_model(
        ctx,
        getattr(wrapper, "before_model_guardrails", []),
        enabled=getattr(wrapper, "guardrails_enabled", True),
        timeout_seconds=getattr(wrapper, "guardrail_timeout_seconds", None),
    )
    return outcome.value


async def run_after_model_guardrails(wrapper: MCPWrapper, ctx: GuardrailContext, output: Any) -> Any:
    outcome = await get_guardrail_runner(wrapper).after_model(
        ctx,
        output,
        getattr(wrapper, "after_model_guardrails", []),
        enabled=getattr(wrapper, "guardrails_enabled", True),
        timeout_seconds=getattr(wrapper, "guardrail_timeout_seconds", None),
    )
    return outcome.value
