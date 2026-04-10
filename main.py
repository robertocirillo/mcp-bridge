"""
MCP-Use REST API Service - Entry Point
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.dependencies import get_settings
from app.core.sessions.manager import SessionManager
from app.core.runtime.mcp_wrapper import initialize_bias_detector_from_env
from app.api.routes import sessions, queries, health, guardrails_bias
from app.utils.logging import setup_logging, get_logger
from config import Settings, settings

# Setup local logging
setup_logging()
logger = get_logger("main")

# Global session manager
session_manager = SessionManager()

print("DEBUG MULTI TENANCY:", settings.multi_tenancy)

def create_app(app_settings: Settings | None = None) -> FastAPI:
    resolved_settings = app_settings or settings

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

    app = FastAPI(
        title=resolved_settings.API_TITLE,
        description=resolved_settings.API_DESCRIPTION,
        version=resolved_settings.API_VERSION,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(sessions.router, prefix="/sessions", tags=["sessions"])
    app.include_router(queries.router, prefix="/sessions", tags=["queries"])
    app.include_router(health.router, tags=["health"])
    app.include_router(guardrails_bias.router, prefix="/v1/guardrails/bias", tags=["guardrails"])
    if resolved_settings.a2a.enabled:
        from app.api.routes import a2a

        app.include_router(a2a.router)

    if app_settings is not None:
        app.dependency_overrides[get_settings] = lambda: resolved_settings

    @app.get("/")
    async def root():
        """Basic health check endpoint"""
        return {
            "service": resolved_settings.API_TITLE,
            "version": resolved_settings.API_VERSION,
            "status": "online",
            "active_sessions": await session_manager.get_session_count(),
        }

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG
    )
