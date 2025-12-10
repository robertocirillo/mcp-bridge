"""
FastAPI dependencies
"""

from functools import lru_cache
from typing import Dict, Optional

from app.core.session_manager import SessionManager
from config import Settings, settings
from app.core.a2a_client import A2AClient
from app.models.config import A2AAgentConfig


from fastapi import Header, HTTPException, Depends
from pydantic import BaseModel

# Session manager singleton
_session_manager: SessionManager = None

def get_session_manager() -> SessionManager:
    """Dependency injection session manager"""
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager

@lru_cache()
def get_settings():
    """Dependency injection settings (cached)"""
    from config import settings
    return settings


def get_a2a_client(
    settings: Settings = Depends(get_settings),
) -> A2AClient:
    """
    Dependency that provides a configured A2AClient instance.
    """

    agent_configs: Dict[str, A2AAgentConfig] = settings.a2a.agents or {}
    return A2AClient(agent_configs=agent_configs)


#MultiTenancy
class TenantContext(BaseModel):
    """Resolved tenant context for the current request."""
    tenant_id: str
    run_id: Optional[str] = None


def get_tenant_context(
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_run_id: Optional[str] = Header(default=None, alias="X-Run-Id"),
) -> TenantContext:
    """
    Resolve tenant_id and run_id based on headers and multi-tenancy settings.

    Modes:

    - multi_tenancy.enabled = False:
        * Ignore headers for routing, always use default_tenant_id (or "default").
    - enabled = True, require_header = False:
        * Use X-Tenant-Id if present, otherwise default_tenant_id (or "default").
    - enabled = True, require_header = True:
        * If X-Tenant-Id is missing, raise HTTP 400.
    """

    mt = settings.multi_tenancy

    # Determine base tenant_id according to the configured mode
    if not mt.enabled:
        # Single-tenant mode: ignore headers, always use default
        tenant_id = mt.default_tenant_id or "default"
    else:
        # Multi-tenant mode
        if x_tenant_id:
            tenant_id = x_tenant_id
        else:
            if mt.require_header:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Missing X-Tenant-Id header "
                        "(multi-tenancy is enabled and require_header = true)."
                    ),
                )
            # Header is optional: fall back to default tenant
            tenant_id = mt.default_tenant_id or "default"

    # run_id is always optional, used only for tracing / correlation
    run_id = x_run_id

    return TenantContext(tenant_id=tenant_id, run_id=run_id)
