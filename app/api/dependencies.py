"""
FastAPI dependencies
"""

from functools import lru_cache
from typing import Dict

from app.core.session_manager import SessionManager
from fastapi import Depends
from config import Settings
from app.core.a2a_client import A2AClient
from app.models.config import A2AAgentConfig

# Session manager singleton
_session_manager: SessionManager = None

def get_session_manager() -> SessionManager:
    """Dependency injection session manager"""
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager

@lru_cache()
def get_settings():
    """Dependency injection settings (cached)"""
    from config import settings
    return settings


def get_a2a_client(
    settings: Settings = Depends(get_settings),
) -> A2AClient:
    """
    Dependency that provides a configured A2AClient instance.
    """

    agent_configs: Dict[str, A2AAgentConfig] = settings.a2a.agents or {}
    return A2AClient(agent_configs=agent_configs)