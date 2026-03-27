"""Endpoints for managing MCP-Bridge sessions."""

from typing import Annotated, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Query

from app.api.dependencies import TenantContext, get_session_manager, get_tenant_context
from app.api.services import session_service
from app.core.sessions.manager import SessionManager
from app.models.requests import PromptRenderRequest, ResourceReadRequest, SessionCreateRequest
from app.models.responses import (
    PromptListResponse,
    PromptRenderResponse,
    ResourceListResponse,
    ResourceReadResponse,
    SessionInfo,
    SessionResponse,
)

TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]
router = APIRouter()


@router.post("", response_model=SessionResponse)
async def create_session(
    request: SessionCreateRequest,
    tenant_ctx: TenantDep,
    session_manager: SessionManager = Depends(get_session_manager),
):
    """New session"""
    return await session_service.create_session(
        request=request,
        tenant_ctx=tenant_ctx,
        session_manager=session_manager,
    )


@router.get("", response_model=List[SessionInfo])
async def list_sessions(
    tenant_ctx: TenantDep,
    session_manager: SessionManager = Depends(get_session_manager),
):
    """Active sessions list for the current tenant (or default tenant)."""
    return await session_service.list_sessions(
        tenant_ctx=tenant_ctx,
        session_manager=session_manager,
    )


@router.get("/{session_id}", response_model=SessionInfo)
async def get_session_info(
    session_id: str,
    tenant_ctx: TenantDep,
    session_manager: SessionManager = Depends(get_session_manager),
):
    """Get session info for the current tenant."""
    return await session_service.get_session_info(
        session_id=session_id,
        tenant_ctx=tenant_ctx,
        session_manager=session_manager,
    )


@router.get("/{session_id}/prompts", response_model=PromptListResponse)
async def list_prompts(
    session_id: str,
    tenant_ctx: TenantDep,
    server_name: Optional[str] = Query(
        default=None,
        description="Specific server name to use. Optional only when the session has exactly one MCP server.",
    ),
    session_manager: SessionManager = Depends(get_session_manager),
):
    """List prompts exposed by the selected MCP server."""
    return await session_service.list_prompts(
        session_id=session_id,
        tenant_ctx=tenant_ctx,
        server_name=server_name,
        session_manager=session_manager,
    )


@router.post("/{session_id}/prompts/{prompt_name}/render", response_model=PromptRenderResponse)
async def render_prompt(
    session_id: str,
    prompt_name: str,
    request: PromptRenderRequest,
    tenant_ctx: TenantDep,
    session_manager: SessionManager = Depends(get_session_manager),
):
    """Render/get a prompt from the selected MCP server."""
    return await session_service.render_prompt(
        session_id=session_id,
        prompt_name=prompt_name,
        request=request,
        tenant_ctx=tenant_ctx,
        session_manager=session_manager,
    )


@router.get("/{session_id}/resources", response_model=ResourceListResponse)
async def list_resources(
    session_id: str,
    tenant_ctx: TenantDep,
    server_name: Optional[str] = Query(
        default=None,
        description="Specific server name to use. Optional only when the session has exactly one MCP server.",
    ),
    session_manager: SessionManager = Depends(get_session_manager),
):
    """List resources exposed by the selected MCP server."""
    return await session_service.list_resources(
        session_id=session_id,
        tenant_ctx=tenant_ctx,
        server_name=server_name,
        session_manager=session_manager,
    )


@router.post("/{session_id}/resources/read", response_model=ResourceReadResponse)
async def read_resource(
    session_id: str,
    request: ResourceReadRequest,
    tenant_ctx: TenantDep,
    session_manager: SessionManager = Depends(get_session_manager),
):
    """Read an MCP resource from the selected server."""
    return await session_service.read_resource(
        session_id=session_id,
        request=request,
        tenant_ctx=tenant_ctx,
        session_manager=session_manager,
    )


@router.delete("/{session_id}")
async def delete_session(
    session_id: str,
    background_tasks: BackgroundTasks,
    tenant_ctx: TenantDep,
    session_manager: SessionManager = Depends(get_session_manager),
):
    """Delete a session by ID for the current tenant."""
    return await session_service.delete_session(
        session_id=session_id,
        background_tasks=background_tasks,
        tenant_ctx=tenant_ctx,
        session_manager=session_manager,
    )
