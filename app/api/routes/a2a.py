"""A2A REST routes.

These endpoints expose a stable REST surface for UIs / visual builders.
Internally we use the official `a2a-sdk` via `app.core.clients.a2a_client.A2AClient`.

IMPORTANT:
- `blocking` is a REST convenience flag, not part of the A2A protocol.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_a2a_client, get_settings
from app.core.clients.a2a_client import A2AClient, A2AClientError
from app.models.requests import A2AMessageRequest
from app.models.responses import A2AAgentSummary, A2AMessageResponse, A2ATaskStatusResponse, A2ATaskState
from app.utils.logging import get_logger
from config import Settings

router = APIRouter(prefix="/a2a", tags=["a2a"])
logger = get_logger(__name__)


def _raise_a2a_http_error(
    *,
    status_code: int,
    code: str,
    message: str,
    agent_id: Optional[str] = None,
    task_id: Optional[str] = None,
    operation: Optional[str] = None,
    upstream: Optional[Dict[str, Any]] = None,
    field: Optional[str] = None,
) -> None:
    """Raise FastAPI HTTPException with stable structured detail payload."""
    detail: Dict[str, Any] = {"code": code, "message": message}
    if agent_id is not None:
        detail["agent_id"] = agent_id
    if task_id is not None:
        detail["task_id"] = task_id
    if operation is not None:
        detail["operation"] = operation
    if upstream is not None:
        detail["upstream"] = upstream
    if field is not None:
        detail["field"] = field
    raise HTTPException(status_code=status_code, detail=detail)


def _normalize_goal(goal: str, *, operation: str, agent_id: Optional[str] = None) -> str:
    if goal is None:
        _raise_a2a_http_error(
            status_code=422,
            code="A2A_SCHEMA_ERROR",
            message="Field 'goal' is required",
            field="goal",
            agent_id=agent_id,
            operation=operation,
        )
    g = goal.strip()
    if not g:
        _raise_a2a_http_error(
            status_code=422,
            code="A2A_SCHEMA_ERROR",
            message="Field 'goal' must be a non-empty string",
            field="goal",
            agent_id=agent_id,
            operation=operation,
        )
    return g


def _ensure_jsonable(value: Any, field_name: str, *, operation: str, agent_id: Optional[str] = None) -> None:
    try:
        json.dumps(value)
    except Exception:
        _raise_a2a_http_error(
            status_code=422,
            code="A2A_SCHEMA_ERROR",
            message=f"Field '{field_name}' must be JSON-serializable",
            field=field_name,
            operation=operation,
            agent_id=agent_id,
        )


def _normalize_task_id(task_id: Any) -> Any:
    if task_id is None:
        return None
    if isinstance(task_id, str):
        t = task_id.strip()
        return t or None
    return task_id


def _normalize_task_state(status: Optional[Any]) -> tuple[A2ATaskState, Optional[str]]:
    """Normalize upstream task status into A2A-standard TaskState values.

    Upstream SDKs may return:
    - a plain string ("working")
    - an enum (TaskState.WORKING)
    - a structured object (e.g. TaskStatus) with a `.state` attribute

    We return:
    - the normalized A2A TaskState enum
    - the raw upstream state string (for observability / debugging)
    """
    if status is None:
        return (A2ATaskState.unknown, None)

    raw = status
    if hasattr(raw, "state"):
        raw = getattr(raw, "state")

    raw_str = str(raw).strip()
    token = raw_str.lower()

    # Handle enum string forms like "TaskState.SUBMITTED" or "taskstate.submitted"
    if "." in token:
        token = token.split(".")[-1]

    # Normalize separators
    token = token.replace("_", "-").strip()

    # Canonical mappings + common synonyms
    if token in {"submitted", "queued", "pending", "created", "accepted", "scheduled"}:
        return (A2ATaskState.submitted, raw_str)

    if token in {"working", "running", "in-progress", "processing", "in progress"}:
        return (A2ATaskState.working, raw_str)

    if token in {"input-required", "requires-input", "inputrequired", "requiresinput"}:
        return (A2ATaskState.input_required, raw_str)

    if token in {"completed", "succeeded", "success", "done", "finished"}:
        return (A2ATaskState.completed, raw_str)

    if token in {"canceled", "cancelled", "canceled", "cancelled", "cancel"}:
        return (A2ATaskState.canceled, raw_str)

    if token in {"failed", "error", "rejected", "timeout"}:
        return (A2ATaskState.failed, raw_str)

    return (A2ATaskState.unknown, raw_str)


def _extract_task_message_from_payload(payload: Any) -> Optional[str]:
    """Best-effort extraction of a human message from a Task-like payload.

    We prefer, in order:
    1) task.status.message.parts[*].text (HITL / input-required message)
    2) first artifact part text
    3) last agent message in history
    """
    if not isinstance(payload, dict):
        return None

    task = payload.get("task") if "task" in payload else payload
    if not isinstance(task, dict):
        return None

    # 1) task.status.message.parts[0].text
    try:
        status = task.get("status") or {}
        msg = status.get("message") or {}
        parts = msg.get("parts") or []
        for p in parts:
            if isinstance(p, dict) and p.get("kind") == "text" and isinstance(p.get("text"), str):
                t = p["text"].strip()
                if t:
                    return t
    except Exception:
        pass

    # 2) artifacts[*].parts[*].text
    try:
        artifacts = task.get("artifacts") or []
        if isinstance(artifacts, list):
            for art in artifacts:
                if not isinstance(art, dict):
                    continue
                parts = art.get("parts") or []
                for p in parts:
                    if isinstance(p, dict) and p.get("kind") == "text" and isinstance(p.get("text"), str):
                        t = p["text"].strip()
                        if t:
                            return t
    except Exception:
        pass

    # 3) last agent message in history
    try:
        history = task.get("history") or []
        if isinstance(history, list):
            for item in reversed(history):
                if not isinstance(item, dict):
                    continue
                if item.get("role") != "agent":
                    continue
                parts = item.get("parts") or []
                for p in parts:
                    if isinstance(p, dict) and p.get("kind") == "text" and isinstance(p.get("text"), str):
                        t = p["text"].strip()
                        if t:
                            return t
    except Exception:
        pass

    return None

def _looks_task_polling_not_applicable(message: str, upstream: Optional[Dict[str, Any]] = None) -> bool:
    msg = (message or "").lower()

    needles = (
        "not supported",
        "not applicable",
        "not implemented",
        "method not found",
        "unknown method",
        "no such method",
        "rpc method not found",
        "tasks not supported",
    )
    if any(n in msg for n in needles):
        return True

    # JSON-RPC "method not found" (common) code is -32601.
    try:
        if upstream and isinstance(upstream, dict):
            err = upstream.get("error")
            if isinstance(err, dict) and err.get("code") == -32601:
                return True
    except Exception:
        pass

    return False


def _ensure_a2a_enabled(
    settings: Settings,
    *,
    operation: str,
    agent_id: Optional[str] = None,
    task_id: Optional[str] = None,
) -> None:
    if not settings.a2a.enabled:
        _raise_a2a_http_error(
            status_code=400,
            code="A2A_DISABLED",
            message="A2A integration is disabled",
            operation=operation,
            agent_id=agent_id,
            task_id=task_id,
        )


def _ensure_agent_enabled(settings: Settings, agent_id: str, *, operation: str) -> None:
    conf = (settings.a2a.agents or {}).get(agent_id)
    if conf is None or not conf.enabled:
        _raise_a2a_http_error(
            status_code=404,
            code="A2A_AGENT_NOT_FOUND",
            message=f"Unknown or disabled agent_id: {agent_id}",
            agent_id=agent_id,
            operation=operation,
        )


def _map_a2a_client_error(exc: A2AClientError) -> Dict[str, Any]:
    # Be defensive: attributes may or may not exist depending on implementation.
    upstream = getattr(exc, "upstream", None)
    if upstream is not None and not isinstance(upstream, dict):
        upstream = {"value": upstream}
    return {
        "status_code": getattr(exc, "status_code", 502),
        "code": getattr(exc, "code", "A2A_UPSTREAM_ERROR"),
        "message": getattr(exc, "message", None) or str(exc),
        "upstream": upstream,
    }


@router.get("/agents", response_model=List[A2AAgentSummary])
async def list_a2a_agents(
    settings: Settings = Depends(get_settings),
) -> List[A2AAgentSummary]:
    try:
        a2a_settings = settings.a2a
        if not a2a_settings.enabled:
            return []

        summaries: List[A2AAgentSummary] = []
        for agent_id, conf in (a2a_settings.agents or {}).items():
            if not getattr(conf, "enabled", False):
                continue

            # Be robust across config schema variants:
            # - some configs may have `label` but not `name`
            # - others may have `display_name`
            name = (
                getattr(conf, "name", None)
                or getattr(conf, "label", None)
                or getattr(conf, "display_name", None)
                or agent_id
            )

            description = getattr(conf, "description", None)

            # Keep backward compatibility: some configs use runtime_url rather than endpoint
            endpoint = getattr(conf, "endpoint", None) or getattr(conf, "runtime_url", None)
            card_url = getattr(conf, "card_url", None)

            summaries.append(
                A2AAgentSummary(
                    agent_id=agent_id,
                    name=name,
                    description=description,
                    enabled=True,
                    endpoint=endpoint,
                    card_url=card_url,
                )
            )

        return summaries

    except Exception as exc:
        logger.exception("Error listing A2A agents: %s", exc)
        _raise_a2a_http_error(
            status_code=500,
            code="A2A_INTERNAL_ERROR",
            message="Error listing A2A agents",
        )


@router.post("/agents/{agent_id}/messages", response_model=A2AMessageResponse)
async def send_a2a_message(
    agent_id: str,
    request: A2AMessageRequest,
    settings: Settings = Depends(get_settings),
    a2a_client: A2AClient = Depends(get_a2a_client),
) -> A2AMessageResponse:
    """Send a message to an A2A agent.

    REST model:
    - blocking=true  -> wait for task completion as much as SDK allows
    - blocking=false -> return early (best-effort) with a task_id
    """
    _ensure_a2a_enabled(settings, operation="send_message", agent_id=agent_id)
    _ensure_agent_enabled(settings, agent_id, operation="send_message")

    goal = _normalize_goal(request.goal, operation="send_message", agent_id=agent_id)
    if request.metadata is not None:
        _ensure_jsonable(request.metadata, "metadata", operation="send_message", agent_id=agent_id)

    try:
        result = await a2a_client.send_message(
            agent_id=agent_id,
            text=goal,
            blocking=request.blocking,
            request_metadata=request.metadata,
        )
        Mode = Literal["blocking", "task"]

        task_id = _normalize_task_id(result.task_id)
        effective_mode: Mode = "task" if task_id else "blocking"

        st, upstream_state = _normalize_task_state(getattr(result, "status", None))
        # For message-only agents we may not get a task status at all.
        status_value = None if upstream_state is None else st

        # Prefer explicit SDK message; otherwise extract from payload (task.status.message / artifacts / history).
        message_value = (
            result.message
            or _extract_task_message_from_payload(result.output)
            or _extract_task_message_from_payload(result.raw_response)
        )

        return A2AMessageResponse(
            mode=effective_mode,
            agent_id=agent_id,
            task_id=task_id,
            status=status_value,
            upstream_state=upstream_state,
            output=result.output,
            message=message_value,
            raw_response=result.raw_response,
        )

    except A2AClientError as exc:
        mapped = _map_a2a_client_error(exc)
        logger.error("Error executing A2A message for %s: %s", agent_id, exc)
        _raise_a2a_http_error(
            status_code=mapped["status_code"],
            code=mapped["code"],
            message=mapped["message"],
            agent_id=agent_id,
            operation="send_message",
            upstream=mapped.get("upstream"),
        )

    except Exception as exc:
        logger.exception("Unexpected error executing A2A message for %s: %s", agent_id, exc)
        _raise_a2a_http_error(
            status_code=500,
            code="A2A_INTERNAL_ERROR",
            message="Error executing A2A message",
            agent_id=agent_id,
            operation="send_message",
        )


@router.get("/agents/{agent_id}/tasks/{task_id}", response_model=A2ATaskStatusResponse)
async def get_a2a_task(
    agent_id: str,
    task_id: str,
    settings: Settings = Depends(get_settings),
    a2a_client: A2AClient = Depends(get_a2a_client),
) -> A2ATaskStatusResponse:
    _ensure_a2a_enabled(settings, operation="get_task", agent_id=agent_id, task_id=task_id)
    _ensure_agent_enabled(settings, agent_id, operation="get_task")

    try:
        result = await a2a_client.get_task(agent_id=agent_id, task_id=task_id)

        st, upstream_state = _normalize_task_state(getattr(result, "status", None))
        message_value = (
            result.message
            or _extract_task_message_from_payload(result.output)
            or _extract_task_message_from_payload(result.raw_response)
        )

        return A2ATaskStatusResponse(
            agent_id=agent_id,
            task_id=task_id,
            status=st,
            upstream_state=upstream_state,
            output=result.output,
            message=message_value,
            raw_response=result.raw_response,
        )

    except A2AClientError as exc:
        mapped = _map_a2a_client_error(exc)
        logger.error("Error getting A2A task for %s/%s: %s", agent_id, task_id, exc)

        status_code = int(mapped["status_code"] or 502)
        code = mapped["code"]
        message = mapped["message"]
        upstream = mapped.get("upstream")

        # Deterministic behavior:
        # - message-only agents: polling is not applicable -> 409
        # - task not found -> 404
        if code == "A2A_TASK_NOT_APPLICABLE" or status_code == 409 or _looks_task_polling_not_applicable(
            message, upstream=upstream
        ):
            status_code = 409
            code = "A2A_TASK_NOT_APPLICABLE"
            message = "Task polling is not applicable for this agent"
        elif code == "A2A_TASK_NOT_FOUND" or status_code == 404:
            status_code = 404
            code = "A2A_TASK_NOT_FOUND"
            message = "Task not found"

        _raise_a2a_http_error(
            status_code=status_code,
            code=code,
            message=message,
            agent_id=agent_id,
            task_id=task_id,
            operation="get_task",
            upstream=upstream,
        )

    except Exception as exc:
        logger.exception("Unexpected error getting A2A task for %s/%s: %s", agent_id, task_id, exc)
        _raise_a2a_http_error(
            status_code=500,
            code="A2A_INTERNAL_ERROR",
            message="Error getting A2A task",
            agent_id=agent_id,
            task_id=task_id,
            operation="get_task",
        )
