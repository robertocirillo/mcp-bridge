"""A2A client wrapper used by mcp-bridge.

This implementation uses the official `a2a-sdk` package.

Design goals:
- Keep the REST API surface stable.
- Centralize agent-card resolution + auth header handling.
- Provide a minimal, predictable mapping for our REST responses.

NOTE:
- Our REST field `blocking` is *not* an A2A protocol field.
  It only controls whether we wait for the final result or return early.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

import httpx

from a2a.client import ClientConfig, ClientFactory, create_text_message_object
from a2a.types import TaskQueryParams

from app.models.config import A2AAgentConfig
from app.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class A2AResult:
    """Internal result container returned by this wrapper."""

    agent_id: str
    task_id: Optional[str]
    status: Optional[str]
    output: Optional[Dict[str, Any]]
    message: Optional[str]
    raw_response: Optional[Dict[str, Any]]


class A2AClientError(RuntimeError):
    """Raised for A2A client wrapper errors."""


class A2AClient:
    """Thin wrapper around `a2a-sdk` client(s).

    The wrapper is responsible for:
    - locating agent configs by agent_id
    - building per-agent http clients (headers/timeouts)
    - resolving agent cards via the SDK
    - sending messages and polling tasks
    """

    def __init__(self, agent_configs: Dict[str, A2AAgentConfig]) -> None:
        self._agent_configs: Dict[str, A2AAgentConfig] = agent_configs or {}

        # Lazily created per-agent SDK clients and their httpx clients
        self._sdk_clients: Dict[str, Any] = {}
        self._httpx_clients: Dict[str, httpx.AsyncClient] = {}

    # -------------------------
    # Public API (used by routes)
    # -------------------------

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
        # create_text_message_object(role=..., content=...) has `role` as the first argument.
        # If you pass a string positionally (create_text_message_object(text)), that string is
        # interpreted as `role` and triggers the validation error you saw.
        message = create_text_message_object(content=text)

        last_task: Optional[Any] = None
        last_message: Optional[Any] = None
        last_update: Optional[Any] = None

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
            raise A2AClientError(str(exc)) from exc

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

        except Exception as exc:
            logger.exception("A2A get_task failed for %s/%s: %s", agent_id, task_id, exc)
            raise A2AClientError(str(exc)) from exc

        # NOTE: We intentionally keep the mapping conservative.
        return A2AResult(
            agent_id=agent_id,
            task_id=getattr(task, "id", task_id),
            status=getattr(task, "status", None) or "unknown",
            output={"task": self._safe_dump(task)},
            message=None,
            raw_response=None,
        )

    async def aclose(self) -> None:
        """Close underlying http clients."""

        for agent_id, sdk_client in list(self._sdk_clients.items()):
            try:
                # SDK client has close() on BaseClient
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

    # -------------------------
    # Internals
    # -------------------------

    async def _get_sdk_client(self, agent_id: str) -> Any:
        if agent_id in self._sdk_clients:
            return self._sdk_clients[agent_id]

        cfg = self._agent_configs.get(agent_id)
        if cfg is None:
            raise A2AClientError(f"Unknown agent_id: {agent_id}")
        if not cfg.enabled:
            raise A2AClientError(f"Agent is disabled: {agent_id}")

        base_url, relative_card_path = self._split_card_url(cfg.card_url)
        headers = self._build_headers(cfg)

        timeout = httpx.Timeout(cfg.timeout_seconds)
        httpx_client = httpx.AsyncClient(
            timeout=timeout,
            headers=headers,
            follow_redirects=True,
        )

        # Keep reference for later close
        self._httpx_clients[agent_id] = httpx_client

        client_config = ClientConfig(httpx_client=httpx_client)

        # Resolver kwargs are used when fetching the card.
        resolver_http_kwargs = {"headers": headers} if headers else None

        try:
            sdk_client = await ClientFactory.connect(
                agent=base_url,
                client_config=client_config,
                relative_card_path=relative_card_path,
                resolver_http_kwargs=resolver_http_kwargs,
            )
        except Exception as exc:
            logger.exception("Failed to connect to A2A agent %s at %s%s: %s", agent_id, base_url, relative_card_path, exc)
            raise A2AClientError(str(exc)) from exc

        self._sdk_clients[agent_id] = sdk_client
        return sdk_client

    @staticmethod
    def _split_card_url(card_url: str) -> Tuple[str, str]:
        """Return (base_url, relative_path) from a full card URL."""

        parsed = urlparse(card_url)
        if not parsed.scheme or not parsed.netloc:
            raise A2AClientError(f"Invalid card_url (expected full URL): {card_url}")

        base_url = f"{parsed.scheme}://{parsed.netloc}"
        relative_path = parsed.path or "/"
        if parsed.query:
            relative_path = f"{relative_path}?{parsed.query}"

        return base_url, relative_path

    @staticmethod
    def _build_headers(cfg: A2AAgentConfig) -> Dict[str, str]:
        headers: Dict[str, str] = dict(cfg.extra_headers or {})

        auth = cfg.auth
        if not auth or auth.type == "none":
            return headers

        if not auth.env_var:
            raise A2AClientError(f"A2A auth configured for agent but env_var is missing (agent label={cfg.label!r})")

        import os

        token = os.getenv(auth.env_var)
        if not token:
            raise A2AClientError(
                f"Missing env var {auth.env_var!r} for A2A agent auth (agent label={cfg.label!r})."
            )

        if auth.type == "bearer_token":
            headers["Authorization"] = f"Bearer {token}"
        elif auth.type == "api_key_header":
            if not auth.header_name:
                raise A2AClientError(
                    f"A2A auth type api_key_header requires header_name (agent label={cfg.label!r})"
                )
            headers[auth.header_name] = token
        else:
            raise A2AClientError(f"Unsupported auth type: {auth.type}")

        return headers

    @staticmethod
    def _safe_dump(obj: Any) -> Any:
        """Best-effort serialization for SDK objects."""

        if obj is None:
            return None

        dump = getattr(obj, "model_dump", None)
        if callable(dump):
            return dump()

        asdict = getattr(obj, "dict", None)
        if callable(asdict):
            return asdict()

        return obj

    def _to_result(
        self,
        agent_id: str,
        *,
        last_task: Optional[Any],
        last_message: Optional[Any],
        last_update: Optional[Any],
    ) -> A2AResult:
        # Prefer Task-based response (it contains task id/status)
        if last_task is not None:
            task_id = getattr(last_task, "id", None)
            status = getattr(last_task, "status", None)
            output: Optional[Dict[str, Any]] = {"task": self._safe_dump(last_task)}

            # Try to include artifact updates if present (optional)
            if last_update is not None:
                output["last_update"] = self._safe_dump(last_update)

            return A2AResult(
                agent_id=agent_id,
                task_id=task_id,
                status=status,
                output=output,
                message=None,
                raw_response=None,
            )

        # Fallback: final Message
        if last_message is not None:
            return A2AResult(
                agent_id=agent_id,
                task_id=None,
                status=None,
                output={"message": self._safe_dump(last_message)},
                message=None,
                raw_response=None,
            )

        # Nothing received
        return A2AResult(
            agent_id=agent_id,
            task_id=None,
            status=None,
            output=None,
            message="No events returned by agent.",
            raw_response=None,
        )
