import logging
from typing import Dict, Any, List, Optional

import httpx
from os import getenv
from app.models.config import A2AAgentConfig
from app.models.requests import A2ATaskRequest
from app.models.responses import A2AAgentInfo, A2ATaskResponse

logger = logging.getLogger(__name__)


class A2AClientError(Exception):
    """Base exception for A2A client errors."""


class A2AAgentNotFoundError(A2AClientError):
    """Raised when a requested A2A agent is not configured."""


class A2AClient:
    """
    Simple client for interacting with remote A2A agents.

    This client is intentionally minimal and focused:
    - it only knows about configured remote agents,
    - it can list them,
    - it can forward tasks to them.
    """

    def __init__(self, agent_configs: Dict[str, A2AAgentConfig]):
        self._agent_configs = agent_configs
        self._cards_cache: Dict[str, Dict[str, Any]] = {}

    def _get_agent_config(self, agent_id: str) -> A2AAgentConfig:
        config = self._agent_configs.get(agent_id)
        if not config:
            raise A2AAgentNotFoundError(f"A2A agent '{agent_id}' is not configured.")
        return config


    async def list_agents(self) -> List[A2AAgentInfo]:
        """
        Returns a list of configured A2A agents, enriched with basic
        information from their agent cards when available.
        """
        agents: List[A2AAgentInfo] = []

        for agent_id, cfg in self._agent_configs.items():
            name = agent_id
            description: Optional[str] = None
            capabilities: Optional[List[str]] = None

            try:
                card = await self._fetch_agent_card(agent_id, cfg)
                name = card.get("name", agent_id)
                description = card.get("description")
                capabilities = card.get("capabilities")
            except Exception as e:
                logger.warning(
                    f"Failed to fetch agent card for '{agent_id}': {e}. "
                    f"Using config-only information."
                )

            agents.append(
                A2AAgentInfo(
                    agent_id=agent_id,
                    name=name,
                    description=description,
                    base_url=str(cfg.base_url),
                    capabilities=capabilities,
                )
            )

        return agents

    def _build_headers(self, cfg: A2AAgentConfig) -> Dict[str, str]:
        """
        Builds request headers based on cfg.extra_headers and cfg.auth.
        Compatible with the project config model (runtime_url/card_url/auth/extra_headers).
        """
        headers: Dict[str, str] = dict(getattr(cfg, "extra_headers", None) or {})

        auth = getattr(cfg, "auth", None)
        if not auth or getattr(auth, "type", "none") == "none":
            return headers

        auth_type = auth.type
        if auth_type == "api_key_header":
            header_name = auth.header_name
            token = getenv(auth.env_var or "", "")
            if header_name and token:
                headers[header_name] = token

        elif auth_type == "bearer_token":
            token = getenv(auth.env_var or "", "")
            if token:
                headers["Authorization"] = f"Bearer {token}"

        return headers

    async def _fetch_agent_card(self, agent_id: str, cfg: A2AAgentConfig) -> Dict[str, Any]:
        """
        Fetch agent card using cfg.card_url (full URL), as defined in PROJECT_CONTEXT.
        """
        if agent_id in self._cards_cache:
            return self._cards_cache[agent_id]

        url = str(cfg.card_url)
        headers = self._build_headers(cfg)

        logger.debug("Fetching A2A agent card for '%s' from %s", agent_id, url)

        async with httpx.AsyncClient(timeout=cfg.timeout_seconds) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            card = response.json()

        self._cards_cache[agent_id] = card
        return card

    async def send_task(self, agent_id: str, task: A2ATaskRequest) -> A2ATaskResponse:
        """
        Post task to cfg.runtime_url + '/tasks' (HTTP shim), compatible with the local echo agent.
        """
        cfg = self._get_agent_config(agent_id)

        runtime_url = getattr(cfg, "runtime_url", None)
        if not runtime_url:
            raise A2AClientError(f"A2A agent '{agent_id}' runtime_url is not configured.")

        url = str(runtime_url).rstrip("/") + "/tasks"
        headers = self._build_headers(cfg)

        payload: Dict[str, Any] = {
            "goal": task.goal,
            "input": task.input,
            "metadata": task.metadata or {},
        }
        if task.task_id:
            payload["taskId"] = task.task_id

        logger.debug("Sending A2A task to '%s' at %s", agent_id, url)

        async with httpx.AsyncClient(timeout=cfg.timeout_seconds) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

        remote_task_id = data.get("taskId") or task.task_id or ""
        status = data.get("status", "unknown")
        output = data.get("output")
        message = data.get("message")

        return A2ATaskResponse(
            task_id=remote_task_id,
            status=status,
            output=output,
            message=message,
            raw_response=data,
        )