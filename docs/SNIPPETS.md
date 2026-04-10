# SNIPPETS.md

Architecturally relevant code fragments for **mcp-bridge – MCP + A2A integration**.
These are **not** meant to be copy-paste complete, but to preserve important structures and patterns.

---

## 1. Settings and Configuration

### 1.1 Global Settings (`config.py`)

```python
"""Global settings for mcp-bridge."""

from pydantic_settings import BaseSettings
from typing import List
import os
from dotenv import load_dotenv
from app.models.config import A2ASettings, A2AAgentConfig, MultiTenancySettings

load_dotenv()


class Settings(BaseSettings):
    """Global settings for mcp-bridge."""

    # API Settings
    API_TITLE: str = "mcp-bridge"
    API_DESCRIPTION: str = (
        "REST bridge for MCP capabilities with session-scoped guardrails, powered by mcp-use."
    )
    API_VERSION: str = "0.2.0"

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
                runtime_url="http://localhost:9001",  # legacy (HTTP shim); not used by the SDK-based A2AClient
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
    runtime_url: Optional[str] = None  # legacy (HTTP shim); not used by the SDK-based A2AClient
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

from app.core.sessions.manager import SessionManager
from app.core.clients.a2a_client import A2AClient
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

### 3.1 Session Primitives (`app/core/sessions/store.py`)

```python
from datetime import datetime
from typing import Optional

from app.core.runtime.mcp_wrapper import MCPWrapper
from app.models.config import SessionConfig


class SessionData:
    """Active session state stored in memory."""

    def __init__(
        self,
        session_id: str,
        config: SessionConfig,
        wrapper: MCPWrapper,
        tenant_id: Optional[str] = None,
        last_run_id: Optional[str] = None,
    ):
        self.session_id = session_id
        self.config = config
        self.wrapper = wrapper
        self.created_at = datetime.now()
        self.last_used = datetime.now()
        self.status = "active"
        self.query_count = 0
        self.tenant_id = tenant_id
        self.last_run_id = last_run_id

    def update_last_used(self) -> None:
        self.last_used = datetime.now()

    def register_query(self) -> None:
        self.query_count += 1
        self.update_last_used()
```

### 3.2 SessionManager Composition (`app/core/sessions/manager.py`)

> NOTE: This is intentionally high-level. `SessionManager` remains the public facade, but session state, async query-operation state, and pending interactions now live in dedicated modules.

```python
import asyncio

from app.core.sessions.query_operation_store import QueryOperationStore
from app.core.sessions.interactions import PendingInteractionStore
from app.core.sessions.store import SessionStore


class SessionManager:
    def __init__(self):
        self._session_store = SessionStore()
        self._sessions = self._session_store.sessions
        self._query_operation_store = QueryOperationStore()
        self._interaction_store = PendingInteractionStore()
        self._lock = asyncio.Lock()

    async def create_session(self, config, tenant_id=None, run_id=None) -> str:
        ...

    async def get_session(self, session_id: str, tenant_id=None):
        ...

    async def list_sessions(self, tenant_id=None):
        return self._session_store.list_sessions(tenant_id=tenant_id)

    async def delete_session(self, session_id: str, tenant_id=None):
        ...

    async def create_query_operation(self, session_id: str, request, tenant_id=None, run_id=None):
        ...

    async def resume_query_operation(self, session_id: str, operation_id: str, request, tenant_id=None):
        ...
```

---

## 4. MCPWrapper – High-Level Shape

> NOTE: This is conceptual; the public entry point is now `app/core/runtime/mcp_wrapper.py`, while the MCP boundary is split across focused internal modules.

```python
from app.core.guardrails import wrapper as mcp_wrapper_guardrails
from app.core.runtime import capabilities as mcp_wrapper_capabilities
from app.core.runtime import tools as mcp_wrapper_tools


class MCPWrapper:
    """Public facade around mcp-use and bridge-side runtime policies."""

    def __init__(self, ..., mcp_servers, ..., disallowed_tools=None, ...):
        self.mcp_servers = mcp_servers or {}
        self.has_mcp_servers = bool(self.mcp_servers)

        self.tool_policy_engine = ...
        self.guardrail_runner = ...
        self.audit_recorder = ...

        self.steps_used = 0
        self.last_server_used = None

    async def initialize(self) -> None:
        # Provider/runtime bootstrap stays in runtime/llm.py
        # Transport/session guards stay in runtime/transport.py
        # Capability helpers live in runtime/capabilities.py
        # Direct tool/task helpers live in runtime/tools.py
        ...

    async def run_query(self, query: str, max_steps=None, server_name=None):
        ...

    async def list_prompts(self, server_name=None):
        ...

    async def render_prompt(self, prompt_name: str, arguments=None, server_name=None):
        ...

    async def list_resources(self, server_name=None):
        ...

    async def read_resource(self, uri: str, server_name=None):
        ...

    async def call_tool(self, tool_name: str, arguments=None, server_name=None):
        ...
```

Internal split reflected by the current codebase:

- `runtime/capabilities.py`: prompt/resource capability lookup and invocation helpers
- `runtime/tools.py`: direct tool execution, task-support detection, raw MCP task transport
- `guardrails/wrapper.py`: shared guardrail pipeline wiring and tool-result wrapping helpers
- `runtime/llm.py`: provider/runtime bootstrap and sandbox normalization
- `runtime/transport.py`: guarded MCP client/session proxies
- `guardrails/pii.py`: PII guardrails
- `guardrails/bias.py`: bias guardrails and detector integration
- `runtime/errors.py`: boundary-specific exceptions

---

## 5. FastAPI Route Layer – Thin Routers, Dedicated Services

> NOTE: The older pattern where routes directly orchestrated sessions and mapped exceptions is obsolete. The current structure keeps routers thin and delegates to `app/api/services/*`, `app/api/session_context.py`, and `app/api/error_mapping.py`.

### 5.1 Public Routers (`app/api/routes/sessions.py`, `app/api/routes/queries.py`)

```python
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import TenantContext, get_session_manager, get_tenant_context
from app.api.services import query_service, session_service
from app.models.requests import QueryRequest, SessionCreateRequest
from app.models.responses import QueryResponse, SessionResponse

TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]
router = APIRouter()


@router.post("", response_model=SessionResponse)
async def create_session(request: SessionCreateRequest, tenant_ctx: TenantDep, session_manager=Depends(get_session_manager)):
    return await session_service.create_session(
        request=request,
        tenant_ctx=tenant_ctx,
        session_manager=session_manager,
    )


@router.post("/{session_id}/query", response_model=QueryResponse)
async def execute_query(session_id: str, request: QueryRequest, tenant_ctx: TenantDep, session_manager=Depends(get_session_manager)):
    return await query_service.execute_query(
        session_id=session_id,
        request=request,
        tenant_ctx=tenant_ctx,
        session_manager=session_manager,
    )
```

### 5.2 Session/Wrapper Context Helpers (`app/api/session_context.py`)

```python
async def get_tenant_session(*, session_id: str, tenant_ctx: TenantContext, session_manager: SessionManager):
    return await session_manager.get_session(
        session_id=session_id,
        tenant_id=tenant_ctx.tenant_id,
    )


def bind_wrapper_context(wrapper, *, tenant_ctx: TenantContext, session_id: str):
    wrapper.set_context(
        tenant_id=tenant_ctx.tenant_id,
        run_id=tenant_ctx.run_id,
        session_id=session_id,
    )
    return wrapper
```

### 5.3 Session Route Services (`app/api/services/session_service.py`)

```python
async def create_session(*, request: SessionCreateRequest, tenant_ctx: TenantContext, session_manager: SessionManager):
    try:
        session_id = await session_manager.create_session(
            config=request,
            tenant_id=tenant_ctx.tenant_id,
            run_id=tenant_ctx.run_id,
        )
        return SessionResponse(
            session_id=session_id,
            status="created",
            message="Session created successfully",
            servers=list(request.mcp_servers.keys()),
        )
    except Exception as exc:
        raise map_basic_session_error(exc) from exc


async def list_resources(*, session_id: str, tenant_ctx: TenantContext, server_name: str | None, session_manager: SessionManager):
    wrapper = await get_owned_wrapper(
        session_id=session_id,
        tenant_ctx=tenant_ctx,
        session_manager=session_manager,
    )
    result = await wrapper.list_resources(server_name=server_name)
    ...
```

### 5.4 Query Route Services (`app/api/services/query_service.py`)

```python
async def execute_query(*, session_id: str, request: QueryRequest, tenant_ctx: TenantContext, session_manager: SessionManager):
    session_data = await session_manager.get_session(session_id)
    wrapper = bind_wrapper_context(
        session_data.wrapper,
        tenant_ctx=tenant_ctx,
        session_id=session_id,
    )

    result = await wrapper.run_query(
        query=request.query,
        max_steps=request.max_steps,
        server_name=request.server_name,
    )

    return QueryResponse(
        session_id=session_id,
        result=result,
        execution_time=...,
        steps_used=wrapper.steps_used,
        timestamp=...,
        server_used=wrapper.last_server_used,
        has_mcp_servers=wrapper.has_mcp_servers,
    )
```

---

## 6. A2A REST Models and Routes (A2A integration)

> Note: The bridge now uses **a2a-sdk**. Any HTTP-shim snippets are legacy and kept only for historical context.

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

### 6.X Structured A2A error payload (REST contract)

All A2A endpoints return structured errors under `detail`:

```json
{
  "detail": {
    "code": "A2A_UPSTREAM_ERROR",
    "message": "Timed out contacting agent",
    "operation": "send_message",
    "agent_id": "<agent_id>",
    "task_id": "<task_id or null>",
    "field": "<optional field name>",
    "upstream": { "optional": "payload" }
  }
}
```

Key codes used by the A2A contract tests include:
- `A2A_DISABLED`, `A2A_AGENT_NOT_FOUND`, `A2A_SCHEMA_ERROR`, `A2A_TASK_NOT_APPLICABLE`, `A2A_TASK_NOT_FOUND`, `A2A_UPSTREAM_ERROR`, `A2A_INTERNAL_ERROR`.


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

### 6.3 A2A Task Status (SDK-based)

`GET /a2a/agents/{agent_id}/tasks/{task_id}` uses the official **a2a-sdk** `get_task(...)` via `A2AClient`.

Bridge-level contract notes:
- Returned `status` is normalized to `queued|running|succeeded|failed|unknown`.
- Message-only agents (task polling not applicable) → HTTP 409 with structured error `code="A2A_TASK_NOT_APPLICABLE"` and `operation="get_task"`.
- Task id not found → HTTP 404 with structured error `code="A2A_TASK_NOT_FOUND"` and `operation="get_task"`.
- Transport/connect/timeout issues are mapped using the same structured A2A error schema under `detail`.

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

`A2AClient` uses **a2a-sdk** and resolves agents via `cfg.card_url` (full URL).

Important SDK details:

* Create a text message using keyword argument:

  * ✅ `create_text_message_object(content=text)`
  * ❌ `create_text_message_object(text)` (the first positional argument is `role` and will fail validation)

* Different `a2a-sdk` versions may have different `send_message(...)` signatures
  (e.g., `request_metadata` may be supported or not). The bridge should align its wrapper
  with the installed SDK version or gate optional kwargs based on the available signature.

## A2A SDK smoke test (HelloWorld agent)

Agent card:

```bash
curl -s http://localhost:9999/.well-known/agent.json | jq
```

Blocking call via mcp-bridge:

```bash
curl -s -X POST "http://localhost:8000/a2a/agents/helloworld/messages" \
  -H "Content-Type: application/json" \
  -d '{ "goal": "hi", "blocking": true, "metadata": {} }' | jq
```

Non-blocking call (agent may still return Message-only):

```bash
curl -s -X POST "http://localhost:8000/a2a/agents/helloworld/messages" \
  -H "Content-Type: application/json" \
  -d '{ "goal": "hi", "blocking": false, "metadata": {} }' | jq
```

Expected with HelloWorld:

* `task_id` may be `null` because the agent can return a final `Message` directly.
* REST `mode` should reflect the actual response (`"task"` only when `task_id` is present, otherwise `"blocking"`).
