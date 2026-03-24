"""Thin API services backing query routes."""

from __future__ import annotations

import asyncio
from datetime import datetime

from app.api.dependencies import TenantContext
from app.api.error_mapping import map_query_error
from app.api.session_context import bind_wrapper_context
from app.core.session_manager import SessionManager
from app.models.requests import QueryOperationCreateRequest, QueryOperationResumeRequest, QueryRequest
from app.models.responses import QueryOperationResponse, QueryResponse


async def execute_query(
    *,
    session_id: str,
    request: QueryRequest,
    tenant_ctx: TenantContext,
    session_manager: SessionManager,
) -> QueryResponse:
    try:
        session_data = await session_manager.get_session(session_id)
        wrapper = bind_wrapper_context(
            session_data.wrapper,
            tenant_ctx=tenant_ctx,
            session_id=session_id,
        )

        loop = asyncio.get_event_loop()
        start_time = loop.time()
        result = await wrapper.run_query(
            query=request.query,
            max_steps=request.max_steps,
            server_name=request.server_name,
        )
        execution_time = loop.time() - start_time

        has_mcp_servers = getattr(wrapper, "has_mcp_servers", None)
        server_used = wrapper.last_server_used
        if has_mcp_servers is False:
            server_used = None

        return QueryResponse(
            session_id=session_id,
            result=result,
            execution_time=execution_time,
            steps_used=wrapper.steps_used,
            timestamp=datetime.now(),
            server_used=server_used,
            has_mcp_servers=has_mcp_servers,
        )
    except Exception as exc:
        raise map_query_error(
            exc,
            operation="execute_query",
            tenant_ctx=tenant_ctx,
            session_id=session_id,
        ) from exc


async def create_query_operation(
    *,
    session_id: str,
    request: QueryOperationCreateRequest,
    tenant_ctx: TenantContext,
    session_manager: SessionManager,
) -> QueryOperationResponse:
    try:
        return await session_manager.create_query_operation(
            session_id=session_id,
            request=request,
            tenant_id=tenant_ctx.tenant_id,
            run_id=tenant_ctx.run_id,
        )
    except Exception as exc:
        raise map_query_error(
            exc,
            operation="create_query_operation",
            tenant_ctx=tenant_ctx,
            session_id=session_id,
        ) from exc


async def get_query_operation(
    *,
    session_id: str,
    operation_id: str,
    tenant_ctx: TenantContext,
    session_manager: SessionManager,
) -> QueryOperationResponse:
    try:
        return await session_manager.get_query_operation(
            session_id=session_id,
            operation_id=operation_id,
            tenant_id=tenant_ctx.tenant_id,
        )
    except Exception as exc:
        raise map_query_error(
            exc,
            operation="get_query_operation",
            tenant_ctx=tenant_ctx,
            session_id=session_id,
            operation_id=operation_id,
        ) from exc


async def resume_query_operation(
    *,
    session_id: str,
    operation_id: str,
    request: QueryOperationResumeRequest,
    tenant_ctx: TenantContext,
    session_manager: SessionManager,
) -> QueryOperationResponse:
    try:
        return await session_manager.resume_query_operation(
            session_id=session_id,
            operation_id=operation_id,
            request=request,
            tenant_id=tenant_ctx.tenant_id,
        )
    except Exception as exc:
        raise map_query_error(
            exc,
            operation="resume_query_operation",
            tenant_ctx=tenant_ctx,
            session_id=session_id,
            operation_id=operation_id,
        ) from exc


async def get_query_history(
    *,
    session_id: str,
    session_manager: SessionManager,
    tenant_ctx: TenantContext | None = None,
) -> dict[str, object]:
    try:
        session_data = await session_manager.get_session(session_id)
        return {
            "session_id": session_id,
            "total_queries": session_data.query_count,
            "last_used": session_data.last_used,
            "message": "Detailed history not implemented yet",
        }
    except Exception as exc:
        raise map_query_error(
            exc,
            operation="get_query_history",
            tenant_ctx=tenant_ctx,
            session_id=session_id,
        ) from exc
