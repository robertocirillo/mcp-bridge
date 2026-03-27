# PROJECT_CONTEXT.md

Project: **mcp-bridge – MCP + A2A integration**

---

## 1. Project Goal

Important baseline for the current branch:

* The project has been upgraded from **`mcp-use 1.3.x`** to **`mcp-use 1.7.0`**.
* This is a foundational runtime change, not a minor dependency bump.
* The current multimodal implementation depends on that upgrade because the `HumanMessage` execution path is expected to work correctly only on the newer `mcp-use` runtime.

**mcp-bridge** is a FastAPI-based REST service that:

1. Exposes a **REST API** for:

   * Managing MCP sessions (LLM + MCP servers via `mcp-use`)
   * Executing queries through those sessions
   * Calling external **A2A agents** (remote/local, third-party)

2. Acts as a **bridge** between:

   * Visual builders / UIs / workflow tools (HTTP/JSON)
   * The **MCP ecosystem** (via `mcp-use`)
   * The **A2A ecosystem** (today: SDK-based integration)

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
    * `/a2a/agents/{agent_id}/tasks/{task_id}` (A2A task polling)
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
    * **A2A config** (`A2ASettings`, `A2AAgentConfig`)

* **Core layer (`app/core/`)**

  * `MCPWrapper`:

    * Façade/orchestrator around **`mcp-use`**
    * Holds session-scoped LLM+MCP configuration and request context
    * Remains the only public MCP backend boundary used by the rest of the application
    * Remains the chosen public facade while internal MCP boundary concerns are consolidated behind it
    * Coordinates the MCP runtime with:
      - `ToolPolicyEngine` for tool invocation decisions
      - `GuardrailRunner` for guardrail execution
      - the audit/event layer for structured observability
    * The recent cleanup of guardrail execution, invocation-context handling, and audit events belongs to this consolidation, not to a new public abstraction
    * Provides `initialize()` and `run_query(...)`
  * `mcp_wrapper_*` internal modules:

    * This focused internal split is the intended current architecture for the MCP boundary
    * `runtime.capabilities`: capability lookup/invocation helpers for prompts, resources, and other optional MCP features
    * `runtime.tools`: direct tool invocation helpers, task-support detection, raw MCP task transport
    * `guardrails.wrapper`: shared guardrail pipeline wiring and tool-result wrapping helpers
    * `runtime.llm`: provider imports, sandbox normalization, LLM creation
    * `runtime.transport`: guarded MCP client/session proxies
    * `guardrails.pii`: PII detection/redaction and related guardrail factories
    * `guardrails.bias`: bias detectors, bias guardrails, output sanitization helpers
    * `mcp_wrapper_errors`: structured MCP boundary errors
    * `MCPRuntimeAdapter` is not part of the current design; it should be reconsidered only if a concrete reusable runtime seam emerges later
  * `ToolPolicyEngine`:

    * Evaluates tool-level allow/deny policy before MCP tool calls
    * Supports explicit policies, deny patterns, allow patterns, and lightweight argument validators
  * `GuardrailRunner`:

    * Executes query-level guardrails (`before_model`, `after_model`)
    * Executes per-tool-result guardrails inside the agent/tool loop
    * Emits structured guardrail audit events
  * `audit.mcp_audit`:

    * Defines `AuditEvent`
    * Provides the in-memory audit recorder used by wrapper and guardrail runner
  * `SessionManager`:

    * Remains the public session/query-operation façade used by the API layer
    * Delegates active session persistence to `SessionStore`
    * Delegates async query-operation state/tasks to `QueryOperationStore`
    * Delegates pending elicitation/task-status bookkeeping to `PendingInteractionStore`
    * Responsible for creating, storing, retrieving, listing, and deleting sessions
    * Responsible for scheduling and resuming asynchronous query operations
    * Enforces `MAX_ACTIVE_SESSIONS`
    * Uses an `asyncio.Lock` for concurrency safety

* Guardrails (session-scoped):

  * Query-level guardrails are applied around the model call:
    - `before_model` runs on the user input
    - `after_model` runs on the model output
  * Tool-result guardrails run on each MCP tool result inside the agent/tool loop.
  * PII guardrail supports Strategy 3 semantics (`mode` default + phase overrides).
    `output_mode` controls both final output handling and tool-result handling.

* Bias detector integration:

  * When `guardrails.bias.base_url` is set, mcp-bridge calls an external `bias-detector-service`
    (HTTP) on the **final answer only** (after output sanitization).
  * Supports **cascaded checks** via `guardrails.bias.checks: []`:
    - Multiple detector calls in a single `after_model` pass, each with per-check overrides
      (`model_id`, `threshold`, `unsafe_labels`, etc.).
    - If any check returns `flagged=true` and bias mode is `block`, the request is blocked
      with structured error `detail.code="BIAS_DETECTED"` and `details.checks_results`.
  * For debugging, optional forwarded flags:
    - `return_all_scores`
    - `return_char_spans` (enables `labels[].spans` when the detector/model supports it)
  * When service calls fail and bias is in `block` mode, mcp-bridge fails closed:
    HTTP 503 `detail.code="BIAS_DETECTOR_UNAVAILABLE"`.
  * `A2AClient`:

    * Wrapper around **`a2a-sdk`**
    * Resolves Agent Cards and communicates with agents
    * Provides `send_message(...)` and `get_task(...)`
  * `exceptions.py`:

    * `MaxSessionsExceededError`
    * NOTE: `app/core/clients/a2a_client.py` must not import `app/api/*` (to avoid circular imports); the API layer injects the client via `dependencies.py`.
    * `SessionNotFoundError`
    * `ConfigurationError`
    * `MCPWrapperError`
    * (others if present)

* **API layer (`app/api/`)**

  * `routes/sessions.py`:

    * Public router for session CRUD plus MCP prompt/resource capability endpoints
  * `routes/queries.py`:

    * Public router for synchronous queries, async query operations, and query history
  * `services/session_service.py`:

    * Route-facing session orchestration
    * Maps session/prompt/resource flows onto `SessionManager` and `MCPWrapper`
  * `services/query_service.py`:

    * Route-facing query and query-operation orchestration
  * `session_context.py`:

    * Tenant-scoped session lookup and wrapper context binding helpers
  * `error_mapping.py`:

    * Shared HTTP error translation for session/query/capability flows
  * `routes/health.py`:

    * Health check endpoints
  * `routes/a2a.py`:

    * A2A discovery, message send, and task polling endpoints
  * `dependencies.py`:

    * `get_session_manager()`
    * `get_settings()`
    * `get_a2a_client()`
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
  * `responses.py`:

    * `SessionResponse`, `SessionInfo`
    * `QueryResponse`
    * `A2AAgentSummary`, `A2AMessageResponse`, `A2ATaskStatusResponse`

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

`MCPWrapper` is the public class that encapsulates the mcp-use integration boundary:

* Constructor receives all config:

  * LLM provider details
  * `mcp_servers` mapping
  * `max_steps`, `verbose`
  * Sandbox options
  * Disallowed tools

  * Runtime role:

    * Keeps the public/session-facing API stable
    * Wraps the `mcp-use` client/session boundary so tool policy is enforced before every MCP tool call
    * Delegates guardrail execution to `GuardrailRunner`
    * Records structured events through the audit layer
    * Delegates boundary internals to focused helper modules (`mcp_wrapper_*`) while keeping the external boundary stable
    * Does not imply another imminent MCP boundary redesign or a new adapter layer

* Main methods and attributes:

  * `async initialize()`:

    * Wires boundary-specific helper modules (`runtime.capabilities`, `runtime.tools`, `guardrails.wrapper`, `runtime.llm`, `runtime.transport`, specialized guardrail modules)
    * Creates `mcp-use` client(s)
    * Connects to configured MCP servers
    * Prepares tools and sessions
    * Works even if `mcp_servers` is empty (LLM-only mode), but mcp-use will log warnings such as “No MCP servers defined in config”.
  * `async run_query(query: str, max_steps: Optional[int] = None, server_name: Optional[str] = None) -> Any`:

    * Executes the agent loop via mcp-use with the given query.
    * Runs query-level guardrails before and after the model execution path.
    * If `max_steps` is provided and > 0, overrides default `max_steps`.
  * `steps_used`: integer, number of steps used in the last run (if available from mcp-use).
  * `last_server_used`: optional name of the last tool/server used.

### 3.3 Session Manager

`SessionManager` manages all active sessions in memory:

* Internal state:

  * `self._session_store: SessionStore`
  * `self._sessions: Dict[str, SessionData]` (owned by `SessionStore`)
  * `self._query_operation_store: QueryOperationStore`
  * `self._interaction_store: PendingInteractionStore`
  * `self._lock: asyncio.Lock`

* `SessionData` structure (from `session_store.py`):

  * `session_id: str`
  * `config: SessionConfig`
  * `wrapper: MCPWrapper`
  * `created_at: datetime`
  * `last_used: datetime`
  * `status: str` (currently `"active"`)
  * `query_count: int`
  * `tenant_id: Optional[str]` (for multi-tenancy)
  * `last_run_id: Optional[str]` (for correlation)

  Methods:

  * `update_last_used()`: sets `last_used = now()`
  * `register_query()`: increments `query_count` and refreshes `last_used`

* Methods:

  * `async create_session(config: SessionConfig, tenant_id: str | None, run_id: str | None) -> str`:

    * Enforces `MAX_ACTIVE_SESSIONS`
    * Creates `MCPWrapper`, calls `await wrapper.initialize()`
    * Creates `SessionData` with the given tenant/run IDs
    * Stores it through `SessionStore.add(...)`
    * Returns `session_id`

  * `async get_session(session_id: str, tenant_id: Optional[str] = None) -> SessionData`:

    * Protects with `async with self._lock`
    * Delegates lookup to `SessionStore.get(...)`
    * If not found → `SessionNotFoundError`
    * If `tenant_id` is provided and does not match → `SessionNotFoundError`
    * Updates `last_used`
    * Returns `session_data`

  * `async list_sessions(tenant_id: Optional[str] = None) -> List[Dict[str, Any]]`:

    * Delegates to `SessionStore.list_sessions(...)`
    * Returns a list of dictionaries containing:

      * `session_id`
      * `status`
      * `created_at`, `last_used`
      * `query_count`
      * `servers` = list of keys in `config.mcp_servers`
      * `llm_provider` = `config.llm_provider.provider`
      * `llm_model` = `config.llm_provider.model`

  * `async delete_session(session_id: str, tenant_id: Optional[str] = None)`:

    * Checks that the session belongs to `tenant_id` (if provided)
    * Clears pending elicitation state and async query-operation tasks
    * Closes the wrapper
    * Removes the session via `SessionStore.remove(...)`

  * `async create_query_operation(...)`, `get_query_operation(...)`, `resume_query_operation(...)`:

    * Use `QueryOperationStore` for queued/running/completed/failed operation state
    * Use `PendingInteractionStore` for elicitation pauses and resumes

---

## 4. A2A Integration Details

### 4.1 Current State vs Target

The A2A integration uses the official **a2a-sdk**.

* **Current implementation (SDK-based):**

  * Resolves the Agent Card from `card_url` using the SDK and communicates using the transport advertised by the agent (commonly JSON-RPC).
  * `POST /a2a/agents/{agent_id}/messages` uses the SDK `send_message(...)`.
  * `GET /a2a/agents/{agent_id}/tasks/{task_id}` uses the SDK `get_task(...)`.
  * The REST field `blocking` is a bridge-level convenience flag (not an A2A protocol field).
  * `blocking=false` does **not** guarantee a Task: some agents may return a final `Message` directly (so `task_id` can be null).

* **Legacy / compatibility note:**

  * Earlier versions used a bridge-specific HTTP shim (`POST {runtime_url}/tasks`). This is not the reference path in SDK mode.

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

    card_url: str                      # full URL to Agent Card (used by a2a-sdk)
    runtime_url: Optional[str] = None  # legacy shim field (optional, avoid relying on it in SDK mode)
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

#### 4.2.1 Legacy HTTP Shim Fields (Implementation Note)

In SDK mode, only:

* `card_url` (full URL to the Agent Card)

is required for communication.

`runtime_url` exists for legacy shim compatibility and should not be relied on for SDK-based agents.
If an agent exposes `runtime_url`, it may still be useful for ad-hoc debugging, but the bridge should prefer SDK calls.

Auth headers must be derived from `auth` + `extra_headers`.

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
from enum import Enum

class A2ATaskState(str, Enum):
    # A2A standard TaskState values
    submitted = "submitted"
    working = "working"
    input_required = "input-required"
    completed = "completed"
    canceled = "canceled"
    failed = "failed"
    unknown = "unknown"


class A2AAgentSummary(BaseModel):
    agent_id: str
    name: str
    description: Optional[str] = None
    card_url: str
    skills: List[str] = []
    labels: List[str] = []


class A2AMessageResponse(BaseModel):
    mode: Literal["blocking", "task"]
    agent_id: str
    task_id: Optional[str]
    status: Optional[A2ATaskState]  # may be null for message-only agents
    upstream_state: Optional[str]   # raw upstream state (may include non-standard values)
    is_terminal: Optional[bool]     # computed from status when available
    output: Optional[Dict[str, Any]]
    message: Optional[str]
    raw_response: Optional[Dict[str, Any]]


class A2ATaskStatusResponse(BaseModel):
    agent_id: str
    task_id: str
    status: A2ATaskState
    upstream_state: Optional[str]
    is_terminal: Optional[bool]
    output: Optional[Dict[str, Any]]
    message: Optional[str]
    raw_response: Optional[Dict[str, Any]]
```

### 4.4 A2A REST Endpoints


#### Error format (A2A)

All A2A endpoints return errors using a consistent JSON structure under `detail`:

```json
{
  "detail": {
    "code": "A2A_UPSTREAM_ERROR",
    "message": "Timed out contacting agent",
    "operation": "send_message",
    "agent_id": "local_echo_agent",
    "task_id": "optional",
    "field": "optional",
    "upstream": { "optional": "payload" }
  }
}
```

Common `code` values:
- `A2A_DISABLED` (A2A integration disabled)
- `A2A_AGENT_NOT_FOUND` (unknown/disabled agent_id)
- `A2A_SCHEMA_ERROR` (invalid/missing request fields)
- `A2A_TASK_NOT_APPLICABLE` (task polling not applicable for this agent)
- `A2A_TASK_NOT_FOUND` (task_id not found on the agent)
- `A2A_UPSTREAM_ERROR` (upstream/SDK or transport error; may include `upstream`)
- `A2A_INTERNAL_ERROR` (unexpected server-side error)

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
      * `skills = []` (placeholder until Agent Card parsing is surfaced)
      * `labels = []` (placeholder)

    * Wrap in `A2AAgentSummary` and return

* `POST /a2a/agents/{agent_id}/messages`:

  * Input: `A2AMessageRequest`
  * Uses the A2A SDK client (resolved from `conf.card_url`) via `A2AClient`.
  * Steps (simplified):

    1. Get `a2a_settings = settings.a2a`, ensure `enabled=True`
    2. Look up `conf = a2a_settings.agents.get(agent_id)` and ensure enabled
    3. Create an outbound A2A text message using:

       * ✅ `create_text_message_object(content=request.goal)`
       * (do not pass the text positionally; the first positional argument is `role`)
    4. Call `a2a_client.send_message(...)` with:

       * `blocking=request.blocking`
       * `request_metadata=request.metadata` (when supported by the installed `a2a-sdk`)
    5. Map the SDK result to `A2AMessageResponse`:

       * `task_id` may be null if the agent returns a final `Message` directly
       * `status` may be null in message-only responses
       * `mode` reflects the actual response:

         * `"task"` only when `task_id` is present
         * otherwise `"blocking"`

* `GET /a2a/agents/{agent_id}/tasks/{task_id}`:

  * Uses the A2A SDK `get_task(...)` via `A2AClient`.
  * `input-required` is a first-class A2A state indicating the task is waiting for additional client (often human) input.
  * Returns `A2ATaskStatusResponse` with A2A-standard task `status` (TaskState): `submitted|working|input-required|completed|canceled|failed|unknown`.
  * Also includes hybrid fields:
    * `upstream_state`: raw upstream state (even if non-standard)
    * `is_terminal`: computed boolean for terminal states (completed|failed|canceled)
  * Message-only agents (task polling not applicable) → HTTP **409** with structured error `code="A2A_TASK_NOT_APPLICABLE"` and `operation="get_task"`.
  * Task id not found → HTTP **404** with structured error `code="A2A_TASK_NOT_FOUND"` and `operation="get_task"`.

---

## 5. Message Flow Overview

### MCP Flow (Sessions + Queries)

1. **Client** calls `POST /sessions` with a JSON body matching `SessionCreateRequest`.

   * Optional headers:

     * `X-Tenant-Id`
     * `X-Run-Id`
2. FastAPI route `create_session`:

   * `tenant_ctx = get_tenant_context(...)` resolves `tenant_id`, `run_id`.
   * Delegates to `session_service.create_session(...)`.
   * `session_service` treats `request` as `SessionConfig`.
   * Calls `SessionManager.create_session(config, tenant_id, run_id)`.
   * Returns `SessionResponse` with `session_id`, `status="created"`, list of configured MCP servers.
3. **Client** calls `POST /sessions/{session_id}/query` with `QueryRequest`:

   * Route `execute_query` delegates to `query_service.execute_query(...)`:

     * `session_data = await session_manager.get_session(session_id)`
     * `wrapper = bind_wrapper_context(session_data.wrapper, tenant_ctx=tenant_ctx, session_id=session_id)`
     * Measures execution time via `event_loop.time()`
     * Calls `await wrapper.run_query(query=request.query, max_steps=request.max_steps, server_name=request.server_name)`
     * Uses `wrapper.steps_used` and `wrapper.last_server_used`
     * Returns `QueryResponse` (session_id, result, execution_time, steps_used, timestamp, server_used, `has_mcp_servers`)

### A2A Flow (SDK-based implementation)

1. **Client** calls `GET /a2a/agents` to list configured agents.

2. **Client** chooses an `agent_id` and calls `POST /a2a/agents/{agent_id}/messages`:

   * The REST layer sends the message via **a2a-sdk**, resolving the Agent Card from `card_url`.
   * `blocking` controls whether the REST call waits for completion, but agents may still respond with a final `Message` directly (no Task).
   * REST `mode` reflects the actual SDK response (`"task"` only when `task_id` is present).

3. If the agent returns a Task, the client can call `GET /a2a/agents/{agent_id}/tasks/{task_id}` to poll task status.

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

* **A2A side (SDK-based integration):**

  * `GET /a2a/agents`:

    * Returns list of agents from static config (`settings.a2a.agents`).
  * `POST /a2a/agents/{agent_id}/messages`:

    * Works with SDK-compatible third-party agents (e.g. JSON-RPC agents advertising an Agent Card at `card_url`).
    * Maps SDK responses into `A2AMessageRequest` / `A2AMessageResponse` (message-only responses may have null `task_id`).

### Not Yet Implemented / Broken

* **A2A protocol coverage (SDK-based):**

  * The bridge uses `a2a-sdk` for sending messages and (when applicable) task polling.
  * `GET /a2a/agents` is still config-driven and does not yet expose Agent Card-derived metadata (skills/interfaces) in the REST response.

* **A2A task status endpoint:**

  * `GET /a2a/agents/{agent_id}/tasks/{task_id}` is implemented via the A2A SDK (`get_task`).
  * Some agents may never return a Task (message-only behavior); in that case task polling is not applicable.

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
* A2A integration is SDK-based (a2a-sdk), but protocol coverage is still evolving:

  * Some agents return Message-only responses (no Task), so polling semantics vary.
  * Streaming and richer task/event handling may require additional normalization at the REST layer.
* Error feedback (A2A):

  * A2A endpoints now return a consistent structured error payload under `detail`:
    `{ "detail": { "code": "...", "message": "...", "agent_id"?: "...", "task_id"?: "...", "upstream"?: {...} } }`.
  * Errors are mapped from `A2AClientError` when possible (status_code/code/upstream), with safe fallbacks.
* README/Docs:

  * README has been updated multiple times; keep `PROJECT_CONTEXT.md` as the more precise technical reference.

---

## 10. Roadmap / Next Steps

**Short-term (A2A & stability)**

Recently completed (Sprint Week 1):
1. Harden `POST /a2a/agents/{agent_id}/messages`:
   * `goal` required + non-empty
   * `mode="task"` only when `task_id` is present; otherwise `mode="blocking"`
   * consistent structured errors under `detail` (stable `code/message`, optional `agent_id/task_id/upstream`)
2. Harden `GET /a2a/agents/{agent_id}/tasks/{task_id}`:
   * message-only agents → HTTP 409 (`A2A_TASK_NOT_APPLICABLE`)
   * task not found → HTTP 404 (`A2A_TASK_NOT_FOUND`)
   * task state is A2A-standard (TaskState): `submitted|working|input-required|completed|canceled|failed|unknown` (+ `upstream_state`, `is_terminal`)
   * transport/connect/timeout mapped into the same structured error schema
3. Add/extend pytest contract tests for A2A behaviors (blocking/message-only, task-based + polling, task not found, transport/timeout).

Next validation step:
4. Validate the hardened task polling behavior across real third-party agents (capability differences, partial compliance) and refine upstream error mapping if needed.

**Medium-term (A2A protocol integration)**

4. Expand **a2a-sdk** coverage:

   * Clarify semantics for message-only vs task-based agents.
   * Improve streaming/task handling and error reporting.
   * Optionally surface selected Agent Card metadata (skills, interfaces) in `GET /a2a/agents`.

5. Extend `GET /a2a/agents`:

   * Fetch real `skills`, `interfaces`, and other metadata from `AgentCard`.
   * Possibly expose raw card in a dedicated endpoint.

6. Implement real task polling:

   * `POST /a2a/agents/{agent_id}/messages` with `blocking=false` → create task.
   * `GET /a2a/agents/{agent_id}/tasks/{task_id}` → map A2A Task/TaskStatus to `A2ATaskStatusResponse` using A2A TaskState values.

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
