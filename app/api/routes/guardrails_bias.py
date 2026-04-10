"""
Read-only proxy endpoints for bias-detector-service introspection.

These endpoints exist so external clients can inspect model labels/policies
even when bias-detector-service is reachable only inside the Docker network.

This module is intentionally *thin*: it forwards requests and returns JSON
without applying any interpretation/policy decisions.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.api.dependencies import get_settings
from app.api.errors import http_error
from config import Settings

router = APIRouter()


async def _forward_get(path: str, *, settings: Settings) -> Any:
    base_url = (getattr(settings, "BIAS_DETECTOR_SERVICE_BASE_URL", None) or "").rstrip("/")
    if not base_url:
        raise http_error(
            status_code=503,
            code="BIAS_DETECTOR_UNAVAILABLE",
            message="Bias detector service is not configured",
            operation="guardrails_bias_proxy",
        )

    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(5.0)) as client:
            r = await client.get(path)
    except Exception as e:
        raise http_error(
            status_code=503,
            code="BIAS_DETECTOR_UNAVAILABLE",
            message="Bias detector service unavailable",
            operation="guardrails_bias_proxy",
            details={"error": type(e).__name__},
        )

    if r.status_code != 200:
        try:
            body = r.json()
        except Exception:
            body = {"detail": r.text}
        return JSONResponse(status_code=r.status_code, content=body)

    return r.json()


@router.get("/models/{model_id:path}/policy")
async def get_model_policy(model_id: str, settings: Settings = Depends(get_settings)):
    """Proxy to bias-detector-service GET /v1/models/{model_id}/policy."""
    return await _forward_get(f"/v1/models/{model_id}/policy", settings=settings)


@router.get("/models/{model_id:path}/labels")
async def get_model_labels(model_id: str, settings: Settings = Depends(get_settings)):
    """Proxy to bias-detector-service GET /v1/models/{model_id}/labels (if supported)."""
    return await _forward_get(f"/v1/models/{model_id}/labels", settings=settings)
