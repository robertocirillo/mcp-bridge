import logging
from typing import Dict, Any, List, Optional

import httpx

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

    async def _fetch_agent_card(
            self,
            agent_id: str,
            config: A2AAgentConfig,
    ) -> Dict[str, Any]:
        """
        Fetches the agent card from the remote A2A agent, if available.

        The result is cached in memory.
        """
        if agent_id in self._cards_cache:
            return self._cards_cache[agent_id]

        # Convert AnyHttpUrl to string before using string methods
        base_url = str(config.base_url).rstrip("/")
        url = base_url + config.card_path
        logger.debug(f"Fetching A2A agent card for '{agent_id}' from {url}")

        async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
            response = await client.get(url)
            response.raise_for_status()
            card = response.json()

        self._cards_cache[agent_id] = card
        return card

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

    async def send_task(
        self,
        agent_id: str,
        task: A2ATaskRequest,
    ) -> A2ATaskResponse:
        """
        Forwards a task to the remote A2A agent and wraps the response.

        This method does NOT attempt to implement full A2A server semantics;
        it simply forwards the request and wraps the response in a stable shape.
        """
        cfg = self._get_agent_config(agent_id)

        base_url = str(cfg.base_url).rstrip("/")
        url = base_url + "/" + cfg.task_endpoint.lstrip("/")
        headers: Dict[str, str] = {}

        if cfg.auth_header and cfg.auth_token:
            headers[cfg.auth_header] = cfg.auth_token

        payload: Dict[str, Any] = {
            "goal": task.goal,
            "input": task.input,
            "metadata": task.metadata or {},
        }

        if task.task_id:
            payload["taskId"] = task.task_id

        logger.debug(f"Sending A2A task to '{agent_id}' at {url}")

        async with httpx.AsyncClient(timeout=cfg.timeout_seconds) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

        # Map the remote response into our wrapper model
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
