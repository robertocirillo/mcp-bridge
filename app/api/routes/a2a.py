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
import httpx
from fastapi import APIRouter, Depends, HTTPException
from os import getenv
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
from config import Settings

router = APIRouter(prefix="/a2a/agents", tags=["A2A Agents"])

logger = get_logger(__name__)

TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]


def _build_a2a_headers(conf) -> Dict[str, str]:
    """Build outbound headers for the A2A HTTP shim from config."""
    headers: Dict[str, str] = dict(conf.extra_headers or {})

    auth = conf.auth
    if not auth or auth.type == "none":
        return headers

    if not auth.env_var:
        raise HTTPException(status_code=500, detail="A2A auth is configured but env_var is missing")

    token = getenv(auth.env_var)
    if not token:
        raise HTTPException(
            status_code=500,
            detail=f"A2A auth token is missing: environment variable '{auth.env_var}' is not set",
        )

    if auth.type == "api_key_header":
        header_name = auth.header_name or "X-API-Key"
        headers[header_name] = token
    elif auth.type == "bearer_token":
        headers["Authorization"] = f"Bearer {token}"
    else:
        raise HTTPException(status_code=500, detail=f"Unsupported A2A auth type: {auth.type}")

    return headers


def _get_agent_conf(settings: Settings, agent_id: str):
    a2a_settings = settings.a2a
    if not a2a_settings.enabled:
        raise HTTPException(status_code=404, detail="A2A is disabled")

    conf = (a2a_settings.agents or {}).get(agent_id)
    if not conf or not conf.enabled:
        raise HTTPException(status_code=404, detail=f"Unknown or disabled agent_id: {agent_id}")

    if not conf.runtime_url:
        raise HTTPException(
            status_code=400,
            detail=f"Agent '{agent_id}' has no runtime_url configured (HTTP shim requires runtime_url)",
        )

    return conf


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


@router.get("/{agent_id}/tasks/{task_id}", response_model=A2ATaskStatusResponse)
async def get_a2a_task_status(
    agent_id: str,
    task_id: str,
    settings: Settings = Depends(get_settings),
):
    """Return task status via the current HTTP shim.

    It attempts GET {runtime_url}/tasks/{task_id}.
    If the agent runtime does not implement task polling, returns a graceful shim response.
    """
    conf = _get_agent_conf(settings, agent_id)

    tasks_get_url = conf.runtime_url.rstrip("/") + f"/tasks/{task_id}"
    headers = _build_a2a_headers(conf)

    timeout = httpx.Timeout(conf.timeout_seconds)

    try:
        async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
            resp = await client.get(tasks_get_url)

        # Some shim agents (like the echo agent) won't have this endpoint.
        if resp.status_code in (404, 405, 501):
            status = (
                "not_found" if resp.status_code == 404 else
                "unsupported" if resp.status_code in (405, 501) else
                "unknown"
            )
            return A2ATaskStatusResponse(
                agent_id=agent_id,
                task_id=task_id,
                status=status,
                output=None,
                message=(
                    f"Task polling is not available via HTTP shim at GET {tasks_get_url} "
                    f"(agent returned HTTP {resp.status_code})."
                ),
                raw_response={
                    "status_code": resp.status_code,
                    "body": resp.text,
                },
            )

        resp.raise_for_status()

        data: Dict[str, Any] = resp.json()

        return A2ATaskStatusResponse(
            agent_id=agent_id,
            task_id=data.get("taskId", task_id),
            status=data.get("status", "unknown"),
            output=data.get("output"),
            message=data.get("message"),
            raw_response=data,
        )

    except httpx.HTTPStatusError as e:
        # propagate remote HTTP status code
        body = None
        try:
            body = e.response.text
        except Exception:
            body = None

        logger.warning("A2A task status error for %s/%s: %s", agent_id, task_id, e)
        raise HTTPException(
            status_code=e.response.status_code,
            detail={
                "error": "A2A agent returned an error",
                "agent_id": agent_id,
                "task_id": task_id,
                "status_code": e.response.status_code,
                "body": body,
            },
        )
    except httpx.RequestError as e:
        logger.warning("A2A task status request error for %s/%s: %s", agent_id, task_id, e)
        raise HTTPException(
            status_code=502,
            detail={
                "error": "Error contacting A2A agent runtime",
                "agent_id": agent_id,
                "task_id": task_id,
                "message": str(e),
            },
        )
    except Exception as e:
        logger.exception("Unexpected error getting A2A task status for %s/%s: %s", agent_id, task_id, e)
        raise HTTPException(status_code=500, detail="Internal Error")
