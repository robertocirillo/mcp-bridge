"""
Session Manager to handle active MCP sessions
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi.encoders import jsonable_encoder

from app.core.mcp_wrapper import MCPWrapper
from app.core.exceptions import (
    ConfigurationError,
    MaxSessionsExceededError,
    QueryOperationElicitationDeclinedError,
    QueryOperationElicitationExpiredError,
    QueryOperationElicitationUnavailableError,
    QueryOperationNotFoundError,
    QueryOperationResumeInvalidError,
    SessionNotFoundError,
)
from app.core.mcp_wrapper import MCPToolNotAllowedError, GuardrailViolationError
from app.models.config import SessionConfig
from app.models.requests import QueryOperationCreateRequest, QueryOperationResumeRequest
from app.models.responses import (
    QueryOperationError,
    QueryOperationInput,
    QueryOperationInteraction,
    QueryOperationMetadata,
    QueryOperationResponse,
    QueryOperationResult,
    QueryOperationStatus,
    QueryOperationToolInput,
)
from config import settings

logger = logging.getLogger(__name__)


@dataclass
class PendingElicitation:
    """In-memory continuation for a paused query operation."""

    interaction_id: str
    future: asyncio.Future
    created_at: datetime


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
        self._query_operations: Dict[str, Dict[str, QueryOperationResponse]] = {}
        self._query_operation_tasks: Dict[str, Dict[str, asyncio.Task]] = {}
        self._pending_elicitations: Dict[str, Dict[str, PendingElicitation]] = {}
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
                self._wire_wrapper_elicitation_handler(wrapper)

                # -----------------------------
                # Session-scoped guardrails
                # -----------------------------
                # Global switch: enable/disable all guardrails for this session.
                guardrails_cfg = getattr(config, "guardrails", None)
                if guardrails_cfg is not None:
                    try:
                        enabled = getattr(guardrails_cfg, "enabled", True)
                        if hasattr(wrapper, "set_guardrails_enabled"):
                            wrapper.set_guardrails_enabled(bool(enabled))

                        # PII resolution (Strategy 3):
                        # - `mode` is a shared default for both input/output when explicitly provided.
                        # - `input_mode` / `output_mode` are per-phase overrides.
                        pii_cfg = getattr(guardrails_cfg, "pii", None)
                        if pii_cfg is not None and enabled:
                            fields_set = getattr(pii_cfg, "model_fields_set", set()) or set()

                            shared_mode = getattr(pii_cfg, "mode", None)
                            input_mode = getattr(pii_cfg, "input_mode", None)
                            output_mode = getattr(pii_cfg, "output_mode", None)

                            # Input effective mode
                            if "input_mode" in fields_set:
                                effective_input_mode = input_mode
                            elif "mode" in fields_set and shared_mode is not None:
                                effective_input_mode = shared_mode
                            else:
                                # Use default (security-default: block)
                                effective_input_mode = input_mode

                            # Output effective mode
                            if "output_mode" in fields_set and output_mode is not None:
                                effective_output_mode = output_mode
                            elif "mode" in fields_set and shared_mode is not None:
                                effective_output_mode = shared_mode
                            else:
                                # Use default (backward-compatible: redact)
                                effective_output_mode = shared_mode

                            if hasattr(wrapper, "set_pii_input_mode"):
                                wrapper.set_pii_input_mode(effective_input_mode)
                            if hasattr(wrapper, "set_pii_mode"):
                                wrapper.set_pii_mode(effective_output_mode)

                        # Bias resolution (Strategy 3, MVP0: after_model only):
                        # - `mode` is a shared default.
                        # - `output_mode` is a phase-specific override for after_model.
                        bias_cfg = getattr(guardrails_cfg, "bias", None)
                        if bias_cfg is not None and enabled:
                            fields_set = getattr(bias_cfg, "model_fields_set", set()) or set()

                            shared_mode = getattr(bias_cfg, "mode", None)
                            output_mode = getattr(bias_cfg, "output_mode", None)

                            if "output_mode" in fields_set and output_mode is not None:
                                effective_bias_output_mode = output_mode
                            elif "mode" in fields_set and shared_mode is not None:
                                effective_bias_output_mode = shared_mode
                            else:
                                # Default (MVP0) is 'off'
                                effective_bias_output_mode = shared_mode

                            # Prefer bias-detector-service integration when supported by the wrapper.
                            # Fallback to legacy set_bias_mode (built-in detectors).
                            if hasattr(wrapper, "set_bias_settings"):
                                wrapper.set_bias_settings(
                                    mode=effective_bias_output_mode,
                                    base_url=getattr(bias_cfg, "base_url", None),
                                    timeout_seconds=getattr(bias_cfg, "timeout_seconds", 5.0),
                                    threshold=getattr(bias_cfg, "threshold", 0.5),
                                    top_k=getattr(bias_cfg, "top_k", 5),
                                    return_all_scores=getattr(bias_cfg, "return_all_scores", False),
                                    return_char_spans=getattr(bias_cfg, "return_char_spans", False),
                                    active_categories=getattr(bias_cfg, "active_categories", None),
                                    unsafe_labels=getattr(bias_cfg, "unsafe_labels", None),
                                    model_id=getattr(bias_cfg, "model_id", None),
                                    revision=getattr(bias_cfg, "revision", None),
                                    checks=getattr(bias_cfg, "checks", None),
                                )
                            elif hasattr(wrapper, "set_bias_mode"):
                                wrapper.set_bias_mode(effective_bias_output_mode)
                    except Exception as e:
                        raise ConfigurationError(f"Invalid guardrails configuration: {e}")

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
            pending_elicitations = list(self._pending_elicitations.pop(session_id, {}).values())
            operation_tasks = list(self._query_operation_tasks.pop(session_id, {}).values())
            self._query_operations.pop(session_id, None)

        for pending in pending_elicitations:
            if not pending.future.done():
                pending.future.cancel()
        for task in operation_tasks:
            if not task.done():
                task.cancel()
        if operation_tasks:
            await asyncio.gather(*operation_tasks, return_exceptions=True)

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

    async def create_query_operation(
        self,
        session_id: str,
        request: QueryOperationCreateRequest,
        tenant_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> QueryOperationResponse:
        """Create and schedule an asynchronous query operation for a session."""
        await self.get_session(session_id=session_id, tenant_id=tenant_id)

        operation_id = str(uuid.uuid4())
        timestamp = datetime.now()
        operation = QueryOperationResponse(
            operation_id=operation_id,
            session_id=session_id,
            status=QueryOperationStatus.queued,
            metadata=QueryOperationMetadata(
                created_at=timestamp,
                updated_at=timestamp,
                request=self._build_query_operation_input(request),
            ),
        )

        async with self._lock:
            self._query_operations.setdefault(session_id, {})[operation_id] = operation

        initial_response = operation.model_copy(deep=True)

        task = asyncio.create_task(
            self._run_query_operation(
                session_id=session_id,
                operation_id=operation_id,
                tenant_id=tenant_id,
                run_id=run_id,
            )
        )

        async with self._lock:
            if session_id in self._sessions and operation_id in self._query_operations.get(session_id, {}):
                self._query_operation_tasks.setdefault(session_id, {})[operation_id] = task
            else:
                task.cancel()

        return initial_response

    async def get_query_operation(
        self,
        session_id: str,
        operation_id: str,
        tenant_id: Optional[str] = None,
    ) -> QueryOperationResponse:
        """Return the current state of an asynchronous query operation."""
        await self.get_session(session_id=session_id, tenant_id=tenant_id)

        async with self._lock:
            operation = self._query_operations.get(session_id, {}).get(operation_id)
            if operation is None:
                raise QueryOperationNotFoundError(
                    f"Query operation {operation_id} not found for session {session_id}"
                )
            return operation.model_copy(deep=True)

    async def resume_query_operation(
        self,
        *,
        session_id: str,
        operation_id: str,
        request: QueryOperationResumeRequest,
        tenant_id: Optional[str] = None,
    ) -> QueryOperationResponse:
        """Resume a paused query operation waiting on elicitation."""
        await self.get_session(session_id=session_id, tenant_id=tenant_id)

        pending: PendingElicitation | None = None
        task: asyncio.Task | None = None

        async with self._lock:
            operation = self._query_operations.get(session_id, {}).get(operation_id)
            if operation is None:
                raise QueryOperationNotFoundError(
                    f"Query operation {operation_id} not found for session {session_id}"
                )

            pending = self._pending_elicitations.get(session_id, {}).get(operation_id)
            interaction = operation.pending_interaction
            if pending is None or interaction is None or not operation.requires_input:
                if operation.status in {
                    QueryOperationStatus.completed,
                    QueryOperationStatus.failed,
                    QueryOperationStatus.cancelled,
                }:
                    raise QueryOperationElicitationExpiredError(
                        f"Elicitation is no longer available for query operation {operation_id}"
                    )
                raise QueryOperationElicitationUnavailableError(
                    f"No pending elicitation is available for query operation {operation_id}"
                )

            self._validate_resume_request(request=request, interaction_id=interaction.interaction_id)

            self._pending_elicitations.get(session_id, {}).pop(operation_id, None)
            if not self._pending_elicitations.get(session_id):
                self._pending_elicitations.pop(session_id, None)

            operation.requires_input = False
            operation.pending_interaction = None
            operation.metadata.updated_at = datetime.now()

            if request.action in {"accept", "decline"}:
                operation.status = QueryOperationStatus.running

            task = self._query_operation_tasks.get(session_id, {}).get(operation_id)

        if pending is None:
            raise QueryOperationElicitationExpiredError(
                f"Elicitation is no longer available for query operation {operation_id}"
            )

        if request.action == "cancel":
            if not pending.future.done():
                pending.future.cancel()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
        else:
            if not pending.future.done():
                pending.future.set_result(request)
            if request.action == "decline" and task is not None:
                await asyncio.gather(task, return_exceptions=True)
            elif request.action == "accept":
                await asyncio.sleep(0)

        return await self.get_query_operation(
            session_id=session_id,
            operation_id=operation_id,
            tenant_id=tenant_id,
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

    def _wire_wrapper_elicitation_handler(self, wrapper: Any) -> None:
        """Attach the bridge elicitation handler when the wrapper exposes the public hook."""
        setter = getattr(wrapper, "set_elicitation_handler", None)
        if callable(setter):
            setter(self._handle_wrapper_elicitation)
        else:
            logger.debug(
                "Wrapper %s does not expose set_elicitation_handler; skipping elicitation wiring",
                type(wrapper).__name__,
            )

    async def _run_query_operation(
        self,
        *,
        session_id: str,
        operation_id: str,
        tenant_id: Optional[str],
        run_id: Optional[str],
    ) -> None:
        request: QueryOperationInput | QueryOperationToolInput | None = None

        try:
            request = await self._set_query_operation_status(
                session_id=session_id,
                operation_id=operation_id,
                status=QueryOperationStatus.running,
            )
            if request is None:
                return

            session_data = await self.get_session(session_id=session_id, tenant_id=tenant_id)
            wrapper = session_data.wrapper
            wrapper.set_context(
                tenant_id=tenant_id,
                run_id=run_id,
                session_id=session_id,
            )

            start_time = asyncio.get_running_loop().time()
            async with wrapper.query_operation_scope(
                operation_id=operation_id,
                tenant_id=tenant_id,
                run_id=run_id,
                session_id=session_id,
            ):
                if isinstance(request, QueryOperationToolInput):
                    result = await wrapper.call_tool(
                        tool_name=request.tool_name,
                        arguments=request.arguments,
                        server_name=request.server_name,
                    )
                    serialized_result = self._serialize_operation_result(result)
                    steps_used = 0
                else:
                    result = await wrapper.run_query(
                        query=request.query,
                        max_steps=request.max_steps,
                        server_name=request.server_name,
                    )
                    serialized_result = result
                    steps_used = wrapper.steps_used
            execution_time = asyncio.get_running_loop().time() - start_time

            server_used = wrapper.last_server_used
            has_mcp_servers = getattr(wrapper, "has_mcp_servers", None)
            if has_mcp_servers is False:
                server_used = None

            await self._complete_query_operation(
                session_id=session_id,
                operation_id=operation_id,
                result=QueryOperationResult(
                    result=serialized_result,
                    execution_time=execution_time,
                    steps_used=steps_used,
                    timestamp=datetime.now(),
                    server_used=server_used,
                    has_mcp_servers=has_mcp_servers,
                ),
            )

        except asyncio.CancelledError:
            await self._cancel_query_operation(
                session_id=session_id,
                operation_id=operation_id,
            )
            raise
        except Exception as exc:
            await self._fail_query_operation(
                session_id=session_id,
                operation_id=operation_id,
                error=self._serialize_query_operation_error(exc),
            )
        finally:
            await self._clear_pending_elicitation(session_id=session_id, operation_id=operation_id)
            async with self._lock:
                session_tasks = self._query_operation_tasks.get(session_id)
                if session_tasks is not None:
                    session_tasks.pop(operation_id, None)
                    if not session_tasks:
                        self._query_operation_tasks.pop(session_id, None)

    async def _handle_wrapper_elicitation(
        self,
        *,
        session_id: str,
        operation_id: str,
        payload: Dict[str, Any],
    ) -> Any:
        interaction = QueryOperationInteraction(
            interaction_id=str(uuid.uuid4()),
            message=str(payload.get("message") or ""),
            requested_schema=payload.get("requested_schema"),
            requested_at=datetime.now(),
            details={
                "server_name": payload.get("server_name"),
                "request_context": payload.get("request_context", {}),
            },
        )
        future = asyncio.get_running_loop().create_future()
        pending = PendingElicitation(
            interaction_id=interaction.interaction_id,
            future=future,
            created_at=interaction.requested_at,
        )

        async with self._lock:
            operation = self._query_operations.get(session_id, {}).get(operation_id)
            if operation is None:
                raise QueryOperationNotFoundError(
                    f"Query operation {operation_id} not found for session {session_id}"
                )

            existing = self._pending_elicitations.setdefault(session_id, {}).get(operation_id)
            if existing is not None and not existing.future.done():
                existing.future.cancel()

            self._pending_elicitations.setdefault(session_id, {})[operation_id] = pending
            operation.status = QueryOperationStatus.input_required
            operation.requires_input = True
            operation.pending_interaction = interaction
            operation.result = None
            operation.error = None
            operation.metadata.updated_at = datetime.now()

        try:
            resume_request: QueryOperationResumeRequest = await future
            if resume_request.action != "accept":
                raise QueryOperationElicitationDeclinedError(
                    f"Elicitation declined for query operation {operation_id}"
                )
            return resume_request.content
        finally:
            await self._clear_pending_elicitation(
                session_id=session_id,
                operation_id=operation_id,
                interaction_id=interaction.interaction_id,
            )

    async def _clear_pending_elicitation(
        self,
        *,
        session_id: str,
        operation_id: str,
        interaction_id: Optional[str] = None,
    ) -> None:
        async with self._lock:
            session_pending = self._pending_elicitations.get(session_id)
            if session_pending is None:
                return

            pending = session_pending.get(operation_id)
            if pending is None:
                return
            if interaction_id is not None and pending.interaction_id != interaction_id:
                return

            session_pending.pop(operation_id, None)
            if not session_pending:
                self._pending_elicitations.pop(session_id, None)

    @staticmethod
    def _validate_resume_request(
        *,
        request: QueryOperationResumeRequest,
        interaction_id: str,
    ) -> None:
        if request.interaction_id and request.interaction_id != interaction_id:
            raise QueryOperationElicitationExpiredError(
                f"Elicitation {request.interaction_id} is no longer active"
            )

        if request.action == "accept" and request.content is None:
            raise QueryOperationResumeInvalidError(
                "Resume action 'accept' requires a non-null content payload"
            )

        if request.action in {"decline", "cancel"} and request.content is not None:
            raise QueryOperationResumeInvalidError(
                f"Resume action '{request.action}' must not include a content payload"
            )

    @staticmethod
    def _build_query_operation_input(
        request: QueryOperationCreateRequest,
    ) -> QueryOperationInput | QueryOperationToolInput:
        if request.tool_name is not None:
            return QueryOperationToolInput(
                server_name=request.server_name,
                tool_name=request.tool_name,
                arguments=dict(request.arguments),
            )

        return QueryOperationInput(
            query=request.query or "",
            max_steps=request.max_steps,
            server_name=request.server_name,
        )

    @staticmethod
    def _serialize_operation_result(result: Any) -> Any:
        return jsonable_encoder(result)

    async def _set_query_operation_status(
        self,
        *,
        session_id: str,
        operation_id: str,
        status: QueryOperationStatus,
    ) -> Optional[QueryOperationInput | QueryOperationToolInput]:
        async with self._lock:
            operation = self._query_operations.get(session_id, {}).get(operation_id)
            if operation is None:
                return None
            operation.status = status
            operation.metadata.updated_at = datetime.now()
            return operation.metadata.request.model_copy(deep=True)

    async def _complete_query_operation(
        self,
        *,
        session_id: str,
        operation_id: str,
        result: QueryOperationResult,
    ) -> None:
        async with self._lock:
            operation = self._query_operations.get(session_id, {}).get(operation_id)
            if operation is None:
                return
            operation.status = QueryOperationStatus.completed
            operation.result = result
            operation.error = None
            operation.requires_input = False
            operation.pending_interaction = None
            operation.metadata.updated_at = datetime.now()

    async def _fail_query_operation(
        self,
        *,
        session_id: str,
        operation_id: str,
        error: QueryOperationError,
    ) -> None:
        async with self._lock:
            operation = self._query_operations.get(session_id, {}).get(operation_id)
            if operation is None:
                return
            operation.status = QueryOperationStatus.failed
            operation.result = None
            operation.error = error
            operation.requires_input = False
            operation.pending_interaction = None
            operation.metadata.updated_at = datetime.now()

    async def _cancel_query_operation(
        self,
        *,
        session_id: str,
        operation_id: str,
        code: str = "MCP_QUERY_OPERATION_CANCELLED",
        message: str = "Query operation cancelled",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        async with self._lock:
            operation = self._query_operations.get(session_id, {}).get(operation_id)
            if operation is None:
                return
            operation.status = QueryOperationStatus.cancelled
            operation.result = None
            operation.error = QueryOperationError(
                code=code,
                message=message,
                details=details or {},
            )
            operation.requires_input = False
            operation.pending_interaction = None
            operation.metadata.updated_at = datetime.now()

    @staticmethod
    def _serialize_query_operation_error(exc: Exception) -> QueryOperationError:
        if isinstance(exc, MCPToolNotAllowedError):
            details = {}
            tool_name = getattr(exc, "tool_name", None)
            if tool_name is not None:
                details["tool_name"] = tool_name
            return QueryOperationError(
                code="MCP_TOOL_NOT_ALLOWED",
                message=str(exc),
                details=details,
            )
        if isinstance(exc, GuardrailViolationError):
            details = {}
            for key in ("phase", "rule", "tool_name"):
                value = getattr(exc, key, None)
                if value is not None:
                    details[key] = value
            extra_details = getattr(exc, "details", None)
            if isinstance(extra_details, dict):
                details["details"] = extra_details
            return QueryOperationError(
                code=getattr(exc, "code", "GUARDRAIL_VIOLATION"),
                message=getattr(exc, "message", str(exc)),
                details=details,
            )
        if isinstance(exc, QueryOperationElicitationDeclinedError):
            return QueryOperationError(
                code="MCP_ELICITATION_DECLINED",
                message=str(exc),
            )
        if isinstance(exc, ConfigurationError):
            return QueryOperationError(code="MCP_CONFIGURATION_ERROR", message=str(exc))
        if isinstance(exc, SessionNotFoundError):
            return QueryOperationError(code="MCP_SESSION_NOT_FOUND", message=str(exc))
        if isinstance(exc, QueryOperationNotFoundError):
            return QueryOperationError(code="MCP_QUERY_OPERATION_NOT_FOUND", message=str(exc))
        if isinstance(exc, ValueError):
            return QueryOperationError(code="MCP_SCHEMA_ERROR", message=str(exc))
        message = str(exc) if str(exc) else "Internal Error"
        return QueryOperationError(code="MCP_UPSTREAM_ERROR", message=message)

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
