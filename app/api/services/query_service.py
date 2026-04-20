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
from app.core.multimodal.preflight import validate_multimodal_query_request
from app.core.multimodal.model_query import resolve_request_query
from app.core.multimodal.validation import MultimodalInputValidationError
from app.core.sessions.manager import SessionManager
from app.models.requests import QueryOperationCreateRequest, QueryOperationResumeRequest, QueryRequest
from app.models.responses import QueryOperationResponse, QueryResponse

MULTIPART_DIRECT_TOOL_INVOCATION_NOT_SUPPORTED_MESSAGE = (
    "Multipart direct tool invocation with uploaded documents is not supported in 0.2.1. "
    "Use POST /sessions/{session_id}/query-operations with JSON arguments. "
    "If the MCP server is path-based, pass a file_path reachable by that server."
)


def _multipart_field_has_non_empty_value(values: Sequence[object] | None) -> bool:
    for value in values or ():
        if isinstance(value, str):
            if value.strip():
                return True
            continue
        if value is not None:
            return True
    return False


async def _execute_query_request(
    *,
    session_id: str,
    request: QueryRequest,
    tenant_ctx: TenantContext,
    session_manager: SessionManager,
) -> QueryResponse:
    session_data = await session_manager.get_session(session_id=session_id, tenant_id=tenant_ctx.tenant_id)
    validate_multimodal_query_request(
        request=request,
        provider=session_data.config.llm_provider.provider,
        model=session_data.config.llm_provider.model,
    )
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
    documents: Sequence[UploadFile] | None,
    tenant_ctx: TenantContext,
    session_manager: SessionManager,
) -> QueryResponse:
    asset_ids: list[str] = []
    try:
        request, asset_ids = await build_multipart_query_request(
            session_id=session_id,
            text=text,
            max_steps=max_steps,
            server_name=server_name,
            images=images,
            documents=documents,
            asset_store=session_manager.temporary_asset_store,
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
    finally:
        await session_manager.temporary_asset_store.delete_assets(
            session_id=session_id,
            asset_ids=asset_ids,
        )


async def create_query_operation(
    *,
    session_id: str,
    request: QueryOperationCreateRequest,
    tenant_ctx: TenantContext,
    session_manager: SessionManager,
) -> QueryOperationResponse:
    try:
        session_data = await session_manager.get_session(session_id=session_id, tenant_id=tenant_ctx.tenant_id)
        validate_multimodal_query_request(
            request=request,
            provider=session_data.config.llm_provider.provider,
            model=session_data.config.llm_provider.model,
        )
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
    raw_tool_name_values: Sequence[object] | None,
    raw_arguments_values: Sequence[object] | None,
    images: Sequence[UploadFile] | None,
    documents: Sequence[UploadFile] | None,
    tenant_ctx: TenantContext,
    session_manager: SessionManager,
) -> QueryOperationResponse:
    asset_ids: list[str] = []
    operation_created = False
    try:
        if (
            _multipart_field_has_non_empty_value(raw_tool_name_values)
            or _multipart_field_has_non_empty_value(raw_arguments_values)
        ):
            raise MultimodalInputValidationError(MULTIPART_DIRECT_TOOL_INVOCATION_NOT_SUPPORTED_MESSAGE)
        request, asset_ids = await build_multipart_query_operation_request(
            session_id=session_id,
            text=text,
            max_steps=max_steps,
            server_name=server_name,
            images=images,
            documents=documents,
            asset_store=session_manager.temporary_asset_store,
        )
    except Exception as exc:
        raise map_query_error(
            exc,
            operation="create_multipart_query_operation",
            tenant_ctx=tenant_ctx,
            session_id=session_id,
        ) from exc

    try:
        session_data = await session_manager.get_session(session_id=session_id, tenant_id=tenant_ctx.tenant_id)
        validate_multimodal_query_request(
            request=request,
            provider=session_data.config.llm_provider.provider,
            model=session_data.config.llm_provider.model,
        )
        response = await session_manager.create_query_operation(
            session_id=session_id,
            request=request,
            tenant_id=tenant_ctx.tenant_id,
            run_id=tenant_ctx.run_id,
        )
        operation_created = True
        return response
    except Exception as exc:
        raise map_query_error(
            exc,
            operation="create_multipart_query_operation",
            tenant_ctx=tenant_ctx,
            session_id=session_id,
        ) from exc
    finally:
        if asset_ids and not operation_created:
            await session_manager.temporary_asset_store.delete_assets(
                session_id=session_id,
                asset_ids=asset_ids,
            )


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
