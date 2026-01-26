"""HTTP client for bias-detector-service.

The service provides an API to classify bias using a transformer classifier
(default model or per-request model override).

This module is intentionally *thin*: it forwards requests and returns JSON.
Policy decisions (block/warn/off) are handled by mcp-bridge guardrails.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx


@dataclass(frozen=True)
class BiasDetectorError(Exception):
    """Raised when bias-detector-service returns a non-200 response."""

    status_code: int
    body: Any

    def __str__(self) -> str:  # pragma: no cover
        return f"BiasDetectorError(status_code={self.status_code}, body={self.body!r})"


class BiasDetectorClient:
    """Async HTTP client for bias-detector-service."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float = 5.0,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.timeout_seconds = float(timeout_seconds)

        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(self.timeout_seconds),
            headers={"Content-Type": "application/json"},
        )

    async def classify(
        self,
        *,
        text: str,
        model_id: Optional[str] = None,
        revision: Optional[str] = None,
        active_categories: Optional[List[str]] = None,
        top_k: int = 5,
        threshold: float = 0.5,
    ) -> Dict[str, Any]:
        """Call POST /v1/bias/classify.

        Returns the decoded JSON response.
        Raises BiasDetectorError on non-200.
        """

        payload: Dict[str, Any] = {
            "text": text,
            "top_k": int(top_k),
            "threshold": float(threshold),
        }
        if model_id:
            payload["model_id"] = model_id
        if revision:
            payload["revision"] = revision
        # NOTE: this is tri-state on the server:
        # - None -> all categories active
        # - []   -> no categories active (flagged=false)
        # - [..] -> selected categories
        if active_categories is not None:
            payload["active_categories"] = active_categories

        r = await self._client.post("/v1/bias/classify", json=payload)
        if r.status_code != 200:
            body: Any
            try:
                body = r.json()
            except Exception:
                body = r.text
            raise BiasDetectorError(status_code=r.status_code, body=body)

        return r.json()

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
