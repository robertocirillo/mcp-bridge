"""A2A REST routes.

These endpoints expose a stable REST surface for UIs / visual builders.
Internally we use the official `a2a-sdk` via `app.core.a2a_client.A2AClient`.

IMPORTANT:
- `blocking` is a REST convenience flag, not part of the A2A protocol.
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_a2a_client, get_settings
from app.core.a2a_client import A2AClient, A2AClientError
from app.models.requests import A2AMessageRequest
from app.models.responses import A2AAgentSummary, A2AMessageResponse, A2ATaskStatusResponse
from app.utils.logging import get_logger
from config import Settings

router = APIRouter(prefix="/a2a", tags=["a2a"])
logger = get_logger(__name__)


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

    mode = "blocking" if request.blocking else "task"

    try:
        result = await a2a_client.send_message(
            agent_id=agent_id,
            text=request.goal,
            blocking=request.blocking,
            request_metadata=request.metadata,
        )

        return A2AMessageResponse(
            mode=mode,
            agent_id=agent_id,
            task_id=result.task_id,
            status=result.status,
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
