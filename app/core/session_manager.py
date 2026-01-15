"""
Session Manager to handle active MCP sessions
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
import uuid

from app.core.mcp_wrapper import MCPWrapper
from app.core.exceptions import SessionNotFoundError, MaxSessionsExceededError
from app.models.config import SessionConfig
from config import settings

logger = logging.getLogger(__name__)

class SessionData:
    """Data of an active session"""

    def __init__(self, session_id: str, config: SessionConfig, wrapper: MCPWrapper):
        self.session_id = session_id
        self.config = config
        self.wrapper = wrapper
        self.created_at = datetime.now()
        self.last_used = datetime.now()
        self.status = "active"
        self.query_count = 0

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

        # Multi-tenancy context (optional for now)
        self.tenant_id = tenant_id
        self.last_run_id = last_run_id

    def update_last_used(self):
        """Updates the last used timestamp"""
        self.last_used = datetime.now()

    def is_expired(self) -> bool:
        """Checks if the session has expired"""
        return (datetime.now() - self.last_used).total_seconds() > settings.SESSION_TIMEOUT

    def register_query(self):
        self.query_count += 1
        self.update_last_used()

class SessionManager:
    """Central manager for MCP sessions"""

    def __init__(self):
        self._sessions: Dict[str, SessionData] = {}
        self._cleanup_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    async def initialize(self):
        """Initializes the session manager"""
        logger.info("Initializing Session Manager")
        # Start the automatic cleanup task
        self._cleanup_task = asyncio.create_task(self._cleanup_expired_sessions())

    async def create_session(
            self,
            config: SessionConfig,
            tenant_id: str | None = None,
            run_id: str | None = None,
    ) -> str:
        """
        Create a new MCP session.

        Args:
            config: Session configuration.
            tenant_id: Optional tenant identifier (used for multi-tenancy).
            run_id: Optional run identifier (used for tracing/correlation).

        Returns:
            The created session ID.
        """
        async with self._lock:
            # Check session limit
            if len(self._sessions) >= settings.MAX_ACTIVE_SESSIONS:
                raise MaxSessionsExceededError(f"Reached the maximum limit of {settings.MAX_ACTIVE_SESSIONS} sessions")

            session_id = str(uuid.uuid4())

            try:
                # Create MCP wrapper
                wrapper = MCPWrapper(
                    llm_provider=config.llm_provider.provider,
                    model=config.llm_provider.model,
                    api_key=config.llm_provider.api_key,
                    base_url=config.llm_provider.base_url,
                    temperature=config.llm_provider.temperature or 0.7,
                    max_tokens=config.llm_provider.max_tokens,
                    mcp_servers=self._convert_mcp_servers(config.mcp_servers),
                    max_steps=config.max_steps,
                    verbose=config.verbose,
                    sandbox=config.sandbox,
                    sandbox_options=config.sandbox_options,
                    disallowed_tools=config.disallowed_tools,
                    use_server_manager=config.use_server_manager
                )

                # Set context for guardrails/logging
                wrapper.set_context(tenant_id=tenant_id, run_id=run_id, session_id=session_id)

                # Initialize the wrapper
                await wrapper.initialize()

                # Create session data
                session_data = SessionData(
                    session_id=session_id,
                    config=config,
                    wrapper=wrapper,
                    tenant_id=tenant_id,
                    last_run_id=run_id,
                )

                # Save the session
                self._sessions[session_id] = session_data

                logger.info(f"Session {session_id} successfully created")
                return session_id

            except Exception as e:
                logger.error(f"Error creating session: {e}")
                raise

    async def get_session(
            self,
            session_id: str,
            tenant_id: Optional[str] = None,
    ) -> SessionData:
        """
        Retrieves a session.

        Args:
            session_id: ID of the session.
            tenant_id: Optional tenant identifier. If provided, the session
                       must belong to this tenant.

        Returns:
            Session data.

        Raises:
            SessionNotFoundError:
                - If the session does not exist.
                - Or if it does not belong to the given tenant.
        """
        async with self._lock:
            session_data = self._sessions.get(session_id)
            if session_data is None:
                raise SessionNotFoundError(f"Session {session_id} not found")
            # If tenant_id is specified, enforce tenant ownership
            if tenant_id is not None and session_data.tenant_id != tenant_id:
                # For security, behave as if the session does not exist
                raise SessionNotFoundError(
                    f"Session {session_id} not found for the specified tenant"
                )

            session_data.update_last_used()
            return session_data

    async def delete_session(self, session_id: str, tenant_id: str | None = None):
        """
        Deletes a session

        Args:
            session_id: ID of the session to delete
            tenant_id: Optional tenant identifier. If provided, the session
                       must belong to this tenant or a SessionNotFoundError
                       will be raised (to preserve tenant isolation).
        Raises:
            SessionNotFoundError: If the session does not exist  or does not
                                  belong to the given tenant.
        """
        if session_id not in self._sessions:
            raise SessionNotFoundError(f"Session {session_id} not found")

        async with self._lock:
            session_data = self._sessions[session_id]
            if tenant_id is not None and session_data.tenant_id != tenant_id:
                raise SessionNotFoundError(f"Session {session_id} not found")
            # Close the wrapper
            try:
                await session_data.wrapper.close()
            except Exception as e:
                logger.warning(f"Error closing wrapper for session {session_id}: {e}")

            # Remove the session
            del self._sessions[session_id]
            logger.info(
                "Session %s deleted (tenant_id=%s)",
                session_id,
                session_data.tenant_id,
            )

    async def list_sessions(
        self,
        tenant_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Lists active sessions.

        If tenant_id is provided, only sessions that belong to that tenant
        are returned. If tenant_id is None, all sessions are returned.
        """
        sessions: List[Dict[str, Any]] = []

        for session_data in self._sessions.values():
            # If tenant_id is specified, filter by tenant
            if tenant_id is not None and session_data.tenant_id != tenant_id:
                continue

            sessions.append({
                "session_id": session_data.session_id,
                "status": session_data.status,
                "created_at": session_data.created_at,
                "last_used": session_data.last_used,
                "query_count": session_data.query_count,
                "servers": list(session_data.config.mcp_servers.keys()),
                "llm_provider": session_data.config.llm_provider.provider,
                "llm_model": session_data.config.llm_provider.model,
            })

        return sessions


    async def get_session_count(self) -> int:
        """Returns the number of active sessions"""
        return len(self._sessions)

    async def cleanup_all(self):
        """Cleans up all sessions"""
        if self._cleanup_task:
            self._cleanup_task.cancel()

        session_ids = list(self._sessions.keys())
        for session_id in session_ids:
            try:
                await self.delete_session(session_id)
            except Exception as e:
                logger.error(f"Error cleaning up session {session_id}: {e}")

        logger.info("Cleanup completed for all sessions")

    async def _cleanup_expired_sessions(self):
        """Automatic cleanup task for expired sessions"""
        while True:
            try:
                expired_sessions = []

                # Identify expired sessions
                for session_id, session_data in self._sessions.items():
                    if session_data.is_expired():
                        expired_sessions.append(session_id)

                # Delete expired sessions
                for session_id in expired_sessions:
                    try:
                        await self.delete_session(session_id)
                        logger.info(f"Expired session {session_id} automatically deleted")
                    except Exception as e:
                        logger.error(f"Error in automatic cleanup of session {session_id}: {e}")

                # Wait before next check
                await asyncio.sleep(300)  # 5 minutes

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in cleanup task: {e}")
                await asyncio.sleep(60)  # Retry in 1 minute

    @staticmethod
    def _convert_mcp_servers(servers) -> Dict[str, Dict[str, Any]]:
        """Converts server configuration from API format to wrapper format"""
        mcp_servers = {}

        for name, config in servers.items():
            server_config = {}

            if config.url:
                server_config["url"] = config.url
            else:
                if config.command:
                    server_config["command"] = config.command
                if config.args:
                    server_config["args"] = config.args
                if config.env:
                    server_config["env"] = config.env

            mcp_servers[name] = server_config

        return mcp_servers
