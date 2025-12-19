# PROJECT_CONTEXT.md

Project: **mcp-bridge – MCP + A2A integration**

---

## 1. Project Goal

**mcp-bridge** is a FastAPI-based REST service that:

1. Exposes a **REST API** for:

   * Managing MCP sessions (LLM + MCP servers via `mcp-use`)
   * Executing queries through those sessions
   * Calling external **A2A agents** (remote/local, third-party)

2. Acts as a **bridge** between:

   * Visual builders / UIs / workflow tools (HTTP/JSON)
   * The **MCP ecosystem** (via `mcp-use`)
   * The **A2A ecosystem** (today: minimal HTTP integration, target: official A2A SDK)

3. Supports **multi-tenancy**, so the same mcp-bridge deployment can be safely used by multiple tenants (e.g. multiple users/applications) without leaking sessions between them.

---

## 2. High-Level Architecture

### 2.1 Components

* **FastAPI app (`main.py`)**

  * Defines the API
  * Mounts routers:

    * `/sessions` (MCP sessions)
    * `/sessions/{id}/query` (MCP queries)
    * `/a2a/agents` (A2A discovery)
    * `/a2a/agents/{agent_id}/messages` (A2A invocation)
    * `/health` and other monitoring endpoints

* **Configuration (`config.py`)**

  * `Settings` (pydantic `BaseSettings`)
  * Handles:

    * API metadata (title, description, version)
    * Host/port/debug
    * Session limits
    * LLM provider keys and URLs
    * E2B sandbox configuration
    * **Multi-tenancy** (`MultiTenancySettings`)
    * **A2A config** (`A2ASettings`, `A2AgentConfig`)

* **Core layer (`app/core/`)**

  * `MCPWrapper`:

    * Internal wrapper around **`mcp-use`** library
    * Holds LLM+MCP configuration and manages the `mcp-use` agent lifecycle
    * Provides `initialize()` and `run_query(...)`
  * `SessionManager`:

    * Manages in-memory `SessionData` objects
    * Responsible for creating, storing, retrieving, listing, and deleting sessions
    * Enforces `MAX_ACTIVE_SESSIONS`
    * Uses an `asyncio.Lock` for concurrency safety
  * `exceptions.py`:

    * `MaxSessionsExceededError`
    * `SessionNotFoundError`
    * `ConfigurationError`
    * `MCPWrapperError`
    * (others if present)

* **API layer (`app/api/`)**

  * `routes/sessions.py`:

    * CRUD operations on sessions
    * Execution of MCP queries
  * `routes/queries.py`:

    * Query-related endpoints (if separated)
  * `routes/health.py`:

    * Health check endpoints
  * `routes/a2a.py`:

    * New A2A discovery and invocation endpoints
  * `dependencies.py`:

    * `get_session_manager()`
    * `get_settings()`
    * `get_a2a_client()` (historically; currently A2A routes use `settings` directly)
    * `get_tenant_context()` returning `TenantContext`

* **Models (`app/models/`)**

  * `config.py`:

    * `SessionConfig`, `LLMProviderConfig`, `MCPServerConfig`, `SandboxOptions`, etc.
    * `MultiTenancySettings`
    * `A2AAuthConfig`, `A2AAgentConfig`, `A2ASettings`
  * `requests.py`:

    * `SessionCreateRequest` (inherits from `SessionConfig` + maybe extra fields)
    * `QueryRequest`
    * `A2AMessageRequest`
    * (legacy `A2ATaskRequest` used previously; currently phased out from routes)
  * `responses.py`:

    * `SessionResponse`, `SessionInfo`
    * `QueryResponse`
    * `A2AgentSummary`, `A2AMessageResponse`, `A2ATaskStatusResponse`

* **Utilities (`app/utils/`)**

  * `logging.py`:

    * `setup_logging()`, `get_logger(name)`
    * Configures loggers, log file(s) under `logs/`, log format, log level
  * `helpers.py`:

    * Misc helper functions if needed

---

## 3. MCP Integration Details

### 3.1 Session Configuration

`SessionConfig` (in `app/models/config.py`) describes an MCP session:

* `llm_provider`:

  * `provider`: `"openai" | "anthropic" | "ollama"` (configurable in `SUPPORTED_PROVIDERS`)
  * `model`: model name for the chosen provider
  * `api_key`: optional override; can also be read from env (`OPENAI_API_KEY`, etc.)
  * `base_url`: optional (e.g. for self-hosted or Ollama)
  * `temperature`: optional float, default ~0.7
  * `max_tokens`: optional

* `mcp_servers`: `Dict[str, MCPServerConfig]`

  * Key: server logical name (e.g. `"filesystem"`)
  * Value: config for each MCP server, typically:

    * `command`: e.g. `"npx"`
    * `args`: `"@modelcontextprotocol/server-filesystem", "/tmp"`
    * Optional env, transport details, etc.
  * **NOTE:** This dictionary is now allowed to be **empty** for LLM-only sessions.

* Other fields:

  * `max_steps`: default from `settings.DEFAULT_MAX_STEPS` (e.g. 30)
  * `verbose`: bool
  * `sandbox`: optional flag to use E2B sandbox
  * `sandbox_options`: normalized sandbox options
  * `disallowed_tools`: list of tool names to disable
  * `use_server_manager`: whether to use mcp-use’s server manager mode (if applicable)

`SessionCreateRequest` in `requests.py` inherits from `SessionConfig`, plus any extra metadata.

### 3.2 MCP Wrapper

`MCPWrapper` is a class that encapsulates mcp-use integration:

* Constructor receives all config:

  * LLM provider details
  * `mcp_servers` mapping
  * `max_steps`, `verbose`
  * Sandbox options
  * Disallowed tools

* Main methods and attributes:

  * `async initialize()`:

    * Creates `mcp-use` client(s)
    * Connects to configured MCP servers
    * Prepares tools and sessions
    * Works even if `mcp_servers` is empty (LLM-only mode), but mcp-use will log warnings such as “No MCP servers defined in config”.
  * `async run_query(query: str, max_steps: Optional[int] = None, server_name: Optional[str] = None) -> Any`:

    * Executes the agent loop via mcp-use with the given query.
    * If `max_steps` is provided and > 0, overrides default `max_steps`.
  * `steps_used`: integer, number of steps used in the last run (if available from mcp-use).
  * `last_server_used`: optional name of the last tool/server used.

### 3.3 Session Manager

`SessionManager` manages all active sessions in memory:

* Internal state:

  * `self._sessions: Dict[str, SessionData]`
  * `self._lock: asyncio.Lock`

* `SessionData` structure (from `session_manager.py`):

  * `session_id: str`
  * `config: SessionConfig`
  * `wrapper: MCPWrapper`
  * `created_at: datetime`
  * `last_used: datetime`
  * `query_count: int`
  * `tenant_id: Optional[str]` (for multi-tenancy)
  * `last_run_id: Optional[str]` (for correlation)

  Methods:

  * `update_last_used()`: sets `last_used = now()`
  * `register_query()`: increments `query_count`

* Methods:

  * `async create_session(config: SessionConfig, tenant_id: str | None, run_id: str | None) -> str`:

    * Enforces `MAX_ACTIVE_SESSIONS`
    * Creates `MCPWrapper`, calls `await wrapper.initialize()`
    * Creates `SessionData` with the given tenant/run IDs
    * Stores in `self._sessions[session_id]`
    * Returns `session_id`

  * `async get_session(session_id: str) -> SessionData`:

    * Protects with `async with self._lock`
    * Looks up `session_data` in `self._sessions`
    * If not found → `SessionNotFoundError`
    * Updates `last_used`
    * Returns `session_data`

  * `async list_sessions(tenant_id: Optional[str] = None) -> List[Dict[str, Any]]`:

    * Iterates over `self._sessions.values()`
    * If `tenant_id` is provided, filters sessions whose `session_data.tenant_id == tenant_id`
    * Returns a list of dictionaries containing:

      * `session_id`
      * `status` (derived, e.g. `"active"`)
      * `created_at`, `last_used`
      * `query_count`
      * `servers` = list of keys in `config.mcp_servers`
      * `llm_provider` = `config.llm_provider.provider`
      * `llm_model` = `config.llm_provider.model`
      * `tenant_id` (optional, for debugging/introspection)

  * `async delete_session(session_id: str, tenant_id: Optional[str] = None)`:

    * Checks that the session belongs to `tenant_id` (if provided)
    * Removes it from `self._sessions`
    * Handles cleanup (closing wrappers, etc.) as needed

---

## 4. A2A Integration Details

### 4.1 Current State vs Target

The current HTTP-based A2A integration is considered a temporary compatibility layer and must not be treated as a reference implementation of the A2A protocol.

* **Current implementation (working, but NOT A2A protocol compliant):**

  * Uses a custom HTTP pattern:

    * `runtime_url + "/tasks"` endpoint on the A2A agent
    * `TaskRequest` / `TaskResponse` payloads:

      * Request:

        * `goal: str`
        * `input: Dict[str, Any] | None`
        * `taskId: str`
        * `metadata: Dict[str, Any] | None`
      * Response:

        * `taskId: str`
        * `status: str`
        * `output: Dict[str, Any] | None`
        * `message: str | None`
  * A2A REST endpoints in mcp-bridge are shaped to be future-proof, but internally they call this custom `/tasks` endpoint on an echo agent.

  * **Note on `blocking` semantics (shim limitation):**
    * `POST /a2a/agents/{agent_id}/messages` with `blocking=false` currently returns `mode="task"` and a `task_id`,
      but the underlying HTTP shim call is still synchronous (`POST {runtime_url}/tasks`) and may return `status="completed"` immediately.
    * Real asynchronous task execution and polling are not provided by the shim unless the agent runtime implements its own task persistence and `GET {runtime_url}/tasks/{task_id}`.


* **Target implementation (planned, NOT yet implemented):**

  * Use the **official A2A SDK** (e.g. `python-a2a`) to:

    * Load Agent Cards (`AgentCard`)
    * Use JSON-RPC 2.0 A2A methods (`message/send`, `tasks/get`, etc.)
  * Keep mcp-bridge’s REST APIs stable:

    * `GET /a2a/agents`
    * `POST /a2a/agents/{agent_id}/messages`
    * `GET /a2a/agents/{agent_id}/tasks/{task_id}`

### 4.2 A2A Configuration Model

In `app/models/config.py`:

```python
class A2AAuthConfig(BaseModel):
    type: Literal["none", "api_key_header", "bearer_token"] = "none"
    header_name: Optional[str] = None
    env_var: Optional[str] = None


class A2AAgentConfig(BaseModel):
    enabled: bool = True
    label: Optional[str] = None
    description: Optional[str] = None

    card_url: str                   # full URL to Agent Card (future)
    runtime_url: Optional[str] = None  # base URL used now to call /tasks
    timeout_seconds: int = 60

    auth: Optional[A2AAuthConfig] = None
    extra_headers: Dict[str, str] = Field(default_factory=dict)


class A2ASettings(BaseModel):
    enabled: bool = True
    agents: Dict[str, A2AAgentConfig] = Field(default_factory=dict)
```

In `config.Settings`:

```python
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
```

#### 4.2.1 A2A HTTP Shim – Client URL Fields (Implementation Note)

The current A2A HTTP shim uses only the following fields from `A2AAgentConfig`:

* `card_url`: full URL to the agent card (e.g. `http://localhost:9001/.well-known/agent.json`)
* `runtime_url`: base URL for the runtime shim (e.g. `http://localhost:9001`)
* tasks endpoint is fixed to: `runtime_url.rstrip("/") + "/tasks"`

Do **not** use alternative/legacy fields such as `base_url`, `task_endpoint`, `card_path`, `auth_header`, or `auth_token` in this project version (they are not part of the authoritative config model). Auth must be derived from `auth` + `extra_headers`.

### 4.3 A2A REST Models

In `app/models/requests.py`:

```python
class A2AMessageRequest(BaseModel):
    goal: str
    input: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    blocking: bool = True
    client_task_id: Optional[str] = None
```

In `app/models/responses.py`:

```python
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

### 4.4 A2A REST Endpoints

`app/api/routes/a2a.py` (current working version):

* `GET /a2a/agents`:

  * Uses `settings.a2a`
  * Returns a list of `A2AAgentSummary`
  * Steps:

    * Load `a2a_settings = settings.a2a`
    * If `a2a_settings.enabled` is False → return `[]`
    * For each `(agent_id, conf)` in `a2a_settings.agents.items()`:

      * Skip if `conf.enabled` is False
      * `name = conf.label or agent_id`
      * `description = conf.description`
      * `card_url = conf.card_url`
      * `skills = []` (placeholder)
      * `labels = []` (placeholder)
    * Wrap in `A2AAgentSummary` and return

* `POST /a2a/agents/{agent_id}/messages`:

  * Input: `A2AMessageRequest`
  * Current implementation uses the **A2AClient HTTP shim** calling the agent `/tasks` endpoint.
  * Steps (simplified):

    1. Get `a2a_settings = settings.a2a`, ensure `enabled=True`

    2. Look up `conf = a2a_settings.agents.get(agent_id)`

    3. Ensure `conf.enabled` and `conf.runtime_url` are present

    4. Determine `mode`:

       * `"blocking"` if `request.blocking` is True
       * `"task"` otherwise

    5. Determine `effective_task_id`:

       * `request.client_task_id` or `uuid.uuid4()`

    6. Build `agent_payload`:

       ```python
       {
         "goal": request.goal,
         "input": request.input,
         "taskId": effective_task_id,
         "metadata": request.metadata or {}
       }
       ```

    7. POST to `tasks_url = conf.runtime_url.rstrip("/") + "/tasks"` using `httpx.AsyncClient`

    8. Handle HTTP errors (propagate status code via HTTPException)

    9. Parse JSON response as `data`

    10. Map:

        * `task_id = data.get("taskId", effective_task_id)`
        * `status = data.get("status")`
        * `output = data.get("output")`
        * `message = data.get("message")`

        into `A2AMessageResponse`.

**Important:**
The current A2A implementation is **bridge-specific**, not A2A protocol compliant. It is intended as a temporary shim until the A2A SDK is integrated.

### A2A SDK behavior: blocking responses may not include a task_id

When using the official A2A SDK, a blocking `POST /a2a/agents/{agent_id}/messages`
may return a final `Message` directly (no Task created/returned by the agent).

In this case:
- `A2AMessageResponse.task_id` can be null
- `A2AMessageResponse.status` can be null
- the agent output is available under `output.message` (raw SDK message model)


---

## 5. Message Flow Overview

### MCP Flow (Sessions + Queries)

1. **Client** calls `POST /sessions` with a JSON body matching `SessionCreateRequest`.

   * Optional headers:

     * `X-Tenant-Id`
     * `X-Run-Id`
2. FastAPI route `create_session`:

   * `tenant_ctx = get_tenant_context(...)` resolves `tenant_id`, `run_id`.
   * Treats `request` as `SessionConfig`.
   * Calls `SessionManager.create_session(config, tenant_id, run_id)`.
   * Returns `SessionResponse` with `session_id`, `status="created"`, list of configured MCP servers.
3. **Client** calls `POST /sessions/{session_id}/query` with `QueryRequest`:

   * Route `execute_query`:

     * `session_data = await session_manager.get_session(session_id)`
     * `wrapper = session_data.wrapper`
     * Measures execution time via `event_loop.time()`
     * Calls `await wrapper.run_query(query=request.query, max_steps=request.max_steps, server_name=request.server_name)`
     * `session_data.register_query()`
     * Uses `wrapper.steps_used` and `wrapper.last_server_used`
     * Returns `QueryResponse` (session_id, result, execution_time, steps_used, timestamp, server_used)

### A2A Flow (Current HTTP-based Implementation)

1. **Client** calls `GET /a2a/agents` to list configured agents.
2. **Client** chooses an `agent_id` and calls `POST /a2a/agents/{agent_id}/messages`:

   * The REST layer:

     * Maps `A2AMessageRequest` to the echo-agent’s `/tasks` endpoint payload.
     * For now, executes synchronously, but still marks `mode` based on `blocking`.
3. Underlying agent handles the request and returns a `TaskResponse`.
4. The REST endpoint wraps it in `A2AMessageResponse`.

---

## 6. Runtime / Docker Environment

* Python project (managed with `uv`):

  * `pyproject.toml`
  * `uv.lock`
  * `.venv` created/managed by `uv sync`
* Main entry point:

  * `python main.py` (often launched via `uv run python main.py`)
  * Uvicorn runs FastAPI on configured `HOST`/`PORT` (default `0.0.0.0:8000`)
* Docker:

  * `Dockerfile` builds the app
  * Multiple `docker-compose*.yml` files:

    * support DIND / DOD modes for MCP gateway
    * orchestrate mcp-bridge + MCP servers + possibly Ollama or other components
* Logs:

  * Directed to console and `logs/app.log` (exact filename may differ)
  * Central logger instance in `app.utils.logging`

---

## 7. What Works / What Is Broken

### Working

* **MCP side:**

  * `POST /sessions`: create MCP sessions with LLM+MCP servers **or LLM-only** (empty `mcp_servers`).
  * `POST /sessions/{session_id}/query`: executes queries via `mcp-use`.

    * mcp-use logs warnings when there are no MCP servers; behavior is acceptable.
  * `GET /sessions`: lists sessions **scoped to the current tenant** (if multi-tenancy enabled).
  * `GET /sessions/{id}`: returns info for the session, ensuring tenant isolation.
  * `DELETE /sessions/{id}`: deletes session in background, ensuring tenant isolation.
  * `multi-tenancy`:

    * `TenantContext` resolution via headers + settings works.
    * Filtering in `list_sessions` works.
    * Tenant-specific checks on `get_session` and `delete_session` are in place.

* **A2A side (temporary HTTP adapter):**

  * `GET /a2a/agents`:

    * Returns list of agents from static config (`settings.a2a.agents`).
  * `POST /a2a/agents/{agent_id}/messages`:

    * Works with the **local echo agent** that exposes `/tasks`.
    * Correctly maps request/response into `A2AMessageRequest` / `A2AMessageResponse`.

### Not Yet Implemented / Broken

* **A2A protocol compliance:**

  * No use of official A2A SDK yet.
  * No JSON-RPC / `message/send` / `tasks/get` integration.
  * No use of A2A’s `AgentCard` beyond `card_url` being stored; `GET /a2a/agents` does not fetch/parsing the card.

* **A2A task status endpoint:**
  * `GET /a2a/agents/{agent_id}/tasks/{task_id}` is implemented using the current HTTP shim:
    * It attempts `GET {runtime_url}/tasks/{task_id}` on the agent runtime.
    * If the runtime does not support task polling (e.g., the local echo agent), it returns a graceful shim response (e.g., `status="not_found"` / `status="unsupported"`) including the remote HTTP status code in `raw_response`.


* **Multi-tenancy for A2A:**

  * A2A endpoints currently do **not** filter agents per tenant.
  * All agents in `settings.a2a.agents` are visible to all tenants (subject to global `a2a.enabled` and `agent.enabled` flags).

* **LLM/A2A correlation by run_id:**

  * `run_id` exists in `TenantContext` and is stored in `SessionData.last_run_id`.
  * A2A side does not yet use `run_id` for correlation.

---

## 8. Explicit Design Decisions Already Taken

See also `DECISIONS.md`, but key points:

* MCP and A2A are **architecturally separated**:

  * mcp-bridge offers MCP endpoints and A2A endpoints from the same process,
  * visual builder orchestrates calls between them.
* Multi-tenancy is implemented at **REST/bridge level**, not at MCP or A2A protocol level:

  * Tenants are identified by `X-Tenant-Id` header.
  * Tenant is stored on sessions and used for filtering/authorization.
* Sessions can be created **without MCP servers** (LLM-only).
* A2A **does not embed tenant_id in the A2A protocol** payloads to avoid binding to a non-standard extension.

---

## 9. Known Limitations and Pain Points

* In-memory session store:

  * No persistence across restarts.
  * Single-process only; not designed for multi-instance scaling yet.
* Multi-tenancy is coarse:

  * No per-tenant configuration for LLM providers or A2A agents yet.
  * Only session visibility and deletion are tenant-aware.
* A2A integration is currently an HTTP shim:

  * Limited to echo-like agents that accept a specific `/tasks` schema.
  * Not interoperable yet for arbitrary third-party A2A agents.
* Error feedback:

  * Some 500s still map to generic `"Internal Error"` or `"Error executing A2A message"` without fine-grained error codes exposed to clients.
* README/Docs:

  * README has been updated multiple times; keep `PROJECT_CONTEXT.md` as the more precise technical reference.

---

## 10. Roadmap / Next Steps

**Short-term (A2A & stability)**

1. Implement `GET /a2a/agents/{agent_id}/tasks/{task_id}` using `A2ATaskStatusResponse`.

   * For the echo agent, this might be a stub or return the same info as `/messages` for now.
2. Improve error visibility:

   * Return more specific messages in A2A errors (e.g., propagate remote `status` and error descriptions).
3. Add basic input validation for A2A requests (e.g. mandatory `goal`).

**Medium-term (A2A protocol integration)**

4. Integrate official **A2A SDK** (e.g. `python-a2a`):

   * Introduce an internal `A2AClient` that:

     * Loads `AgentCard` from `card_url`.
     * Uses JSON-RPC A2A methods for messaging/tasks.
   * Keep REST surface stable while changing internals.

5. Extend `GET /a2a/agents`:

   * Fetch real `skills`, `interfaces`, and other metadata from `AgentCard`.
   * Possibly expose raw card in a dedicated endpoint.

6. Implement real task polling:

   * `POST /a2a/agents/{agent_id}/messages` with `blocking=false` → create task.
   * `GET /a2a/agents/{agent_id}/tasks/{task_id}` → map A2A Task/TaskStatus to `A2ATaskStatusResponse`.

**Medium-term (Multi-tenancy & config)**

7. Multi-tenant A2A configuration:

   * Optionally allow different sets of agents per tenant.
   * Optionally different(credentials per tenant via `A2AAuthConfig`.

8. Optional per-tenant LLM configuration:

   * Different default providers or models per tenant.

**Long-term**

9. Persistence & scaling:

   * Replace in-memory session store with persistent storage (Redis, DB, etc.).
10. Unified tracing & observability:

* Correlate MCP sessions and A2A calls via `run_id` and external tracing systems (Langfuse, OpenTelemetry, etc.).
