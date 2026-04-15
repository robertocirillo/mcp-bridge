from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import TenantContext, get_session_manager, get_tenant_context
from app.api.error_mapping import map_query_error
from app.api.multipart_form import multipart_query_request_body_openapi, normalize_multipart_query_form
from app.api.services import query_service
from app.core.sessions.manager import SessionManager
from app.models.requests import QueryOperationCreateRequest, QueryOperationResumeRequest, QueryRequest
from app.models.responses import QueryOperationResponse, QueryResponse

TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]
router = APIRouter()


@router.post("/{session_id}/query", response_model=QueryResponse)
async def execute_query(
    session_id: str,
    request: QueryRequest,
    tenant_ctx: TenantDep,
    session_manager: SessionManager = Depends(get_session_manager),
):
    """Execute a query on an existing session."""
    return await query_service.execute_query(
        session_id=session_id,
        request=request,
        tenant_ctx=tenant_ctx,
        session_manager=session_manager,
    )


@router.post(
    "/{session_id}/query-multipart",
    response_model=QueryResponse,
    openapi_extra=multipart_query_request_body_openapi(),
)
async def execute_multipart_query(
    session_id: str,
    request: Request,
    tenant_ctx: TenantDep,
    session_manager: SessionManager = Depends(get_session_manager),
):
    """Execute a synchronous multimodal query from multipart form-data uploads."""
    try:
        form = await normalize_multipart_query_form(request)
    except Exception as exc:
        raise map_query_error(
            exc,
            operation="execute_multipart_query",
            tenant_ctx=tenant_ctx,
            session_id=session_id,
        ) from exc
    return await query_service.execute_multipart_query(
        session_id=session_id,
        text=form.text,
        max_steps=form.max_steps,
        server_name=form.server_name,
        images=form.images,
        documents=form.documents,
        tenant_ctx=tenant_ctx,
        session_manager=session_manager,
    )


@router.post("/{session_id}/query-operations", response_model=QueryOperationResponse)
async def create_query_operation(
    session_id: str,
    request: QueryOperationCreateRequest,
    tenant_ctx: TenantDep,
    session_manager: SessionManager = Depends(get_session_manager),
):
    """Create an asynchronous session operation for an existing session."""
    return await query_service.create_query_operation(
        session_id=session_id,
        request=request,
        tenant_ctx=tenant_ctx,
        session_manager=session_manager,
    )


@router.post(
    "/{session_id}/query-operations-multipart",
    response_model=QueryOperationResponse,
    openapi_extra=multipart_query_request_body_openapi(),
)
async def create_multipart_query_operation(
    session_id: str,
    request: Request,
    tenant_ctx: TenantDep,
    session_manager: SessionManager = Depends(get_session_manager),
):
    """Create an asynchronous multimodal query operation from multipart form-data uploads."""
    try:
        form = await normalize_multipart_query_form(request)
    except Exception as exc:
        raise map_query_error(
            exc,
            operation="create_multipart_query_operation",
            tenant_ctx=tenant_ctx,
            session_id=session_id,
        ) from exc
    return await query_service.create_multipart_query_operation(
        session_id=session_id,
        text=form.text,
        max_steps=form.max_steps,
        server_name=form.server_name,
        raw_tool_name_values=form.raw_tool_name_values,
        raw_arguments_values=form.raw_arguments_values,
        images=form.images,
        documents=form.documents,
        tenant_ctx=tenant_ctx,
        session_manager=session_manager,
    )


@router.get("/{session_id}/query-operations/{operation_id}", response_model=QueryOperationResponse)
async def get_query_operation(
    session_id: str,
    operation_id: str,
    tenant_ctx: TenantDep,
    session_manager: SessionManager = Depends(get_session_manager),
):
    """Return the current state of an asynchronous query operation."""
    return await query_service.get_query_operation(
        session_id=session_id,
        operation_id=operation_id,
        tenant_ctx=tenant_ctx,
        session_manager=session_manager,
    )


@router.post("/{session_id}/query-operations/{operation_id}/resume", response_model=QueryOperationResponse)
async def resume_query_operation(
    session_id: str,
    operation_id: str,
    request: QueryOperationResumeRequest,
    tenant_ctx: TenantDep,
    session_manager: SessionManager = Depends(get_session_manager),
):
    """Resume a paused query operation waiting on elicitation."""
    return await query_service.resume_query_operation(
        session_id=session_id,
        operation_id=operation_id,
        request=request,
        tenant_ctx=tenant_ctx,
        session_manager=session_manager,
    )


@router.get("/{session_id}/history")
async def get_query_history(
    session_id: str,
    limit: int = 10,
    session_manager: SessionManager = Depends(get_session_manager),
):
    """Return basic query stats for a session.

    NOTE: Detailed history is not implemented yet.
    """
    _ = limit
    return await query_service.get_query_history(
        session_id=session_id,
        session_manager=session_manager,
    )
