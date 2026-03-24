"""Session and wrapper context helpers for API routes."""

from __future__ import annotations

from typing import Any

from app.api.dependencies import TenantContext
from app.core.session_manager import SessionManager


async def get_tenant_session(
    *,
    session_id: str,
    tenant_ctx: TenantContext,
    session_manager: SessionManager,
) -> Any:
    """Return a session scoped to the current tenant."""
    return await session_manager.get_session(
        session_id=session_id,
        tenant_id=tenant_ctx.tenant_id,
    )


def bind_wrapper_context(
    wrapper: Any,
    *,
    tenant_ctx: TenantContext,
    session_id: str,
) -> Any:
    """Refresh wrapper context for logs and structured API errors."""
    wrapper.set_context(
        tenant_id=tenant_ctx.tenant_id,
        run_id=tenant_ctx.run_id,
        session_id=session_id,
    )
    return wrapper


async def get_owned_wrapper(
    *,
    session_id: str,
    tenant_ctx: TenantContext,
    session_manager: SessionManager,
) -> Any:
    """Return the wrapper for a tenant-owned session with fresh context."""
    session_data = await get_tenant_session(
        session_id=session_id,
        tenant_ctx=tenant_ctx,
        session_manager=session_manager,
    )
    return bind_wrapper_context(
        session_data.wrapper,
        tenant_ctx=tenant_ctx,
        session_id=session_id,
    )
