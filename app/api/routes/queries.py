"""Query endpoints.

This module is part of the MCP REST surface.
Errors must follow the same structured schema used by A2A endpoints:
`detail.code`, `detail.message`, plus optional contextual fields.
"""

from fastapi import APIRouter, Depends
from typing import Annotated
import asyncio
import logging
from datetime import datetime

from app.models.requests import QueryRequest
from app.models.responses import QueryResponse
from app.core.session_manager import SessionManager
from app.core.exceptions import SessionNotFoundError, ConfigurationError, MCPWrapperError
from app.core.mcp_wrapper import MCPToolNotAllowedError, GuardrailViolationError
from app.api.dependencies import get_session_manager, get_tenant_context, TenantContext
from app.api.errors import http_error

TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]
logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/{session_id}/query", response_model=QueryResponse)
async def execute_query(
    session_id: str,
    request: QueryRequest,
    tenant_ctx: TenantDep,
    session_manager: SessionManager = Depends(get_session_manager),
):
    """Execute a query on an existing session."""
    try:
        session_data = await session_manager.get_session(session_id)
        wrapper = session_data.wrapper

        # Refresh context for logs / structured errors (guardrails + tool policy)
        wrapper.set_context(
            tenant_id=tenant_ctx.tenant_id,
            run_id=tenant_ctx.run_id,
            session_id=session_id,
        )

        start_time = asyncio.get_event_loop().time()

        result = await wrapper.run_query(
            query=request.query,
            max_steps=request.max_steps,
            server_name=request.server_name,
        )

        execution_time = asyncio.get_event_loop().time() - start_time

        steps_used = wrapper.steps_used
        server_used = wrapper.last_server_used

        # Get number of mcp servers
        has_mcp_servers = getattr(wrapper, "has_mcp_servers", None)
        if has_mcp_servers is False:
            server_used = None

        return QueryResponse(
            session_id=session_id,
            result=result,
            execution_time=execution_time,
            steps_used=steps_used,
            timestamp=datetime.now(),
            server_used=server_used,
            has_mcp_servers=has_mcp_servers,
        )

    except SessionNotFoundError as e:
        logger.warning("Session not found: %s", e)
        raise http_error(
            404,
            "MCP_SESSION_NOT_FOUND",
            str(e),
            operation="execute_query",
            tenant_id=tenant_ctx.tenant_id,
            run_id=tenant_ctx.run_id,
            session_id=session_id,
        )

    except MCPToolNotAllowedError as e:
        logger.warning(
            "MCP tool blocked by session policy",
            extra={
                "tenant_id": tenant_ctx.tenant_id,
                "run_id": tenant_ctx.run_id,
                "session_id": session_id,
                "tool_name": getattr(e, "tool_name", None),
            },
        )
        raise http_error(
            403,
            "MCP_TOOL_NOT_ALLOWED",
            str(e),
            operation="execute_query",
            tool_name=getattr(e, "tool_name", None),
            tenant_id=tenant_ctx.tenant_id,
            run_id=tenant_ctx.run_id,
            session_id=session_id,
        )

    except GuardrailViolationError as e:
        logger.warning(
            "Guardrail blocked request",
            extra={
                "tenant_id": tenant_ctx.tenant_id,
                "run_id": tenant_ctx.run_id,
                "session_id": session_id,
                "phase": getattr(e, "phase", None),
                "rule": getattr(e, "rule", None),
            },
        )
        raise http_error(
            403,
            getattr(e, "code", "GUARDRAIL_VIOLATION"),
            getattr(e, "message", str(e)),
            operation="execute_query",
            phase=getattr(e, "phase", None),
            rule=getattr(e, "rule", None),
            tenant_id=tenant_ctx.tenant_id,
            run_id=tenant_ctx.run_id,
            session_id=session_id,
            details=getattr(e, "details", {}),
        )

    except ConfigurationError as e:
        raise http_error(
            400,
            "MCP_CONFIGURATION_ERROR",
            str(e),
            operation="execute_query",
            tenant_id=tenant_ctx.tenant_id,
            run_id=tenant_ctx.run_id,
            session_id=session_id,
        )
    except MCPWrapperError as e:
        raise http_error(
            502,
            "MCP_UPSTREAM_ERROR",
            str(e),
            operation="execute_query",
            tenant_id=tenant_ctx.tenant_id,
            run_id=tenant_ctx.run_id,
            session_id=session_id,
        )
    except ValueError as e:
        raise http_error(
            400,
            "MCP_SCHEMA_ERROR",
            str(e),
            operation="execute_query",
            tenant_id=tenant_ctx.tenant_id,
            run_id=tenant_ctx.run_id,
            session_id=session_id,
        )
    except Exception:
        raise http_error(
            500,
            "MCP_INTERNAL_ERROR",
            "Internal Error",
            operation="execute_query",
            tenant_id=tenant_ctx.tenant_id,
            run_id=tenant_ctx.run_id,
            session_id=session_id,
        )


@router.get("/{session_id}/history")
async def get_query_history(
    session_id: str,
    limit: int = 10,
    session_manager: SessionManager = Depends(get_session_manager),
):
    """Return basic query stats for a session.

    NOTE: Detailed history is not implemented yet.
    """
    try:
        session_data = await session_manager.get_session(session_id)

        return {
            "session_id": session_id,
            "total_queries": session_data.query_count,
            "last_used": session_data.last_used,
            "message": "Detailed history not implemented yet",
        }

    except SessionNotFoundError as e:
        logger.warning("Session not found: %s", e)
        raise http_error(
            404,
            "MCP_SESSION_NOT_FOUND",
            str(e),
            operation="get_query_history",
            session_id=session_id,
        )
    except ConfigurationError as e:
        raise http_error(
            400,
            "MCP_CONFIGURATION_ERROR",
            str(e),
            operation="get_query_history",
            session_id=session_id,
        )
    except MCPWrapperError as e:
        raise http_error(
            502,
            "MCP_UPSTREAM_ERROR",
            str(e),
            operation="get_query_history",
            session_id=session_id,
        )
    except Exception:
        raise http_error(
            500,
            "MCP_INTERNAL_ERROR",
            "Internal Error",
            operation="get_query_history",
            session_id=session_id,
        )
