from typing import List

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_a2a_client
from app.core.a2a_client import A2AClient, A2AAgentNotFoundError, A2AClientError
from app.models.requests import A2ATaskRequest
from app.models.responses import A2AAgentInfo, A2ATaskResponse
from config import settings

router = APIRouter(prefix="/a2a", tags=["a2a"])


@router.get("/agents", response_model=List[A2AAgentInfo])
async def list_a2a_agents(
    client: A2AClient = Depends(get_a2a_client),
):
    """
    Returns the list of configured A2A agents.

    This endpoint is meant for visual builders and UIs that need to list
    available remote agents to the end user.
    """
    if not settings.a2a.enabled:
        raise HTTPException(status_code=404, detail="A2A support is disabled.")

    return await client.list_agents()


@router.post("/agents/{agent_id}/tasks", response_model=A2ATaskResponse)
async def send_a2a_task(
    agent_id: str,
    request: A2ATaskRequest,
    client: A2AClient = Depends(get_a2a_client),
):
    """
    Forwards a task to the specified remote A2A agent.

    The bridge does not manage task lifecycle; it simply forwards the request
    and returns the remote response wrapped in a stable shape.
    """
    if not settings.a2a.enabled:
        raise HTTPException(status_code=404, detail="A2A support is disabled.")

    try:
        return await client.send_task(agent_id, request)
    except A2AAgentNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except A2AClientError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Internal A2A error")
