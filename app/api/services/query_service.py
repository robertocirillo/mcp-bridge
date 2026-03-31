"""Thin API services backing query routes."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime

from fastapi import UploadFile

from app.api.dependencies import TenantContext
from app.api.error_mapping import map_query_error
from app.api.session_context import bind_wrapper_context
from app.api.services.multipart_query import (
    build_multipart_query_operation_request,
    build_multipart_query_request,
)
from app.core.multimodal.model_query import resolve_request_query
from app.core.sessions.manager import SessionManager
from app.models.requests import QueryOperationCreateRequest, QueryOperationResumeRequest, QueryRequest
from app.models.responses import QueryOperationResponse, QueryResponse


async def _execute_query_request(
    *,
    session_id: str,
    request: QueryRequest,
    tenant_ctx: TenantContext,
    session_manager: SessionManager,
) -> QueryResponse:
    session_data = await session_manager.get_session(session_id)
    wrapper = bind_wrapper_context(
        session_data.wrapper,
        tenant_ctx=tenant_ctx,
        session_id=session_id,
    )

    loop = asyncio.get_event_loop()
    start_time = loop.time()
    result = await wrapper.run_query(
        query=resolve_request_query(query=request.query, input_payload=request.input),
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


async def execute_query(
    *,
    session_id: str,
    request: QueryRequest,
    tenant_ctx: TenantContext,
    session_manager: SessionManager,
) -> QueryResponse:
    try:
        return await _execute_query_request(
            session_id=session_id,
            request=request,
            tenant_ctx=tenant_ctx,
            session_manager=session_manager,
        )
    except Exception as exc:
        raise map_query_error(
            exc,
            operation="execute_query",
            tenant_ctx=tenant_ctx,
            session_id=session_id,
        ) from exc


async def execute_multipart_query(
    *,
    session_id: str,
    text: str | None,
    max_steps: int | None,
    server_name: str | None,
    images: Sequence[UploadFile] | None,
    tenant_ctx: TenantContext,
    session_manager: SessionManager,
) -> QueryResponse:
    try:
        request = await build_multipart_query_request(
            text=text,
            max_steps=max_steps,
            server_name=server_name,
            images=images,
        )
    except Exception as exc:
        raise map_query_error(
            exc,
            operation="execute_multipart_query",
            tenant_ctx=tenant_ctx,
            session_id=session_id,
        ) from exc

    try:
        return await _execute_query_request(
            session_id=session_id,
            request=request,
            tenant_ctx=tenant_ctx,
            session_manager=session_manager,
        )
    except Exception as exc:
        raise map_query_error(
            exc,
            operation="execute_multipart_query",
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


async def create_multipart_query_operation(
    *,
    session_id: str,
    text: str | None,
    max_steps: int | None,
    server_name: str | None,
    images: Sequence[UploadFile] | None,
    tenant_ctx: TenantContext,
    session_manager: SessionManager,
) -> QueryOperationResponse:
    try:
        request = await build_multipart_query_operation_request(
            session_id=session_id,
            text=text,
            max_steps=max_steps,
            server_name=server_name,
            images=images,
            upload_store=session_manager.temporary_upload_store,
        )
    except Exception as exc:
        raise map_query_error(
            exc,
            operation="create_multipart_query_operation",
            tenant_ctx=tenant_ctx,
            session_id=session_id,
        ) from exc

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
            operation="create_multipart_query_operation",
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
