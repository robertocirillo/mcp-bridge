"""A2A REST routes.

These endpoints expose a stable REST surface for UIs / visual builders.
Internally we use the official `a2a-sdk` via `app.core.a2a_client.A2AClient`.

IMPORTANT:
- `blocking` is a REST convenience flag, not part of the A2A protocol.
"""

from __future__ import annotations

from typing import List, Literal

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_a2a_client, get_settings
from app.core.a2a_client import A2AClient, A2AClientError
from app.models.requests import A2AMessageRequest
from app.models.responses import A2AAgentSummary, A2AMessageResponse, A2ATaskStatusResponse
from app.utils.logging import get_logger
from config import Settings

import json



router = APIRouter(prefix="/a2a", tags=["a2a"])
logger = get_logger(__name__)


def _normalize_goal(goal: str) -> str:
    if goal is None:
        raise HTTPException(status_code=422, detail="Field 'goal' is required")
    g = goal.strip()
    if not g:
        raise HTTPException(status_code=422, detail="Field 'goal' must be a non-empty string")
    return g

def _ensure_jsonable(value, field_name: str) -> None:
    try:
        json.dumps(value)
    except Exception:
        raise HTTPException(status_code=422, detail=f"Field '{field_name}' must be JSON-serializable")

def _normalize_task_id(task_id):
    if task_id is None:
        return None
    if isinstance(task_id, str):
        t = task_id.strip()
        return t or None
    return task_id



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
            if not conf.enabled:
                continue

            summaries.append(
                A2AAgentSummary(
                    agent_id=agent_id,
                    name=conf.label or agent_id,
                    description=conf.description,
                    card_url=conf.card_url,
                    skills=[],
                    labels=[],
                )
            )

        return summaries

    except Exception as exc:
        logger.exception("Error listing A2A agents: %s", exc)
        raise HTTPException(status_code=500, detail="Error listing A2A agents")


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

    a2a_settings = settings.a2a
    if not a2a_settings.enabled:
        raise HTTPException(status_code=400, detail="A2A integration is disabled")

    conf = (a2a_settings.agents or {}).get(agent_id)
    if conf is None or not conf.enabled:
        raise HTTPException(status_code=404, detail=f"Unknown or disabled agent_id: {agent_id}")
    goal = _normalize_goal(request.goal)
    if request.metadata is not None:
        _ensure_jsonable(request.metadata, "metadata")

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
        return A2AMessageResponse(
            mode=effective_mode,
            agent_id=agent_id,
            task_id=task_id,
            status=result.status or "unknown",
            output=result.output,
            message=result.message,
            raw_response=result.raw_response,
        )

    except A2AClientError as exc:
        logger.error("Error executing A2A message for %s: %s", agent_id, exc)
        raise HTTPException(status_code=502, detail=str(exc))

    except Exception as exc:
        logger.exception("Unexpected error executing A2A message for %s: %s", agent_id, exc)
        raise HTTPException(status_code=500, detail="Error executing A2A message")


@router.get("/agents/{agent_id}/tasks/{task_id}", response_model=A2ATaskStatusResponse)
async def get_a2a_task(
    agent_id: str,
    task_id: str,
    settings: Settings = Depends(get_settings),
    a2a_client: A2AClient = Depends(get_a2a_client),
) -> A2ATaskStatusResponse:
    a2a_settings = settings.a2a
    if not a2a_settings.enabled:
        raise HTTPException(status_code=400, detail="A2A integration is disabled")

    conf = (a2a_settings.agents or {}).get(agent_id)
    if conf is None or not conf.enabled:
        raise HTTPException(status_code=404, detail=f"Unknown or disabled agent_id: {agent_id}")

    try:
        result = await a2a_client.get_task(agent_id=agent_id, task_id=task_id)

        return A2ATaskStatusResponse(
            agent_id=agent_id,
            task_id=task_id,
            status=result.status or "unknown",
            output=result.output,
            message=result.message,
            raw_response=result.raw_response,
        )

    except A2AClientError as exc:
        logger.error("Error getting A2A task for %s/%s: %s", agent_id, task_id, exc)
        raise HTTPException(status_code=502, detail=str(exc))

    except Exception as exc:
        logger.exception("Unexpected error getting A2A task for %s/%s: %s", agent_id, task_id, exc)
        raise HTTPException(status_code=500, detail="Error getting A2A task")
