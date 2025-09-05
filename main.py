"""
MCP-Use REST API Service - Entry Point
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging

from app.core.session_manager import SessionManager
from app.api.routes import sessions, queries, health
from app.utils.logging import setup_logging
from config import settings

# Setup logging
setup_logging()
logger = logging.getLogger(__name__)

# Session manager globale
session_manager = SessionManager()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gestisce il ciclo di vita dell'applicazione"""
    logger.info("Avvio del servizio MCP-Use REST API")
    await session_manager.initialize()
    yield
    logger.info("Chiusura del servizio MCP-Use REST API")
    await session_manager.cleanup_all()

# Crea l'app FastAPI
app = FastAPI(
    title=settings.API_TITLE,
    description=settings.API_DESCRIPTION,
    version=settings.API_VERSION,
    lifespan=lifespan
)

# Configurazione CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Registra le routes
app.include_router(sessions.router, prefix="/sessions", tags=["sessions"])
app.include_router(queries.router, prefix="/sessions", tags=["queries"])
app.include_router(health.router, tags=["health"])

# Root endpoint
@app.get("/")
async def root():
    """Endpoint di health check base"""
    return {
        "service": settings.API_TITLE,
        "version": settings.API_VERSION,
        "status": "online",
        "active_sessions": await session_manager.get_session_count()
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG
    )