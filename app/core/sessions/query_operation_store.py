"""
Query operation storage and state helpers used by SessionManager.
"""

import asyncio
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi.encoders import jsonable_encoder

from app.core.exceptions import (
    ConfigurationError,
    ImageInputNotSupportedError,
    QueryOperationElicitationDeclinedError,
    QueryOperationNotFoundError,
    SessionNotFoundError,
)
from app.core.multimodal.model_query import sanitize_multimodal_error, summarize_query_input
from app.core.runtime.mcp_wrapper import GuardrailViolationError, MCPToolNotAllowedError
from app.models.requests import QueryOperationCreateRequest
from app.models.responses import (
    QueryOperationError,
    QueryOperationInput,
    QueryOperationMultimodalInput,
    QueryOperationResponse,
    QueryOperationResult,
    QueryOperationStatus,
    QueryOperationToolInput,
)


def build_query_operation_input(
    request: QueryOperationCreateRequest,
) -> QueryOperationInput | QueryOperationMultimodalInput | QueryOperationToolInput:
    if request.tool_name is not None:
        return QueryOperationToolInput(
            server_name=request.server_name,
            tool_name=request.tool_name,
            arguments=dict(request.arguments),
        )

    if request.input is not None:
        return QueryOperationMultimodalInput(
            input=summarize_query_input(request.input),
            max_steps=request.max_steps,
            server_name=request.server_name,
        )

    return QueryOperationInput(
        query=request.query or "",
        max_steps=request.max_steps,
        server_name=request.server_name,
    )


def serialize_operation_result(result: Any) -> Any:
    return jsonable_encoder(result)


def serialize_query_operation_error(exc: Exception) -> QueryOperationError:
    if isinstance(exc, MCPToolNotAllowedError):
        details = {}
        tool_name = getattr(exc, "tool_name", None)
        if tool_name is not None:
            details["tool_name"] = tool_name
        return QueryOperationError(
            code="MCP_TOOL_NOT_ALLOWED",
            message=sanitize_multimodal_error(exc),
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
            message=sanitize_multimodal_error(getattr(exc, "message", str(exc))),
            details=details,
        )
    if isinstance(exc, QueryOperationElicitationDeclinedError):
        return QueryOperationError(
            code="MCP_ELICITATION_DECLINED",
            message=sanitize_multimodal_error(exc),
        )
    if isinstance(exc, ImageInputNotSupportedError):
        return QueryOperationError(
            code="MCP_IMAGE_INPUT_NOT_SUPPORTED",
            message=sanitize_multimodal_error(exc),
            details={
                "provider": getattr(exc, "provider", None),
                "model": getattr(exc, "model", None),
                "reason": getattr(exc, "reason", None),
            },
        )
    if isinstance(exc, ConfigurationError):
        return QueryOperationError(code="MCP_CONFIGURATION_ERROR", message=sanitize_multimodal_error(exc))
    if isinstance(exc, SessionNotFoundError):
        return QueryOperationError(code="MCP_SESSION_NOT_FOUND", message=sanitize_multimodal_error(exc))
    if isinstance(exc, QueryOperationNotFoundError):
        return QueryOperationError(code="MCP_QUERY_OPERATION_NOT_FOUND", message=sanitize_multimodal_error(exc))
    if isinstance(exc, ValueError):
        return QueryOperationError(code="MCP_SCHEMA_ERROR", message=sanitize_multimodal_error(exc))
    message = sanitize_multimodal_error(exc) if str(exc) else "Internal Error"
    return QueryOperationError(code="MCP_UPSTREAM_ERROR", message=message)


class QueryOperationStore:
    """In-memory storage for query operations and their background tasks."""

    def __init__(self):
        self.operations: Dict[str, Dict[str, QueryOperationResponse]] = {}
        self.tasks: Dict[str, Dict[str, asyncio.Task]] = {}
        self.execution_requests: Dict[str, Dict[str, QueryOperationCreateRequest]] = {}

    def add(
        self,
        session_id: str,
        operation: QueryOperationResponse,
        execution_request: QueryOperationCreateRequest,
    ) -> None:
        self.operations.setdefault(session_id, {})[operation.operation_id] = operation
        self.execution_requests.setdefault(session_id, {})[operation.operation_id] = execution_request.model_copy(
            deep=True
        )

    def get(self, session_id: str, operation_id: str) -> Optional[QueryOperationResponse]:
        return self.operations.get(session_id, {}).get(operation_id)

    def get_copy(self, session_id: str, operation_id: str) -> Optional[QueryOperationResponse]:
        operation = self.get(session_id, operation_id)
        if operation is None:
            return None
        return operation.model_copy(deep=True)

    def has(self, session_id: str, operation_id: str) -> bool:
        return operation_id in self.operations.get(session_id, {})

    def remove_session(self, session_id: str) -> None:
        self.operations.pop(session_id, None)
        self.execution_requests.pop(session_id, None)

    def pop_session_tasks(self, session_id: str) -> list[asyncio.Task]:
        return list(self.tasks.pop(session_id, {}).values())

    def get_task(self, session_id: str, operation_id: str) -> Optional[asyncio.Task]:
        return self.tasks.get(session_id, {}).get(operation_id)

    def set_task(self, session_id: str, operation_id: str, task: asyncio.Task) -> None:
        self.tasks.setdefault(session_id, {})[operation_id] = task

    def discard_task(self, session_id: str, operation_id: str) -> None:
        session_tasks = self.tasks.get(session_id)
        if session_tasks is None:
            return
        session_tasks.pop(operation_id, None)
        if not session_tasks:
            self.tasks.pop(session_id, None)

    def set_status(
        self,
        *,
        session_id: str,
        operation_id: str,
        status: QueryOperationStatus,
    ) -> Optional[QueryOperationCreateRequest]:
        operation = self.get(session_id, operation_id)
        if operation is None:
            return None
        operation.status = status
        operation.metadata.updated_at = datetime.now()
        request = self.execution_requests.get(session_id, {}).get(operation_id)
        return request.model_copy(deep=True) if request is not None else None

    def complete(
        self,
        *,
        session_id: str,
        operation_id: str,
        result: QueryOperationResult,
    ) -> None:
        operation = self.get(session_id, operation_id)
        if operation is None:
            return
        operation.status = QueryOperationStatus.completed
        operation.result = result
        operation.error = None
        operation.requires_input = False
        operation.pending_interaction = None
        operation.metadata.updated_at = datetime.now()
        self.execution_requests.get(session_id, {}).pop(operation_id, None)

    def fail(
        self,
        *,
        session_id: str,
        operation_id: str,
        error: QueryOperationError,
    ) -> None:
        operation = self.get(session_id, operation_id)
        if operation is None:
            return
        operation.status = QueryOperationStatus.failed
        operation.result = None
        operation.error = error
        operation.requires_input = False
        operation.pending_interaction = None
        operation.metadata.updated_at = datetime.now()
        self.execution_requests.get(session_id, {}).pop(operation_id, None)

    def cancel(
        self,
        *,
        session_id: str,
        operation_id: str,
        code: str = "MCP_QUERY_OPERATION_CANCELLED",
        message: str = "Query operation cancelled",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        operation = self.get(session_id, operation_id)
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
        self.execution_requests.get(session_id, {}).pop(operation_id, None)
