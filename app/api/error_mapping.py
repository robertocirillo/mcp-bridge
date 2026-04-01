"""HTTP error mapping helpers for API route services."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.api.dependencies import TenantContext
from app.api.errors import http_error
from app.core.exceptions import (
    ConfigurationError,
    ImageInputNotSupportedError,
    MCPCapabilityNotSupportedError,
    MCPCapabilityUpstreamError,
    MCPWrapperError,
    MaxSessionsExceededError,
    PDFInputNotSupportedError,
    QueryOperationElicitationExpiredError,
    QueryOperationElicitationUnavailableError,
    QueryOperationNotFoundError,
    QueryOperationResumeInvalidError,
    SessionNotFoundError,
    TemporaryUploadError,
)
from app.core.runtime.mcp_wrapper import GuardrailViolationError, MCPToolNotAllowedError


def tenant_http_error(
    status_code: int,
    code: str,
    message: str,
    *,
    tenant_ctx: TenantContext | None,
    **extra: Any,
) -> HTTPException:
    """Build a structured HTTPException including tenant/run context."""
    context = dict(extra)
    if tenant_ctx is not None:
        context["tenant_id"] = tenant_ctx.tenant_id
        context["run_id"] = tenant_ctx.run_id
    return http_error(status_code, code, message, **context)


def capability_http_error(
    *,
    status_code: int,
    code: str,
    message: str,
    operation: str,
    tenant_ctx: TenantContext,
    session_id: str,
    **extra: Any,
) -> HTTPException:
    """Build a structured HTTPException for MCP capability endpoints."""
    return tenant_http_error(
        status_code,
        code,
        message,
        tenant_ctx=tenant_ctx,
        operation=operation,
        session_id=session_id,
        **extra,
    )


def map_basic_session_error(
    exc: Exception,
    *,
    not_found_status: int = 404,
) -> HTTPException:
    """Map classic session route errors to the historical plain HTTP shape."""
    if isinstance(exc, MaxSessionsExceededError):
        return HTTPException(status_code=429, detail=str(exc))
    if isinstance(exc, SessionNotFoundError):
        return HTTPException(status_code=not_found_status, detail=str(exc))
    if isinstance(exc, ConfigurationError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, MCPWrapperError):
        return HTTPException(status_code=502, detail=str(exc))
    return HTTPException(status_code=500, detail="Internal Error")


def map_capability_error(
    exc: Exception,
    *,
    operation: str,
    tenant_ctx: TenantContext,
    session_id: str,
    **extra: Any,
) -> HTTPException:
    """Map MCP capability exceptions to the structured HTTP contract."""
    if isinstance(exc, SessionNotFoundError):
        return capability_http_error(
            status_code=404,
            code="MCP_SESSION_NOT_FOUND",
            message=str(exc),
            operation=operation,
            tenant_ctx=tenant_ctx,
            session_id=session_id,
            **extra,
        )
    if isinstance(exc, ConfigurationError):
        return capability_http_error(
            status_code=400,
            code="MCP_CONFIGURATION_ERROR",
            message=str(exc),
            operation=operation,
            tenant_ctx=tenant_ctx,
            session_id=session_id,
            **extra,
        )
    if isinstance(exc, MCPCapabilityNotSupportedError):
        return capability_http_error(
            status_code=501,
            code="MCP_CAPABILITY_NOT_SUPPORTED",
            message=str(exc),
            operation=operation,
            tenant_ctx=tenant_ctx,
            session_id=session_id,
            capability=exc.capability,
            server_name=exc.server_name,
            **extra,
        )
    if isinstance(exc, MCPCapabilityUpstreamError):
        return capability_http_error(
            status_code=502,
            code="MCP_UPSTREAM_ERROR",
            message=str(exc),
            operation=operation,
            tenant_ctx=tenant_ctx,
            session_id=session_id,
            capability=exc.capability,
            server_name=exc.server_name,
            **extra,
        )
    if isinstance(exc, MCPWrapperError):
        return capability_http_error(
            status_code=502,
            code="MCP_UPSTREAM_ERROR",
            message=str(exc),
            operation=operation,
            tenant_ctx=tenant_ctx,
            session_id=session_id,
            **extra,
        )
    return capability_http_error(
        status_code=500,
        code="MCP_INTERNAL_ERROR",
        message="Internal Error",
        operation=operation,
        tenant_ctx=tenant_ctx,
        session_id=session_id,
        **extra,
    )


def map_query_error(
    exc: Exception,
    *,
    operation: str,
    tenant_ctx: TenantContext | None,
    session_id: str,
    **extra: Any,
) -> HTTPException:
    """Map query and query-operation exceptions to the structured HTTP contract."""
    context = {
        "operation": operation,
        "session_id": session_id,
        **extra,
    }

    if isinstance(exc, QueryOperationNotFoundError):
        return tenant_http_error(
            404,
            "MCP_QUERY_OPERATION_NOT_FOUND",
            str(exc),
            tenant_ctx=tenant_ctx,
            **context,
        )
    if isinstance(exc, SessionNotFoundError):
        return tenant_http_error(
            404,
            "MCP_SESSION_NOT_FOUND",
            str(exc),
            tenant_ctx=tenant_ctx,
            **context,
        )
    if isinstance(exc, QueryOperationElicitationUnavailableError):
        return tenant_http_error(
            409,
            "MCP_QUERY_OPERATION_ELICITATION_UNAVAILABLE",
            str(exc),
            tenant_ctx=tenant_ctx,
            **context,
        )
    if isinstance(exc, QueryOperationElicitationExpiredError):
        return tenant_http_error(
            409,
            "MCP_QUERY_OPERATION_ELICITATION_EXPIRED",
            str(exc),
            tenant_ctx=tenant_ctx,
            **context,
        )
    if isinstance(exc, QueryOperationResumeInvalidError):
        return tenant_http_error(
            400,
            "MCP_QUERY_OPERATION_RESUME_INVALID",
            str(exc),
            tenant_ctx=tenant_ctx,
            **context,
        )
    if isinstance(exc, MCPToolNotAllowedError):
        return tenant_http_error(
            403,
            "MCP_TOOL_NOT_ALLOWED",
            str(exc),
            tenant_ctx=tenant_ctx,
            tool_name=getattr(exc, "tool_name", None),
            **context,
        )
    if isinstance(exc, GuardrailViolationError):
        return tenant_http_error(
            int(getattr(exc, "http_status", 403) or 403),
            getattr(exc, "code", "GUARDRAIL_VIOLATION"),
            getattr(exc, "message", str(exc)),
            tenant_ctx=tenant_ctx,
            phase=getattr(exc, "phase", None),
            rule=getattr(exc, "rule", None),
            guardrail=getattr(exc, "rule", None),
            tool_name=getattr(exc, "tool_name", None),
            details=getattr(exc, "details", {}),
            **context,
        )
    if isinstance(exc, ImageInputNotSupportedError):
        return tenant_http_error(
            400,
            "MCP_IMAGE_INPUT_NOT_SUPPORTED",
            str(exc),
            tenant_ctx=tenant_ctx,
            provider=getattr(exc, "provider", None),
            model=getattr(exc, "model", None),
            reason=getattr(exc, "reason", None),
            **context,
        )
    if isinstance(exc, PDFInputNotSupportedError):
        return tenant_http_error(
            400,
            "MCP_PDF_INPUT_NOT_SUPPORTED",
            str(exc),
            tenant_ctx=tenant_ctx,
            provider=getattr(exc, "provider", None),
            model=getattr(exc, "model", None),
            reason=getattr(exc, "reason", None),
            **context,
        )
    if isinstance(exc, ConfigurationError):
        return tenant_http_error(
            400,
            "MCP_CONFIGURATION_ERROR",
            str(exc),
            tenant_ctx=tenant_ctx,
            **context,
        )
    if isinstance(exc, TemporaryUploadError):
        return tenant_http_error(
            500,
            "MCP_TEMP_UPLOAD_ERROR",
            str(exc),
            tenant_ctx=tenant_ctx,
            **context,
        )
    if isinstance(exc, MCPWrapperError):
        return tenant_http_error(
            502,
            "MCP_UPSTREAM_ERROR",
            str(exc),
            tenant_ctx=tenant_ctx,
            **context,
        )
    if isinstance(exc, ValueError):
        return tenant_http_error(
            400,
            "MCP_SCHEMA_ERROR",
            str(exc),
            tenant_ctx=tenant_ctx,
            **context,
        )
    return tenant_http_error(
        500,
        "MCP_INTERNAL_ERROR",
        "Internal Error",
        tenant_ctx=tenant_ctx,
        **context,
    )
