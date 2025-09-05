"""
Endpoints per health check e monitoraggio
"""

from fastapi import APIRouter, Depends
from datetime import datetime
import logging

from app.models.responses import HealthResponse, SessionStatsResponse
from app.core.session_manager import SessionManager
from app.api.dependencies import get_session_manager, get_settings
from config import Settings

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/health", response_model=HealthResponse)
async def health_check(
    session_manager: SessionManager = Depends(get_session_manager),
    settings: Settings = Depends(get_settings)
):
    """Health check dettagliato"""
    try:
        active_sessions = await session_manager.get_session_count()
        
        return HealthResponse(
            status="healthy",
            timestamp=datetime.now(),
            active_sessions=active_sessions,
            supported_providers=settings.SUPPORTED_PROVIDERS,
            features={
                "sandbox_support": True,
                "multi_server_support": True,
                "streaming": False,  # Implementazione futura
                "session_management": True,
                "auto_cleanup": True,
                "background_tasks": True
            }
        )
        
    except Exception as e:
        logger.error(f"Errore nel health check: {e}")
        return HealthResponse(
            status="unhealthy",
            timestamp=datetime.now(),
            active_sessions=0,
            supported_providers=[],
            features={}
        )

@router.get("/stats", response_model=SessionStatsResponse)
async def get_stats(
    session_manager: SessionManager = Depends(get_session_manager)
):
    """Ottiene statistiche del servizio"""
    try:
        sessions_data = await session_manager.list_sessions()
        
        total_queries = sum(session["query_count"] for session in sessions_data)
        
        # Calcola statistiche provider
        providers_usage = {}
        for session in sessions_data:
            provider = session["llm_provider"]
            providers_usage[provider] = providers_usage.get(provider, 0) + 1
        
        # Calcolo tempo medio (placeholder - serve implementazione cronologia)
        avg_execution_time = 0.0  # Da implementare con vera cronologia
        
        return SessionStatsResponse(
            total_sessions=len(sessions_data),  # Attualmente solo sessioni attive
            active_sessions=len(sessions_data),
            total_queries=total_queries,
            avg_execution_time=avg_execution_time,
            providers_usage=providers_usage
        )
        
    except Exception as e:
        logger.error(f"Errore nel recupero statistiche: {e}")
        raise HTTPException(status_code=500, detail=f"Errore interno: {str(e)}")

@router.get("/version")
async def get_version(settings: Settings = Depends(get_settings)):
    """Ottiene informazioni sulla versione"""
    return {
        "version": settings.API_VERSION,
        "title": settings.API_TITLE,
        "description": settings.API_DESCRIPTION
    }