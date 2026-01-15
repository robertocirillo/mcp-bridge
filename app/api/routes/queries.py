"""
Endpoints per l'esecuzione delle query
"""

from fastapi import APIRouter, HTTPException, Depends
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
    """Esegue una query su una sessione esistente."""
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
        raise HTTPException(status_code=404, detail=str(e))

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
        raise HTTPException(
            status_code=403,
            detail={
                "code": "MCP_TOOL_NOT_ALLOWED",
                "message": str(e),
                "tool_name": getattr(e, "tool_name", None),
                "tenant_id": tenant_ctx.tenant_id,
                "run_id": tenant_ctx.run_id,
                "session_id": session_id,
            },
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
        raise HTTPException(
            status_code=403,
            detail={
                "code": getattr(e, "code", "GUARDRAIL_VIOLATION"),
                "message": getattr(e, "message", str(e)),
                "phase": getattr(e, "phase", None),
                "rule": getattr(e, "rule", None),
                "tenant_id": tenant_ctx.tenant_id,
                "run_id": tenant_ctx.run_id,
                "session_id": session_id,
                "details": getattr(e, "details", {}),
            },
        )

    except ConfigurationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except MCPWrapperError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Internal Error")


@router.get("/{session_id}/history")
async def get_query_history(
    session_id: str,
    limit: int = 10,
    session_manager: SessionManager = Depends(get_session_manager),
):
    """Ottiene la cronologia delle query per una sessione"""
    try:
        session_data = await session_manager.get_session(session_id)

        # Per ora restituiamo solo le statistiche base
        # In futuro si potrebbe implementare una vera cronologia
        return {
            "session_id": session_id,
            "total_queries": session_data.query_count,
            "last_used": session_data.last_used,
            "message": "Cronologia dettagliata non ancora implementata",
        }

    except SessionNotFoundError as e:
        logger.warning("Attempt failed: %s", e)
        raise HTTPException(status_code=404, detail=str(e))
    except ConfigurationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except MCPWrapperError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Internal Error")
