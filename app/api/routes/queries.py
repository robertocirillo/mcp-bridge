from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import TenantContext, get_session_manager, get_tenant_context
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
