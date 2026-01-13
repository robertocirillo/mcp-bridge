"""
A2A client wrapper used by mcp-bridge.

This implementation uses the official a2a-sdk (python) client.

Notes:
- `blocking` is a REST convenience flag.
  It only controls whether we wait for the final result or return early with a task_id.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import json
import httpx
from a2a.client import ClientConfig, ClientFactory, create_text_message_object
from a2a.types import TaskQueryParams

from app.models.config import A2AAgentConfig
from app.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class A2AResult:
    agent_id: str
    task_id: Optional[str] = None
    status: Optional[str] = None
    output: Optional[Dict[str, Any]] = None
    message: Optional[str] = None
    raw_response: Optional[Dict[str, Any]] = None


class A2AClientError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int = 502,
        code: str = "A2A_UPSTREAM_ERROR",
        upstream: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.upstream = upstream


class A2AClient:
    def __init__(self, agent_configs: dict[str, A2AAgentConfig]) -> None:
        self._agent_configs = agent_configs
        self._sdk_clients: dict[str, Any] = {}
        self._httpx_clients: dict[str, httpx.AsyncClient] = {}

    async def send_message(
        self,
        agent_id: str,
        text: str,
        *,
        blocking: bool = True,
        request_metadata: Optional[Dict[str, Any]] = None,
    ) -> A2AResult:
        """Send a text message to an A2A agent.

        If blocking=False we return as soon as we receive the first Task event (task id).
        """
        client = await self._get_sdk_client(agent_id)

        # IMPORTANT:
        # a2a-sdk's create_text_message_object expects role/content; passing a positional string
        # breaks typing / runtime in some versions.
        message = create_text_message_object(role="user", content=text)

        last_task: Optional[Any] = None
        last_update: Optional[Any] = None
        last_message: Optional[Any] = None

        try:
            async for ev in client.send_message(message, request_metadata=request_metadata):
                # SDK yields either a (Task, UpdateEvent|None) tuple, or a final Message
                if isinstance(ev, tuple) and len(ev) == 2:
                    task, update = ev
                    last_task = task
                    last_update = update

                    if not blocking:
                        break
                else:
                    last_message = ev

        except Exception as exc:
            logger.exception("A2A send_message failed for %s: %s", agent_id, exc)
            raise A2AClientError(
                str(exc),
                status_code=502,
                code="A2A_UPSTREAM_ERROR",
                upstream={"exception": str(exc)},
            ) from exc

        return self._to_result(agent_id, last_task=last_task, last_message=last_message, last_update=last_update)

    async def get_task(
        self,
        agent_id: str,
        task_id: str,
        *,
        history_length: Optional[int] = None,
    ) -> A2AResult:
        """Fetch task status from an A2A agent."""
        client = await self._get_sdk_client(agent_id)

        try:
            params_kwargs: Dict[str, Any] = {"id": task_id}
            if history_length is not None:
                params_kwargs["historyLength"] = history_length

            task = await client.get_task(TaskQueryParams(**params_kwargs))

        except httpx.TimeoutException as exc:
            logger.exception("A2A get_task timed out for %s/%s: %s", agent_id, task_id, exc)
            raise A2AClientError(
                "Timed out contacting agent",
                status_code=504,
                code="A2A_UPSTREAM_ERROR",
                upstream={"exception": str(exc)},
            ) from exc

        except httpx.RequestError as exc:
            logger.exception("A2A get_task transport error for %s/%s: %s", agent_id, task_id, exc)
            raise A2AClientError(
                str(exc),
                status_code=502,
                code="A2A_UPSTREAM_ERROR",
                upstream={"exception": str(exc)},
            ) from exc

        except Exception as exc:
            msg = str(exc) or repr(exc)
            msg_l = msg.lower()

            # Best-effort mapping:
            # - JSON-RPC "method not found" (-32601) or similar -> polling not applicable
            # - task not found -> 404
            if (
                "method not found" in msg_l
                or "not implemented" in msg_l
                or "not supported" in msg_l
                or "unknown method" in msg_l
                or "-32601" in msg_l
            ):
                logger.exception("A2A get_task not applicable for %s/%s: %s", agent_id, task_id, exc)
                raise A2AClientError(
                    "Task polling is not applicable for this agent",
                    status_code=409,
                    code="A2A_TASK_NOT_APPLICABLE",
                    upstream={"exception": msg},
                ) from exc

            if ("task" in msg_l and "not found" in msg_l) or "no such task" in msg_l:
                logger.exception("A2A get_task task not found for %s/%s: %s", agent_id, task_id, exc)
                raise A2AClientError(
                    "Task not found",
                    status_code=404,
                    code="A2A_TASK_NOT_FOUND",
                    upstream={"exception": msg},
                ) from exc

            logger.exception("A2A get_task failed for %s/%s: %s", agent_id, task_id, exc)
            raise A2AClientError(
                msg,
                status_code=502,
                code="A2A_UPSTREAM_ERROR",
                upstream={"exception": msg},
            ) from exc

        # Keep mapping conservative and JSON-safe.
        payload: Dict[str, Any] = {"task": self._safe_dump(task)}

        return A2AResult(
            agent_id=agent_id,
            task_id=getattr(task, "id", task_id),
            status=getattr(task, "status", None) or "unknown",
            output=payload,
            message=None,
            raw_response=payload,
        )

    async def aclose(self) -> None:
        """Close underlying http clients."""
        for agent_id, sdk_client in list(self._sdk_clients.items()):
            try:
                close = getattr(sdk_client, "close", None)
                if callable(close):
                    await close()
            except Exception:
                logger.debug("Ignoring error closing SDK client for %s", agent_id)

        for agent_id, httpx_client in list(self._httpx_clients.items()):
            try:
                await httpx_client.aclose()
            except Exception:
                logger.debug("Ignoring error closing httpx client for %s", agent_id)

        self._sdk_clients.clear()
        self._httpx_clients.clear()

    async def _get_sdk_client(self, agent_id: str) -> Any:
        if agent_id in self._sdk_clients:
            return self._sdk_clients[agent_id]

        cfg = self._agent_configs.get(agent_id)
        if cfg is None:
            raise A2AClientError(
                f"Unknown agent_id: {agent_id}",
                status_code=404,
                code="A2A_AGENT_NOT_FOUND",
            )

        base_url = str(getattr(cfg, "base_url", None) or getattr(cfg, "runtime_url", None) or cfg.card_url)
        parsed = urlparse(base_url)
        if not parsed.scheme:
            raise A2AClientError(
                f"Invalid agent base_url/card_url: {base_url}",
                status_code=400,
                code="A2A_BAD_CONFIG",
            )

        headers: Dict[str, str] = {}
        api_key = getattr(cfg, "api_key", None)
        if api_key:
            headers["Authorization"] = f"Bearer {cfg.api_key}"

        httpx_client = httpx.AsyncClient(
            timeout=float(getattr(cfg, "timeout_s", None) or getattr(cfg, "timeout_seconds", None) or 30),
            headers=headers,
            follow_redirects=True,
        )

        self._httpx_clients[agent_id] = httpx_client

        client_config = ClientConfig(httpx_client=httpx_client)

        resolver_http_kwargs = {"headers": headers} if headers else None

        try:
            sdk_client = await ClientFactory.connect(
                agent=base_url,
                client_config=client_config,
                resolver_http_kwargs=resolver_http_kwargs,
            )
        except Exception as exc:
            logger.exception("Failed to connect A2A SDK client for %s: %s", agent_id, exc)
            raise A2AClientError(
                str(exc),
                status_code=502,
                code="A2A_UPSTREAM_ERROR",
                upstream={"exception": str(exc)},
            ) from exc

        self._sdk_clients[agent_id] = sdk_client
        return sdk_client

    def _safe_dump(self, obj: Any) -> Any:
        """Best-effort JSON-safe dump for SDK objects."""
        if obj is None:
            return None
        try:
            if hasattr(obj, "model_dump"):
                return obj.model_dump()
        except Exception:
            pass
        try:
            if hasattr(obj, "dict"):
                return obj.dict()
        except Exception:
            pass
        try:
            json.dumps(obj)
            return obj
        except Exception:
            return {"repr": repr(obj)}

    def _to_result(self, agent_id: str, *, last_task: Any, last_message: Any, last_update: Any) -> A2AResult:
        payload: Dict[str, Any] = {
            "task": self._safe_dump(last_task),
            "update": self._safe_dump(last_update),
            "message": self._safe_dump(last_message),
        }

        task_id = None
        status = None
        if last_task is not None:
            task_id = getattr(last_task, "id", None)
            status = getattr(last_task, "status", None)

        return A2AResult(
            agent_id=agent_id,
            task_id=task_id,
            status=status or "unknown",
            output=payload,
            message=None,
            raw_response=payload,
        )
