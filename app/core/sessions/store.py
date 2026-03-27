"""
Session storage primitives used by SessionManager.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.exceptions import SessionNotFoundError
from app.core.runtime.mcp_wrapper import MCPWrapper
from app.models.config import SessionConfig
from config import settings


class SessionData:
    """Data of an active session."""

    def __init__(
        self,
        session_id: str,
        config: SessionConfig,
        wrapper: MCPWrapper,
        tenant_id: Optional[str] = None,
        last_run_id: Optional[str] = None,
    ):
        self.session_id = session_id
        self.config = config
        self.wrapper = wrapper
        self.created_at = datetime.now()
        self.last_used = datetime.now()
        self.status = "active"
        self.query_count = 0
        self.tenant_id = tenant_id
        self.last_run_id = last_run_id

    def update_last_used(self):
        """Updates the last used timestamp."""
        self.last_used = datetime.now()

    def is_expired(self) -> bool:
        """Checks if the session has expired."""
        return (datetime.now() - self.last_used).total_seconds() > settings.SESSION_TIMEOUT

    def register_query(self):
        self.query_count += 1
        self.update_last_used()


class SessionStore:
    """In-memory registry for active sessions."""

    def __init__(self):
        self.sessions: Dict[str, SessionData] = {}

    def add(self, session_data: SessionData) -> None:
        self.sessions[session_data.session_id] = session_data

    def get(
        self,
        session_id: str,
        tenant_id: Optional[str] = None,
        *,
        touch: bool = True,
    ) -> SessionData:
        session_data = self.sessions.get(session_id)
        if session_data is None:
            raise SessionNotFoundError(f"Session {session_id} not found")
        if tenant_id is not None and session_data.tenant_id != tenant_id:
            raise SessionNotFoundError(
                f"Session {session_id} not found for the specified tenant"
            )
        if touch:
            session_data.update_last_used()
        return session_data

    def remove(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)

    def count(self) -> int:
        return len(self.sessions)

    def list_sessions(self, tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        sessions: List[Dict[str, Any]] = []
        for session_data in self.sessions.values():
            if tenant_id is not None and session_data.tenant_id != tenant_id:
                continue
            sessions.append(
                {
                    "session_id": session_data.session_id,
                    "status": session_data.status,
                    "created_at": session_data.created_at,
                    "last_used": session_data.last_used,
                    "query_count": session_data.query_count,
                    "servers": list(session_data.config.mcp_servers.keys()),
                    "llm_provider": session_data.config.llm_provider.provider,
                    "llm_model": session_data.config.llm_provider.model,
                }
            )
        return sessions

    def expired_session_ids(self) -> List[str]:
        return [
            session_id
            for session_id, session_data in self.sessions.items()
            if session_data.is_expired()
        ]
