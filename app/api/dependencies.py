"""
Dipendenze per FastAPI
"""

from functools import lru_cache
from app.core.session_manager import SessionManager

# Session manager singleton
_session_manager: SessionManager = None

def get_session_manager() -> SessionManager:
    """Dependency injection per il session manager"""
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager

@lru_cache()
def get_settings():
    """Dependency injection per le settings (cached)"""
    from config import settings
    return settings