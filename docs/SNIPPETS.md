# SNIPPETS.md

Architecturally relevant code fragments for **mcp-bridge – MCP + A2A integration**.
These are **not** meant to be copy-paste complete, but to preserve important structures and patterns.

---

## 1. Settings and Configuration

### 1.1 Global Settings (`config.py`)

```python
"""MCP-BRIDGE REST API global settings"""

from pydantic_settings import BaseSettings
from typing import List
import os
from dotenv import load_dotenv
from app.models.config import A2ASettings, A2AAgentConfig, MultiTenancySettings

load_dotenv()


class Settings(BaseSettings):
    """Global settings for MCP-BRIDGE"""

    # API Settings
    API_TITLE: str = "mcp-bridge: REST API for mcp-use library"
    API_DESCRIPTION: str = (
        "A modular and scalable REST service to interact with MCP servers using the mcp-use library"
    )
    API_VERSION: str = "0.1.0-beta"

    # Server Settings
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = False

    # CORS Settings
    CORS_ORIGINS: List[str] = ["*"]

    # Session Settings
    MAX_ACTIVE_SESSIONS: int = 100
    SESSION_TIMEOUT: int = 3600  # seconds

    # MCP Settings
    DEFAULT_MAX_STEPS: int = 30
    SUPPORTED_PROVIDERS: List[str] = ["openai", "anthropic", "ollama"]

    # Logging Settings
    LOG_LEVEL: str = "DEBUG"
    LOG_FORMAT: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    # E2B Sandbox Settings
    E2B_API_KEY: str = os.getenv("E2B_API_KEY", "")
    DEFAULT_SANDBOX_TEMPLATE: str = "base"

    OPENAI_API_KEY: str | None = None
    ANTHROPIC_API_KEY: str | None = None
    OLLAMA_BASE_URL: str | None = None

    LANGFUSE_PUBLIC_KEY: str | None = None
    LANGFUSE_SECRET_KEY: str | None = None
    LANGFUSE_HOST: str | None = None

    # A2A
    a2a: A2ASettings = A2ASettings(
        enabled=True,
        agents={
            "local_echo_agent": A2AAgentConfig(
                card_url="http://localhost:9001/.well-known/agent.json",
                runtime_url="http://localhost:9001",
                timeout_seconds=60,
                enabled=True,
                label="Local Echo Agent",
                description="Simple local A2A agent used for testing.",
            )
        },
    )

    # MultiTenancy
    multi_tenancy: MultiTenancySettings = MultiTenancySettings()

    class Config:
        env_file = ".env"
        case_sensitive = True


# global settings instance
settings = Settings()
```

### 1.2 A2A Config Models (`app/models/config.py`)

```python
from typing import Dict, Optional
from typing_extensions import Literal
from pydantic import BaseModel, Field


class A2AAuthConfig(BaseModel):
    type: Literal["none", "api_key_header", "bearer_token"] = "none"
    header_name: Optional[str] = None
    env_var: Optional[str] = None


class A2AAgentConfig(BaseModel):
    enabled: bool = True
    label: Optional[str] = None
    description: Optional[str] = None

    card_url: str
    runtime_url: Optional[str] = None
    timeout_seconds: int = 60

    auth: Optional[A2AAuthConfig] = None
    extra_headers: Dict[str, str] = Field(default_factory=dict)


class A2ASettings(BaseModel):
    enabled: bool = True
    agents: Dict[str, A2AAgentConfig] = Field(default_factory=dict)
```

### 1.3 Multi-Tenancy Settings (`app/models/config.py`)

```python
class MultiTenancySettings(BaseModel):
    enabled: bool = False
    require_header: bool = False
    default_tenant_id: Optional[str] = "default"
```

---

## 2. Dependency Injection & Tenant Context

### 2.1 Dependencies (`app/api/dependencies.py`)

```python
"""FastAPI dependencies"""

from functools import lru_cache
from typing import Dict, Optional, Annotated

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel

from app.core.session_manager import SessionManager
from app.core.a2a_client import A2AClient
from app.models.config import A2AAgentConfig
from config import Settings, settings


# Session manager singleton
_session_manager: SessionManager | None = None


def get_session_manager() -> SessionManager:
    """Dependency injection session manager"""
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager


@lru_cache()
def get_settings() -> Settings:
    """Dependency injection settings (cached)"""
    return settings


def get_a2a_client(
    settings: Settings = Depends(get_settings),
) -> A2AClient:
    """Dependency that provides a configured A2AClient instance."""

    agent_configs: Dict[str, A2AAgentConfig] = settings.a2a.agents or {}
    return A2AClient(agent_configs=agent_configs)


class TenantContext(BaseModel):
    """Resolved tenant context for the current request."""
    tenant_id: str
    run_id: Optional[str] = None


def get_tenant_context(
    x_tenant_id: Annotated[Optional[str], Header(alias="X-Tenant-Id")] = None,
    x_run_id: Annotated[Optional[str], Header(alias="X-Run-Id")] = None,
    settings: Settings = Depends(get_settings),
) -> TenantContext:
    """Resolve tenant_id and run_id based on headers and multi-tenancy settings.

    Modes:

    - multi_tenancy.enabled = False:
        * Ignore headers, always use default_tenant_id (or "default").
    - enabled = True, require_header = False:
        * Use X-Tenant-Id if present, otherwise default_tenant_id (or "default").
    - enabled = True, require_header = True:
        * If X-Tenant-Id is missing, raise HTTP 400.
    """

    mt = settings.multi_tenancy

    if not mt.enabled:
        tenant_id = mt.default_tenant_id or "default"
    else:
        if x_tenant_id:
            tenant_id = x_tenant_id
        else:
            if mt.require_header:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Missing X-Tenant-Id header (multi-tenancy is enabled and "
                        "require_header = true)."
                    ),
                )
            tenant_id = mt.default_tenant_id or "default"

    run_id = x_run_id
    return TenantContext(tenant_id=tenant_id, run_id=run_id)
```

---

## 3. Session Manager & Session Data

### 3.1 SessionData (`app/core/session_manager.py`)

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.models.config import SessionConfig
from app.core.mcp_wrapper import MCPWrapper


@dataclass
class SessionData:
    session_id: str
    config: SessionConfig
    wrapper: MCPWrapper
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_used: datetime = field(default_factory=datetime.utcnow)
    query_count: int = 0

    tenant_id: Optional[str] = None
    last_run_id: Optional[str] = None

    def update_last_used(self) -> None:
        self.last_used = datetime.utcnow()

    def register_query(self) -> None:
        self.query_count += 1
```

---

## 4. MCPWrapper – High-Level Shape

> NOTE: This is conceptual; exact implementation is in `app/core/mcp_wrapper.py` and uses `mcp-use`.

```python
from typing import Any, Dict, Optional

from app.utils.logging import get_logger

logger = get_logger(__name__)


class MCPWrapper:
    def __init__(
        self,
        llm_provider: str,
        model: str,
        api_key: Optional[str],
        base_url: Optional[str],
        temperature: float,
        max_tokens: Optional[int],
        mcp_servers: Dict[str, Any],
        max_steps: int,
        verbose: bool,
        sandbox: bool,
        sandbox_options: Optional[Dict[str, Any]],
        disallowed_tools: Optional[list[str]],
        use_server_manager: bool,
    ) -> None:
        self.llm_provider = llm_provider
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.mcp_servers = mcp_servers
        self.max_steps = max_steps
        self.verbose = verbose
        self.sandbox = sandbox
        self.sandbox_options = sandbox_options or {}
        self.disallowed_tools = disallowed_tools or []
        self.use_server_manager = use_server_manager

        self.steps_used: int = 0
        self.last_server_used: Optional[str] = None

        # Internal mcp-use client / agent will be set in initialize()
        self._agent = None

    async def initialize(self) -> None:
        """Initialize the mcp-use agent and connect to MCP servers.

        Must handle the case where mcp_servers is empty (LLM-only mode).
        """
        logger.info("Initializing MCPWrapper with provider %s and model %s", self.llm_provider, self.model)

    async def run_query(
        self,
        query: str,
        max_steps: Optional[int] = None,
        server_name: Optional[str] = None,
    ) -> Any:
        """Execute a query using mcp-use agent."""
        logger.debug("Executing query: %s", query[:100])
        effective_max_steps = max_steps if max_steps and max_steps > 0 else self.max_steps
        raise NotImplementedError
```

---

## 5. FastAPI Routes – MCP Sessions & Queries

### 5.1 Create Session (`POST /sessions`)

```python
from typing import List

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_session_manager, get_tenant_context, TenantContext
from app.core.session_manager import SessionManager
from app.core.exceptions import (
    MaxSessionsExceededError,
    SessionNotFoundError,
    ConfigurationError,
    MCPWrapperError,
)
from app.models.requests import SessionCreateRequest
from app.models.responses import SessionResponse
from app.utils.logging import get_logger

router = APIRouter()
logger = get_logger(__name__)


@router.post("", response_model=SessionResponse)
async def create_session(
    request: SessionCreateRequest,
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    session_manager: SessionManager = Depends(get_session_manager),
):
    """Create new MCP session."""
    try:
        # request already is a SessionConfig (inherits from SessionConfig)
        config = request

        session_id = await session_manager.create_session(
            config,
            tenant_id=tenant_ctx.tenant_id,
            run_id=tenant_ctx.run_id,
        )

        return SessionResponse(
            session_id=session_id,
            status="created",
            message="Session created successfully",
            servers=list(config.mcp_servers.keys()),
        )

    except MaxSessionsExceededError as e:
        logger.warning("Limit exceeded %s", e)
        raise HTTPException(status_code=429, detail=str(e))
    except ConfigurationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except MCPWrapperError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Internal Error")
```

### 5.2 List Sessions (`GET /sessions`)

```python
from typing import List

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_session_manager, get_tenant_context, TenantContext
from app.core.session_manager import SessionManager
from app.core.exceptions import SessionNotFoundError, ConfigurationError, MCPWrapperError
from app.models.responses import SessionInfo
from app.utils.logging import get_logger

router = APIRouter()
logger = get_logger(__name__)


@router.get("", response_model=List[SessionInfo])
async def list_sessions(
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    session_manager: SessionManager = Depends(get_session_manager),
):
    """Active sessions list (tenant-scoped)."""
    try:
        sessions_data = await session_manager.list_sessions(tenant_id=tenant_ctx.tenant_id)
        return [SessionInfo(**data) for data in sessions_data]

    except SessionNotFoundError as e:
        logger.warning("Session not found %s", e)
        raise HTTPException(status_code=429, detail=str(e))
    except ConfigurationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except MCPWrapperError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Internal Error")
```

### 5.3 Execute Query (`POST /sessions/{session_id}/query`)

```python
from datetime import datetime
import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_session_manager, get_tenant_context, TenantContext
from app.core.session_manager import SessionManager
from app.core.exceptions import SessionNotFoundError, ConfigurationError, MCPWrapperError
from app.models.requests import QueryRequest
from app.models.responses import QueryResponse
from app.utils.logging import get_logger

router = APIRouter()
logger = get_logger(__name__)


@router.post("/{session_id}/query", response_model=QueryResponse)
async def execute_query(
    session_id: str,
    request: QueryRequest,
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    session_manager: SessionManager = Depends(get_session_manager),
):
    """Execute a query on an existing session."""
    try:
        session_data = await session_manager.get_session_for_tenant(
            session_id, tenant_ctx.tenant_id
        )
        wrapper = session_data.wrapper

        start_time = asyncio.get_event_loop().time()

        result = await wrapper.run_query(
            query=request.query,
            max_steps=request.max_steps,
            server_name=request.server_name,
        )

        end_time = asyncio.get_event_loop().time()
        execution_time = end_time - start_time

        session_data.register_query()

        steps_used = wrapper.steps_used
        server_used = getattr(wrapper, "last_server_used", None)

        return QueryResponse(
            session_id=session_id,
            result=result,
            execution_time=execution_time,
            steps_used=steps_used,
            timestamp=datetime.now(),
            server_used=server_used,
        )

    except SessionNotFoundError as e:
        logger.warning("Session not found: %s", e)
        raise HTTPException(status_code=404, detail=str(e))
    except ConfigurationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except MCPWrapperError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Internal Error")
```

### 5.4 Delete Session (`DELETE /sessions/{session_id}`)

```python
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from app.api.dependencies import get_session_manager, get_tenant_context, TenantContext
from app.core.session_manager import SessionManager
from app.core.exceptions import SessionNotFoundError, ConfigurationError, MCPWrapperError
from app.utils.logging import get_logger

router = APIRouter()
logger = get_logger(__name__)


@router.delete("/{session_id}")
async def delete_session(
    session_id: str,
    background_tasks: BackgroundTasks,
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    session_manager: SessionManager = Depends(get_session_manager),
):
    """Delete a session by ID (tenant-scoped)."""
    try:
        background_tasks.add_task(
            session_manager.delete_session_for_tenant,
            session_id,
            tenant_ctx.tenant_id,
        )

        return {"message": f"Session {session_id} deleted successfully"}

    except SessionNotFoundError as e:
        logger.warning("Deleting not found session: %s", e)
        raise HTTPException(status_code=404, detail=str(e))
    except ConfigurationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except MCPWrapperError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Internal Error")
```

---

## 6. A2A REST Models and Routes (Current HTTP Shim)

### 6.1 A2A Request/Response Models (`app/models/requests.py`, `app/models/responses.py`)

```python
# requests.py
from typing import Any, Dict, Optional
from pydantic import BaseModel


class A2AMessageRequest(BaseModel):
    goal: str
    input: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    blocking: bool = True
    client_task_id: Optional[str] = None
```

```python
# responses.py
from typing import Any, Dict, List, Optional
from typing_extensions import Literal
from pydantic import BaseModel


class A2AAgentSummary(BaseModel):
    agent_id: str
    name: str
    description: Optional[str] = None
    card_url: Optional[str] = None
    skills: List[str] = []
    labels: List[str] = []


class A2AMessageResponse(BaseModel):
    mode: Literal["blocking", "task"]
    agent_id: str
    task_id: Optional[str]
    status: Optional[str]
    output: Optional[Dict[str, Any]]
    message: Optional[str]
    raw_response: Optional[Dict[str, Any]]


class A2ATaskStatusResponse(BaseModel):
    agent_id: str
    task_id: str
    status: str
    output: Optional[Dict[str, Any]]
    message: Optional[str]
    raw_response: Optional[Dict[str, Any]]
```

### 6.2 A2A Routes (`app/api/routes/a2a.py` – simplified)

```python
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_settings
from app.models.responses import A2AAgentSummary, A2AMessageResponse
from app.models.requests import A2AMessageRequest
from app.utils.logging import get_logger
from config import Settings

router = APIRouter()
logger = get_logger(__name__)


@router.get("/agents", response_model=List[A2AAgentSummary])
async def list_a2a_agents(
    settings: Settings = Depends(get_settings),
):
    """List configured A2A agents from settings."""
    try:
        a2a_settings = settings.a2a
        if not a2a_settings.enabled:
            return []

        summaries: List[A2AAgentSummary] = []
        for agent_id, conf in a2a_settings.agents.items():
            if not conf.enabled:
                continue

            summaries.append(
                A2AAgentSummary(
                    agent_id=agent_id,
                    name=conf.label or agent_id,
                    description=conf.description,
                    card_url=conf.card_url,
                    skills=[],   # future: from AgentCard
                    labels=[],   # future: from config/card
                )
            )

        return summaries
    except Exception as exc:
        logger.exception("Error listing A2A agents: %s", exc)
        raise HTTPException(status_code=500, detail="Error listing A2A agents")
```

### 6.3 A2A Task Status (HTTP shim)

`GET /a2a/agents/{agent_id}/tasks/{task_id}` tries `GET {runtime_url}/tasks/{task_id}` and falls back gracefully if the runtime doesn’t implement polling (common for the local echo agent).
It builds outbound headers from `conf.extra_headers` + `conf.auth` (env_var-based).


---

## 7. Local Echo A2A Agent Example

This is the minimal example A2A-like agent used for local testing.

```python
from typing import Any, Dict, Optional
import uuid

from fastapi import FastAPI
from pydantic import BaseModel, Field


app = FastAPI(
    title="Local Echo A2A Agent",
    description="A minimal A2A-like echo agent for testing mcp-bridge integration.",
    version="0.1.0",
)


class AgentCard(BaseModel):
    name: str
    description: Optional[str] = None
    version: str = "0.1.0"
    capabilities: Optional[list[str]] = None
    provider: Optional[str] = None


class TaskRequest(BaseModel):
    goal: str = Field(..., description="High-level goal or instruction for this agent.")
    input: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Structured input payload for the task.",
    )
    taskId: Optional[str] = Field(
        default=None,
        description="Optional client-provided task identifier.",
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional metadata (tenant, correlation id, etc.).",
    )


class TaskResponse(BaseModel):
    taskId: str
    status: str = Field(..., description="Task status: completed, failed, etc.")
    output: Optional[Dict[str, Any]] = None
    message: Optional[str] = None


@app.get("/.well-known/agent.json", response_model=AgentCard)
async def get_agent_card() -> AgentCard:
    return AgentCard(
        name="Local Echo Agent",
        description=(
            "A simple A2A-compatible echo agent used for testing. "
            "It echoes back the goal and input in the output payload."
        ),
        version="0.1.0",
        capabilities=["echo", "debug"],
        provider="local-dev",
    )


@app.post("/tasks", response_model=TaskResponse)
async def handle_task(request: TaskRequest) -> TaskResponse:
    task_id = request.taskId or str(uuid.uuid4())

    output: Dict[str, Any] = {
        "echo_goal": request.goal,
        "echo_input": request.input,
        "info": "This is a test echo agent. Replace this logic with real work.",
    }

    return TaskResponse(
        taskId=task_id,
        status="completed",
        output=output,
        message="Task handled successfully by Local Echo Agent.",
    )


# To run from CLI:
# uvicorn main:app --reload --port 9001
```

---

## 8. Example HTTP Calls

### 8.1 Create MCP Session (with MCP server)

```bash
curl -X POST "http://localhost:8000/sessions" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: tenant-123" \
  -d '{
    "llm_provider": {
      "provider": "openai",
      "model": "gpt-4.1-mini",
      "temperature": 0.7
    },
    "mcp_servers": {
      "filesystem": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
      }
    },
    "max_steps": 30,
    "verbose": false
  }'
```

### 8.2 Create LLM-only MCP Session (no MCP servers)

```bash
curl -X POST "http://localhost:8000/sessions" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: tenant-llm-only" \
  -d '{
    "llm_provider": {
      "provider": "openai",
      "model": "gpt-4.1-mini",
      "temperature": 0.7
    },
    "mcp_servers": {},
    "max_steps": 5,
    "verbose": false
  }'
```

### 8.3 List Sessions for a Tenant

```bash
curl -X GET "http://localhost:8000/sessions" \
  -H "X-Tenant-Id: tenant-123"
```

### 8.4 Query a Session

```bash
curl -X POST "http://localhost:8000/sessions/{session_id}/query" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: tenant-123" \
  -d '{
    "query": "List the files in the directory",
    "max_steps": 10,
    "server_name": "filesystem"
  }'
```

### 8.5 List A2A Agents

```bash
curl http://localhost:8000/a2a/agents
```

### 8.6 Call Local Echo A2A Agent

```bash
curl -X POST "http://localhost:8000/a2a/agents/local_echo_agent/messages" \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "Test the echo agent through mcp-bridge",
    "input": { "foo": "bar", "number": 42 },
    "metadata": {},
    "blocking": true
  }'
```

---

## 9. A2AClient Compatibility Note

`A2AClient.send_task()` must build:

* Agent card URL from `cfg.card_url` (full URL)
* Tasks URL from `cfg.runtime_url.rstrip("/") + "/tasks"`

Headers must be built from:

* `cfg.extra_headers`
* `cfg.auth` (api_key_header / bearer_token via env var)

Do not use legacy/alternative URL fields like `base_url`, `task_endpoint`, or `card_path` in this project version.


## A2A SDK smoke test (HelloWorld agent)

Agent card:
```bash
curl -s http://localhost:9999/.well-known/agent.json | jq

curl -s -X POST "http://localhost:8000/a2a/agents/helloworld/messages" \
  -H "Content-Type: application/json" \
  -d '{ "goal": "hi", "blocking": true, "metadata": {} }' | jq
```

## A2A SDK smoke test (HelloWorld agent)

Agent card:
```bash
curl -s http://localhost:9999/.well-known/agent.json | jq

curl -s -X POST "http://localhost:8000/a2a/agents/helloworld/messages" \
  -H "Content-Type: application/json" \
  -d '{ "goal": "hi", "blocking": false, "metadata": {} }' | jq


```