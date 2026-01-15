"""Shared structured error helpers (MCP + A2A aligned).

This module provides a small utility to build HTTPException payloads with
the same `detail.{code,message,...}` schema used by the A2A endpoints.

Keep this module dependency-free (besides FastAPI) and deterministic.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import HTTPException


# Guardrail-related stable codes
GUARDRAIL_VIOLATION = "GUARDRAIL_VIOLATION"
PII_DETECTED = "PII_DETECTED"
BIAS_DETECTED = "BIAS_DETECTED"
MCP_GUARDRAIL_TIMEOUT = "MCP_GUARDRAIL_TIMEOUT"


def make_detail(code: str, message: str, **extra: Any) -> Dict[str, Any]:
    """Build a structured error payload under `detail`.

    The returned dict is suitable for FastAPI's HTTPException(detail=...).
    """
    detail: Dict[str, Any] = {
        "code": code,
        "message": message,
    }
    # Include only non-None extras to keep responses compact and stable.
    for k, v in extra.items():
        if v is not None:
            detail[k] = v
    return detail


def http_error(
    status_code: int,
    code: str,
    message: str,
    **extra: Any,
) -> HTTPException:
    """Create an HTTPException with structured `detail` payload."""
    return HTTPException(
        status_code=status_code,
        detail=make_detail(code, message, **extra),
    )
