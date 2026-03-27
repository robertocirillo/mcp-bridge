"""Thin API services backing session routes."""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from fastapi import BackgroundTasks, HTTPException
from pydantic import ValidationError

from app.api.dependencies import TenantContext
from app.api.error_mapping import (
    capability_http_error,
    map_basic_session_error,
    map_capability_error,
)
from app.api.mcp_capabilities import (
    normalize_prompt_list,
    normalize_prompt_render,
    normalize_resource_list,
    normalize_resource_read,
)
from app.api.session_context import get_owned_wrapper, get_tenant_session
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

logger = logging.getLogger(__name__)


def _build_session_response(
    *,
    session_id: str,
    request: SessionCreateRequest,
) -> SessionResponse:
    return SessionResponse(
        session_id=session_id,
        status="created",
        message="Session created successfully",
        servers=list(request.mcp_servers.keys()),
    )


def _build_session_info(session_data: Any) -> SessionInfo:
    return SessionInfo(
        session_id=session_data.session_id,
        status=session_data.status,
        created_at=session_data.created_at,
        last_used=session_data.last_used,
        query_count=session_data.query_count,
        servers=list(session_data.config.mcp_servers.keys()),
        llm_provider=session_data.config.llm_provider.provider,
        llm_model=session_data.config.llm_provider.model,
    )


def _resolved_server_name(wrapper: Any, requested_server_name: Optional[str]) -> str:
    return wrapper.last_server_used or requested_server_name or ""


async def create_session(
    *,
    request: SessionCreateRequest,
    tenant_ctx: TenantContext,
    session_manager: SessionManager,
) -> SessionResponse:
    try:
        session_id = await session_manager.create_session(
            config=request,
            tenant_id=tenant_ctx.tenant_id,
            run_id=tenant_ctx.run_id,
        )
        return _build_session_response(session_id=session_id, request=request)
    except Exception as exc:
        raise map_basic_session_error(exc) from exc


async def list_sessions(
    *,
    tenant_ctx: TenantContext,
    session_manager: SessionManager,
) -> List[SessionInfo]:
    try:
        sessions_data = await session_manager.list_sessions(tenant_id=tenant_ctx.tenant_id)
        return [SessionInfo(**data) for data in sessions_data]
    except Exception as exc:
        raise map_basic_session_error(exc, not_found_status=429) from exc


async def get_session_info(
    *,
    session_id: str,
    tenant_ctx: TenantContext,
    session_manager: SessionManager,
) -> SessionInfo:
    try:
        session_data = await get_tenant_session(
            session_id=session_id,
            tenant_ctx=tenant_ctx,
            session_manager=session_manager,
        )
        return _build_session_info(session_data)
    except Exception as exc:
        raise map_basic_session_error(exc) from exc


async def list_prompts(
    *,
    session_id: str,
    tenant_ctx: TenantContext,
    server_name: Optional[str],
    session_manager: SessionManager,
) -> PromptListResponse:
    operation = "list_prompts"
    try:
        wrapper = await get_owned_wrapper(
            session_id=session_id,
            tenant_ctx=tenant_ctx,
            session_manager=session_manager,
        )
        result = await wrapper.list_prompts(server_name=server_name)
        return PromptListResponse(
            session_id=session_id,
            server_name=_resolved_server_name(wrapper, server_name),
            prompts=normalize_prompt_list(result),
        )
    except Exception as exc:
        raise map_capability_error(
            exc,
            operation=operation,
            tenant_ctx=tenant_ctx,
            session_id=session_id,
        ) from exc


async def render_prompt(
    *,
    session_id: str,
    prompt_name: str,
    request: PromptRenderRequest,
    tenant_ctx: TenantContext,
    session_manager: SessionManager,
) -> PromptRenderResponse:
    operation = "render_prompt"
    try:
        wrapper = await get_owned_wrapper(
            session_id=session_id,
            tenant_ctx=tenant_ctx,
            session_manager=session_manager,
        )
        result = await wrapper.render_prompt(
            prompt_name,
            arguments=request.arguments,
            server_name=request.server_name,
        )
        description, messages = normalize_prompt_render(result)
        return PromptRenderResponse(
            session_id=session_id,
            server_name=_resolved_server_name(wrapper, request.server_name),
            prompt_name=prompt_name,
            description=description,
            messages=messages,
        )
    except Exception as exc:
        raise map_capability_error(
            exc,
            operation=operation,
            tenant_ctx=tenant_ctx,
            session_id=session_id,
            prompt_name=prompt_name,
        ) from exc


async def list_resources(
    *,
    session_id: str,
    tenant_ctx: TenantContext,
    server_name: Optional[str],
    session_manager: SessionManager,
) -> ResourceListResponse:
    operation = "list_resources"
    try:
        wrapper = await get_owned_wrapper(
            session_id=session_id,
            tenant_ctx=tenant_ctx,
            session_manager=session_manager,
        )
        result = await wrapper.list_resources(server_name=server_name)
        return ResourceListResponse(
            session_id=session_id,
            server_name=_resolved_server_name(wrapper, server_name),
            resources=normalize_resource_list(result),
        )
    except Exception as exc:
        raise map_capability_error(
            exc,
            operation=operation,
            tenant_ctx=tenant_ctx,
            session_id=session_id,
        ) from exc


async def read_resource(
    *,
    session_id: str,
    request: ResourceReadRequest,
    tenant_ctx: TenantContext,
    session_manager: SessionManager,
) -> ResourceReadResponse:
    operation = "read_resource"
    try:
        wrapper = await get_owned_wrapper(
            session_id=session_id,
            tenant_ctx=tenant_ctx,
            session_manager=session_manager,
        )
        result = await wrapper.read_resource(
            request.uri,
            server_name=request.server_name,
        )
        try:
            return ResourceReadResponse(
                session_id=session_id,
                server_name=_resolved_server_name(wrapper, request.server_name),
                uri=request.uri,
                contents=normalize_resource_read(result),
            )
        except ValidationError as exc:
            resolved_server_name = _resolved_server_name(wrapper, request.server_name)
            logger.exception(
                "Invalid MCP read_resource payload",
                extra={
                    "session_id": session_id,
                    "server_name": resolved_server_name,
                    "uri": request.uri,
                },
            )
            raise capability_http_error(
                status_code=502,
                code="MCP_UPSTREAM_ERROR",
                message=f"read_resource returned an incompatible payload: {exc}",
                operation=operation,
                tenant_ctx=tenant_ctx,
                session_id=session_id,
                uri=request.uri,
                server_name=resolved_server_name,
            ) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Unhandled exception while serving read_resource",
            extra={
                "session_id": session_id,
                "server_name": request.server_name,
                "uri": request.uri,
            },
        )
        raise map_capability_error(
            exc,
            operation=operation,
            tenant_ctx=tenant_ctx,
            session_id=session_id,
            uri=request.uri,
        ) from exc


async def delete_session(
    *,
    session_id: str,
    background_tasks: BackgroundTasks,
    tenant_ctx: TenantContext,
    session_manager: SessionManager,
) -> dict[str, str]:
    try:
        await get_tenant_session(
            session_id=session_id,
            tenant_ctx=tenant_ctx,
            session_manager=session_manager,
        )
        background_tasks.add_task(
            session_manager.delete_session,
            session_id,
            tenant_ctx.tenant_id,
        )
        return {"message": f"Session {session_id} deleted successfully"}
    except Exception as exc:
        raise map_basic_session_error(exc) from exc
