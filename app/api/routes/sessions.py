"""
Endpoints for managing MCP-Bridge sessions.
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
from typing import List, Annotated
import logging

from app.models.config import SessionConfig
from app.models.requests import SessionCreateRequest
from app.models.responses import SessionResponse, SessionInfo
from app.core.session_manager import SessionManager
from app.core.exceptions import SessionNotFoundError, MaxSessionsExceededError, ConfigurationError, MCPWrapperError
from app.api.dependencies import get_session_manager, get_tenant_context, TenantContext

TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]
logger = logging.getLogger(__name__)
router = APIRouter()

@router.post("", response_model=SessionResponse)
async def create_session(
    request: SessionCreateRequest,
    tenant_ctx: TenantDep,
    session_manager: SessionManager = Depends(get_session_manager),
):
    """New session"""
    try:
        # request is already a SessionConfig (inherits from SessionConfig)
        config = request

        # session creation (SessionManager will be updated to accept tenant_id / run_id)
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
        # Enforce tenant isolation when retrieving the session
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
        # 404: esiste per altri tenant o non esiste proprio
        raise HTTPException(status_code=404, detail=str(e))
    except ConfigurationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except MCPWrapperError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Internal Error")


@router.delete("/{session_id}")
async def delete_session(
    session_id: str,
    background_tasks: BackgroundTasks,
    tenant_ctx: TenantDep,
    session_manager: SessionManager = Depends(get_session_manager),
):
    """Delete a session by ID for the current tenant."""
    try:
        # First, ensure the session exists and belongs to the current tenant.
        # get_session will raise SessionNotFoundError if:
        # - the session does not exist, or
        # - it exists but belongs to a different tenant.
        await session_manager.get_session(
            session_id=session_id,
            tenant_id=tenant_ctx.tenant_id,
        )

        # If we reach here, the session belongs to this tenant.
        # We can safely schedule the cleanup in the background.
        background_tasks.add_task(
            session_manager.delete_session,
            session_id,
            tenant_ctx.tenant_id,
        )

        return {"message": f"Session {session_id} deleted successfully"}

    except SessionNotFoundError as e:
        logger.warning(f"Deleting not found session: {e}")
        # 404 even if the session exists for another tenant (isolation)
        raise HTTPException(status_code=404, detail=str(e))
    except ConfigurationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except MCPWrapperError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Internal Error")
