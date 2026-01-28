"""
MCP-Use REST API Service - Entry Point
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging

from app.core.session_manager import SessionManager
from app.core.mcp_wrapper import initialize_bias_detector_from_env
from app.api.routes import sessions, queries, health, a2a, guardrails_bias
from app.utils.logging import setup_logging, get_logger
from config import settings

# Setup local logging
setup_logging()
logger = get_logger("main")

# Global session manager
session_manager = SessionManager()

print("DEBUG MULTI TENANCY:", settings.multi_tenancy)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manages the application lifecycle"""
    logger.info("Starting MCP-Use REST API service")
    detector = initialize_bias_detector_from_env()
    logger.info("Bias detector initialized", extra={"detector": detector})
    await session_manager.initialize()
    yield
    logger.info("Shutting down MCP-Use REST API service")
    await session_manager.cleanup_all()

# Create the FastAPI app
app = FastAPI(
    title=settings.API_TITLE,
    description=settings.API_DESCRIPTION,
    version=settings.API_VERSION,
    lifespan=lifespan
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routes
app.include_router(sessions.router, prefix="/sessions", tags=["sessions"])
app.include_router(queries.router, prefix="/sessions", tags=["queries"])
app.include_router(health.router, tags=["health"])
app.include_router(guardrails_bias.router, prefix="/v1/guardrails/bias", tags=["guardrails"])
if settings.a2a.enabled:
    app.include_router(a2a.router)

# Root endpoint
@app.get("/")
async def root():
    """Basic health check endpoint"""
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
