"""
Endpoints per la gestione delle sessioni
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
from typing import List
import logging

from app.models.config import SessionConfig
from app.models.requests import SessionCreateRequest
from app.models.responses import SessionResponse, SessionInfo
from app.core.session_manager import SessionManager
from app.core.exceptions import SessionNotFoundError, MaxSessionsExceededError
from app.api.dependencies import get_session_manager

logger = logging.getLogger(__name__)
router = APIRouter()

@router.post("", response_model=SessionResponse)
async def create_session(
    request: SessionCreateRequest,
    session_manager: SessionManager = Depends(get_session_manager)
):
    """Crea una nuova sessione MCP-Use"""
    try:
        # Converte la richiesta in configurazione
        config = SessionConfig(**request.dict())
        
        # Crea la sessione
        session_id = await session_manager.create_session(config)
        
        return SessionResponse(
            session_id=session_id,
            status="created",
            message="Sessione creata con successo",
            servers=list(config.mcp_servers.keys())
        )
        
    except MaxSessionsExceededError as e:
        logger.warning(f"Limite massimo sessioni raggiunto: {e}")
        raise HTTPException(status_code=429, detail=str(e))
    
    except Exception as e:
        logger.error(f"Errore nella creazione della sessione: {e}")
        raise HTTPException(status_code=500, detail=f"Errore interno: {str(e)}")

@router.get("", response_model=List[SessionInfo])
async def list_sessions(
    session_manager: SessionManager = Depends(get_session_manager)
):
    """Lista tutte le sessioni attive"""
    try:
        sessions_data = await session_manager.list_sessions()
        
        sessions = []
        for data in sessions_data:
            sessions.append(SessionInfo(
                session_id=data["session_id"],
                status=data["status"],
                created_at=data["created_at"],
                last_used=data["last_used"],
                query_count=data["query_count"],
                servers=data["servers"],
                llm_provider=data["llm_provider"],
                llm_model=data["llm_model"]
            ))
        
        return sessions
        
    except Exception as e:
        logger.error(f"Errore nel recupero delle sessioni: {e}")
        raise HTTPException(status_code=500, detail=f"Errore interno: {str(e)}")

@router.get("/{session_id}", response_model=SessionInfo)
async def get_session_info(
    session_id: str,
    session_manager: SessionManager = Depends(get_session_manager)
):
    """Ottiene informazioni su una sessione specifica"""
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
        logger.warning(f"Sessione non trovata: {e}")
        raise HTTPException(status_code=404, detail=str(e))
    
    except Exception as e:
        logger.error(f"Errore nel recupero informazioni sessione {session_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Errore interno: {str(e)}")

@router.delete("/{session_id}")
async def delete_session(
    session_id: str,
    background_tasks: BackgroundTasks,
    session_manager: SessionManager = Depends(get_session_manager)
):
    """Elimina una sessione"""
    try:
        # Aggiunge il cleanup alle task di background
        background_tasks.add_task(session_manager.delete_session, session_id)
        
        return {"message": f"Sessione {session_id} eliminata"}
        
    except SessionNotFoundError as e:
        logger.warning(f"Tentativo di eliminare sessione inesistente: {e}")
        raise HTTPException(status_code=404, detail=str(e))
    
    except Exception as e:
        logger.error(f"Errore nell'eliminazione della sessione {session_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Errore interno: {str(e)}")