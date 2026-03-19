"""
Endpoints for managing MCP-Bridge sessions.
"""

import logging
from typing import Any, Annotated, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from app.api.mcp_capabilities import (
    normalize_prompt_list,
    normalize_prompt_render,
    normalize_resource_list,
    normalize_resource_read,
)
from app.api.dependencies import TenantContext, get_session_manager, get_tenant_context
from app.api.errors import http_error
from app.core.exceptions import (
    ConfigurationError,
    MCPCapabilityNotSupportedError,
    MCPCapabilityUpstreamError,
    MCPWrapperError,
    MaxSessionsExceededError,
    SessionNotFoundError,
)
from app.core.session_manager import SessionManager
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
logger = logging.getLogger(__name__)
router = APIRouter()


async def _get_owned_wrapper(
    *,
    session_id: str,
    tenant_ctx: TenantContext,
    session_manager: SessionManager,
):
    session_data = await session_manager.get_session(
        session_id=session_id,
        tenant_id=tenant_ctx.tenant_id,
    )
    wrapper = session_data.wrapper
    wrapper.set_context(
        tenant_id=tenant_ctx.tenant_id,
        run_id=tenant_ctx.run_id,
        session_id=session_id,
    )
    return wrapper


def _capability_error(
    *,
    status_code: int,
    code: str,
    message: str,
    operation: str,
    tenant_ctx: TenantContext,
    session_id: str,
    **extra: Any,
) -> HTTPException:
    return http_error(
        status_code,
        code,
        message,
        operation=operation,
        tenant_id=tenant_ctx.tenant_id,
        run_id=tenant_ctx.run_id,
        session_id=session_id,
        **extra,
    )


@router.post("", response_model=SessionResponse)
async def create_session(
    request: SessionCreateRequest,
    tenant_ctx: TenantDep,
    session_manager: SessionManager = Depends(get_session_manager),
):
    """New session"""
    try:
        config = request

        session_id = await session_manager.create_session(
            config=config,
            tenant_id=tenant_ctx.tenant_id,
            run_id=tenant_ctx.run_id,
        )

        return SessionResponse(
            session_id=session_id,
            status="created",
            message="Session created successfully",
            servers=list(config.mcp_servers.keys()),
        )

    except MaxSessionsExceededError as e:
        logger.warning(f"Limit exceeded {e}")
        raise HTTPException(status_code=429, detail=str(e))
    except ConfigurationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except MCPWrapperError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Internal Error")


@router.get("", response_model=List[SessionInfo])
async def list_sessions(
    tenant_ctx: TenantDep,
    session_manager: SessionManager = Depends(get_session_manager),
):
    """Active sessions list for the current tenant (or default tenant)."""
    try:
        sessions_data = await session_manager.list_sessions(
            tenant_id=tenant_ctx.tenant_id
        )
        return [SessionInfo(**data) for data in sessions_data]

    except SessionNotFoundError as e:
        logger.warning(f"Session not found {e}")
        raise HTTPException(status_code=429, detail=str(e))
    except ConfigurationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except MCPWrapperError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Internal Error")


@router.get("/{session_id}", response_model=SessionInfo)
async def get_session_info(
    session_id: str,
    tenant_ctx: TenantDep,
    session_manager: SessionManager = Depends(get_session_manager),
):
    """Get session info for the current tenant."""
    try:
        session_data = await session_manager.get_session(
            session_id=session_id,
            tenant_id=tenant_ctx.tenant_id,
        )

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

    except SessionNotFoundError as e:
        logger.warning(f"Session not found {e}")
        raise HTTPException(status_code=404, detail=str(e))
    except ConfigurationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except MCPWrapperError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Internal Error")


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
    operation = "list_prompts"
    try:
        wrapper = await _get_owned_wrapper(
            session_id=session_id,
            tenant_ctx=tenant_ctx,
            session_manager=session_manager,
        )
        result = await wrapper.list_prompts(server_name=server_name)
        return PromptListResponse(
            session_id=session_id,
            server_name=wrapper.last_server_used or server_name or "",
            prompts=normalize_prompt_list(result),
        )
    except SessionNotFoundError as e:
        raise _capability_error(
            status_code=404,
            code="MCP_SESSION_NOT_FOUND",
            message=str(e),
            operation=operation,
            tenant_ctx=tenant_ctx,
            session_id=session_id,
        )
    except ConfigurationError as e:
        raise _capability_error(
            status_code=400,
            code="MCP_CONFIGURATION_ERROR",
            message=str(e),
            operation=operation,
            tenant_ctx=tenant_ctx,
            session_id=session_id,
        )
    except MCPCapabilityNotSupportedError as e:
        raise _capability_error(
            status_code=501,
            code="MCP_CAPABILITY_NOT_SUPPORTED",
            message=str(e),
            operation=operation,
            tenant_ctx=tenant_ctx,
            session_id=session_id,
            capability=e.capability,
            server_name=e.server_name,
        )
    except MCPCapabilityUpstreamError as e:
        raise _capability_error(
            status_code=502,
            code="MCP_UPSTREAM_ERROR",
            message=str(e),
            operation=operation,
            tenant_ctx=tenant_ctx,
            session_id=session_id,
            capability=e.capability,
            server_name=e.server_name,
        )
    except MCPWrapperError as e:
        raise _capability_error(
            status_code=502,
            code="MCP_UPSTREAM_ERROR",
            message=str(e),
            operation=operation,
            tenant_ctx=tenant_ctx,
            session_id=session_id,
        )
    except Exception:
        raise _capability_error(
            status_code=500,
            code="MCP_INTERNAL_ERROR",
            message="Internal Error",
            operation=operation,
            tenant_ctx=tenant_ctx,
            session_id=session_id,
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
    operation = "render_prompt"
    try:
        wrapper = await _get_owned_wrapper(
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
            server_name=wrapper.last_server_used or request.server_name or "",
            prompt_name=prompt_name,
            description=description,
            messages=messages,
        )
    except SessionNotFoundError as e:
        raise _capability_error(
            status_code=404,
            code="MCP_SESSION_NOT_FOUND",
            message=str(e),
            operation=operation,
            tenant_ctx=tenant_ctx,
            session_id=session_id,
            prompt_name=prompt_name,
        )
    except ConfigurationError as e:
        raise _capability_error(
            status_code=400,
            code="MCP_CONFIGURATION_ERROR",
            message=str(e),
            operation=operation,
            tenant_ctx=tenant_ctx,
            session_id=session_id,
            prompt_name=prompt_name,
        )
    except MCPCapabilityNotSupportedError as e:
        raise _capability_error(
            status_code=501,
            code="MCP_CAPABILITY_NOT_SUPPORTED",
            message=str(e),
            operation=operation,
            tenant_ctx=tenant_ctx,
            session_id=session_id,
            prompt_name=prompt_name,
            capability=e.capability,
            server_name=e.server_name,
        )
    except MCPCapabilityUpstreamError as e:
        raise _capability_error(
            status_code=502,
            code="MCP_UPSTREAM_ERROR",
            message=str(e),
            operation=operation,
            tenant_ctx=tenant_ctx,
            session_id=session_id,
            prompt_name=prompt_name,
            capability=e.capability,
            server_name=e.server_name,
        )
    except MCPWrapperError as e:
        raise _capability_error(
            status_code=502,
            code="MCP_UPSTREAM_ERROR",
            message=str(e),
            operation=operation,
            tenant_ctx=tenant_ctx,
            session_id=session_id,
            prompt_name=prompt_name,
        )
    except Exception:
        raise _capability_error(
            status_code=500,
            code="MCP_INTERNAL_ERROR",
            message="Internal Error",
            operation=operation,
            tenant_ctx=tenant_ctx,
            session_id=session_id,
            prompt_name=prompt_name,
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
    operation = "list_resources"
    try:
        wrapper = await _get_owned_wrapper(
            session_id=session_id,
            tenant_ctx=tenant_ctx,
            session_manager=session_manager,
        )
        result = await wrapper.list_resources(server_name=server_name)
        return ResourceListResponse(
            session_id=session_id,
            server_name=wrapper.last_server_used or server_name or "",
            resources=normalize_resource_list(result),
        )
    except SessionNotFoundError as e:
        raise _capability_error(
            status_code=404,
            code="MCP_SESSION_NOT_FOUND",
            message=str(e),
            operation=operation,
            tenant_ctx=tenant_ctx,
            session_id=session_id,
        )
    except ConfigurationError as e:
        raise _capability_error(
            status_code=400,
            code="MCP_CONFIGURATION_ERROR",
            message=str(e),
            operation=operation,
            tenant_ctx=tenant_ctx,
            session_id=session_id,
        )
    except MCPCapabilityNotSupportedError as e:
        raise _capability_error(
            status_code=501,
            code="MCP_CAPABILITY_NOT_SUPPORTED",
            message=str(e),
            operation=operation,
            tenant_ctx=tenant_ctx,
            session_id=session_id,
            capability=e.capability,
            server_name=e.server_name,
        )
    except MCPCapabilityUpstreamError as e:
        raise _capability_error(
            status_code=502,
            code="MCP_UPSTREAM_ERROR",
            message=str(e),
            operation=operation,
            tenant_ctx=tenant_ctx,
            session_id=session_id,
            capability=e.capability,
            server_name=e.server_name,
        )
    except MCPWrapperError as e:
        raise _capability_error(
            status_code=502,
            code="MCP_UPSTREAM_ERROR",
            message=str(e),
            operation=operation,
            tenant_ctx=tenant_ctx,
            session_id=session_id,
        )
    except Exception:
        raise _capability_error(
            status_code=500,
            code="MCP_INTERNAL_ERROR",
            message="Internal Error",
            operation=operation,
            tenant_ctx=tenant_ctx,
            session_id=session_id,
        )


@router.post("/{session_id}/resources/read", response_model=ResourceReadResponse)
async def read_resource(
    session_id: str,
    request: ResourceReadRequest,
    tenant_ctx: TenantDep,
    session_manager: SessionManager = Depends(get_session_manager),
):
    """Read an MCP resource from the selected server."""
    operation = "read_resource"
    try:
        wrapper = await _get_owned_wrapper(
            session_id=session_id,
            tenant_ctx=tenant_ctx,
            session_manager=session_manager,
        )
        result = await wrapper.read_resource(
            request.uri,
            server_name=request.server_name,
        )
        return ResourceReadResponse(
            session_id=session_id,
            server_name=wrapper.last_server_used or request.server_name or "",
            uri=request.uri,
            contents=normalize_resource_read(result),
        )
    except SessionNotFoundError as e:
        raise _capability_error(
            status_code=404,
            code="MCP_SESSION_NOT_FOUND",
            message=str(e),
            operation=operation,
            tenant_ctx=tenant_ctx,
            session_id=session_id,
            uri=request.uri,
        )
    except ConfigurationError as e:
        raise _capability_error(
            status_code=400,
            code="MCP_CONFIGURATION_ERROR",
            message=str(e),
            operation=operation,
            tenant_ctx=tenant_ctx,
            session_id=session_id,
            uri=request.uri,
        )
    except MCPCapabilityNotSupportedError as e:
        raise _capability_error(
            status_code=501,
            code="MCP_CAPABILITY_NOT_SUPPORTED",
            message=str(e),
            operation=operation,
            tenant_ctx=tenant_ctx,
            session_id=session_id,
            uri=request.uri,
            capability=e.capability,
            server_name=e.server_name,
        )
    except MCPCapabilityUpstreamError as e:
        raise _capability_error(
            status_code=502,
            code="MCP_UPSTREAM_ERROR",
            message=str(e),
            operation=operation,
            tenant_ctx=tenant_ctx,
            session_id=session_id,
            uri=request.uri,
            capability=e.capability,
            server_name=e.server_name,
        )
    except MCPWrapperError as e:
        raise _capability_error(
            status_code=502,
            code="MCP_UPSTREAM_ERROR",
            message=str(e),
            operation=operation,
            tenant_ctx=tenant_ctx,
            session_id=session_id,
            uri=request.uri,
        )
    except Exception:
        raise _capability_error(
            status_code=500,
            code="MCP_INTERNAL_ERROR",
            message="Internal Error",
            operation=operation,
            tenant_ctx=tenant_ctx,
            session_id=session_id,
            uri=request.uri,
        )


@router.delete("/{session_id}")
async def delete_session(
    session_id: str,
    background_tasks: BackgroundTasks,
    tenant_ctx: TenantDep,
    session_manager: SessionManager = Depends(get_session_manager),
):
    """Delete a session by ID for the current tenant."""
    try:
        await session_manager.get_session(
            session_id=session_id,
            tenant_id=tenant_ctx.tenant_id,
        )

        background_tasks.add_task(
            session_manager.delete_session,
            session_id,
            tenant_ctx.tenant_id,
        )

        return {"message": f"Session {session_id} deleted successfully"}

    except SessionNotFoundError as e:
        logger.warning(f"Deleting not found session: {e}")
        raise HTTPException(status_code=404, detail=str(e))
    except ConfigurationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except MCPWrapperError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Internal Error")
