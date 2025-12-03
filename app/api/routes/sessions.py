"""
Endpoints for managing MCP-Bridge sessions.
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
from typing import List
import logging

from app.models.config import SessionConfig
from app.models.requests import SessionCreateRequest
from app.models.responses import SessionResponse, SessionInfo
from app.core.session_manager import SessionManager
from app.core.exceptions import SessionNotFoundError, MaxSessionsExceededError, ConfigurationError, MCPWrapperError
from app.api.dependencies import get_session_manager

logger = logging.getLogger(__name__)
router = APIRouter()

@router.post("", response_model=SessionResponse)
async def create_session(
    request: SessionCreateRequest,
    session_manager: SessionManager = Depends(get_session_manager)
):
    """New session"""
    try:
        # Parsing and validation of the request
        config = SessionConfig(**request.dict())
        
        # sessione creation
        session_id = await session_manager.create_session(config)
        
        return SessionResponse(
            session_id=session_id,
            status="created",
            message="Sessione creata con successo",
            servers=list(config.mcp_servers.keys())
        )
        
    except MaxSessionsExceededError as e:
        logger.warning(f"Limit exceeded {e}")
        raise HTTPException(status_code=429, detail=str(e))
    except ConfigurationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except MCPWrapperError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal Error")

@router.get("", response_model=List[SessionInfo])
async def list_sessions(
    session_manager: SessionManager = Depends(get_session_manager)
):
    """Active sessions list"""
    try:
        sessions_data = await session_manager.list_sessions()
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
    session_manager: SessionManager = Depends(get_session_manager)
):
    """Got session info by ID"""
    try:
        session_data = await session_manager.get_session(session_id)
        
        return SessionInfo(
            session_id=session_data.session_id,
            status=session_data.status,
            created_at=session_data.created_at,
            last_used=session_data.last_used,
            query_count=session_data.query_count,
            servers=list(session_data.config.mcp_servers.keys()),
            llm_provider=session_data.config.llm_provider.provider,
            llm_model=session_data.config.llm_provider.model
        )

    except SessionNotFoundError as e:
        logger.warning(f"Session not found {e}")
        raise HTTPException(status_code=429, detail=str(e))
    except ConfigurationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except MCPWrapperError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal Error")

@router.delete("/{session_id}")
async def delete_session(
    session_id: str,
    background_tasks: BackgroundTasks,
    session_manager: SessionManager = Depends(get_session_manager)
):
    """Delete a session by ID"""
    try:
        # Aggiunge il cleanup alle task di background
        background_tasks.add_task(session_manager.delete_session, session_id)
        
        return {"message": f"Session {session_id} deleted successfully"}
        
    except SessionNotFoundError as e:
        logger.warning(f"Deleting not found session: {e}")
        raise HTTPException(status_code=404, detail=str(e))
    except ConfigurationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except MCPWrapperError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal Error")