"""
A2A Agents REST endpoints.

These endpoints expose A2A agents (configured in settings.a2a) in a
REST-friendly way for visual builders and other clients.

For now:
- GET /a2a/agents reads from static configuration (settings.a2a.agents)
- POST /a2a/agents/{agent_id}/messages forwards the request to the existing
  A2AClient.execute_task implementation, adapting to the new models
  (A2AMessageRequest / A2AMessageResponse).

Later, the internal implementation will be replaced with a client based on
the official A2A SDK, while keeping these REST contracts stable.
"""

from typing import List, Annotated, Any, Dict
import uuid
from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import (
    get_tenant_context,
    TenantContext,
    get_settings,
    get_a2a_client,
)
from app.core.a2a_client import A2AClient
from app.models.requests import A2AMessageRequest, A2ATaskRequest  # type: ignore[import]
from app.models.responses import (
    A2AAgentSummary,
    A2AMessageResponse,
    A2ATaskStatusResponse,
)
from app.utils.logging import get_logger

router = APIRouter(prefix="/a2a/agents", tags=["A2A Agents"])

logger = get_logger(__name__)

TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]


@router.get("", response_model=List[A2AAgentSummary])
async def list_a2a_agents(
    tenant_ctx: TenantDep,            # noqa: ARG001 - reserved for future per-tenant filtering
    settings = Depends(get_settings),
) -> List[A2AAgentSummary]:
    """
    List configured A2A agents visible to the current tenant.

    For now this endpoint reads only from static configuration:
    settings.a2a.agents

    Later this can be extended to:
    - filter agents per tenant_id
    - fetch and merge dynamic data from A2A Agent Cards via the A2A SDK
    """
    try:
        a2a_settings = settings.a2a
    except AttributeError as e:
        logger.error("A2A settings not configured on Settings: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="A2A settings not configured")

    # Global switch
    if not a2a_settings.enabled:
        return []

    summaries: List[A2AAgentSummary] = []

    for agent_id, conf in a2a_settings.agents.items():
        # Skip disabled agents
        if not getattr(conf, "enabled", True):
            continue

        # Name priority: config label -> agent_id
        name = getattr(conf, "label", None) or agent_id

        description = getattr(conf, "description", None)
        card_url = getattr(conf, "card_url", None)

        # For now we do not parse skills/labels from the Agent Card:
        # these fields can be populated later when we integrate the A2A SDK.
        skills: List[str] = []
        labels: List[str] = []

        summaries.append(
            A2AAgentSummary(
                agent_id=agent_id,
                name=name,
                description=description,
                card_url=card_url,
                skills=skills,
                labels=labels,
            )
        )

    return summaries


@router.post("/{agent_id}/messages", response_model=A2AMessageResponse)
async def send_a2a_message(
    agent_id: str,
    request: A2AMessageRequest,
    tenant_ctx: TenantDep,  # noqa: ARG001 - reserved for future per-tenant logic
    a2a_client: A2AClient = Depends(get_a2a_client),
) -> A2AMessageResponse:
    """
    Send a high-level message (goal + optional input) to a specific A2A agent.

    This endpoint adapts the new A2AMessageRequest to the existing
    A2AClient.send_task API, which was originally designed around
    A2ATaskRequest / A2ATaskResponse.

    Later, A2AClient will be refactored to use the official A2A SDK,
    but this REST contract will remain stable.
    """
    try:
        # Ensure we always have a non-empty task_id for the legacy A2ATaskRequest
        effective_task_id = request.client_task_id or str(uuid.uuid4())

        # Build the legacy A2ATaskRequest model from the new request.
        task_request = A2ATaskRequest(
            goal=request.goal,
            input=request.input,
            task_id=effective_task_id,
            metadata=request.metadata or {},
        )

        # ✅ Call the real client method (it exists)
        task_response = await a2a_client.send_task(agent_id=agent_id, task=task_request)

        mode = "blocking" if request.blocking else "task"

        return A2AMessageResponse(
            mode=mode,
            agent_id=agent_id,
            task_id=task_response.task_id or effective_task_id,
            status=task_response.status,
            output=task_response.output,
            message=task_response.message,
            raw_response=task_response.raw_response,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Error executing A2A message for agent_id=%s: %s",
            agent_id,
            e,
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Error executing A2A message") from e
