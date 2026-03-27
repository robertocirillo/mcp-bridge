# ARCHITECTURE_FLOW.md

## 1. Overview

This document describes how requests flow through **mcp-bridge**, spanning:

- MCP sessions and queries (via `mcp-use`)
- A2A agent invocation (via official a2a-sdk)
- Multi-tenancy
- Failure modes and current constraints

The visual builder/consumer is responsible for orchestrating multi-step flows such as:

> Create MCP session → Make MCP queries → Call A2A agents → Combine results

mcp-bridge itself is deliberately kept as a **thin bridge**.

For the MCP/session side, the public FastAPI routers are now intentionally thin:

- `app/api/routes/sessions.py` and `app/api/routes/queries.py` define the public HTTP endpoints
- `app/api/services/session_service.py` and `app/api/services/query_service.py` contain route-facing orchestration
- `app/api/session_context.py` centralizes tenant/session lookup and wrapper context binding
- `app/api/error_mapping.py` centralizes HTTP error translation

---

## 2. MCP Flow: Client → MCP-Bridge → MCP-Use → LLM + MCP Servers

### 2.1 Create MCP Session (`POST /sessions`)

**Actors:**

- Client (visual builder, workflow engine, etc.)
- FastAPI route `create_session`
- `SessionManager`
- `MCPWrapper` (public mcp-use boundary / façade)

**Headers:**

- `X-Tenant-Id` (optional, depending on multi-tenancy mode)
- `X-Run-Id` (optional; for correlation)

**Body:** `SessionCreateRequest` ≈ `SessionConfig`:

```json
{
  "llm_provider": {
    "provider": "openai",
    "model": "gpt-4.1-mini",
    "temperature": 0.7,
    "api_key": "optional-override",
    "base_url": "optional"
  },
  "mcp_servers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    }
  },
  "max_steps": 30,
  "verbose": false,
  "sandbox": false,
  "sandbox_options": {},
  "disallowed_tools": [],
  "use_server_manager": false
}
```

> Note: `mcp_servers` can be **empty** for LLM-only sessions.

**Flow:**

1. FastAPI calls `get_tenant_context()`:
   - Reads `X-Tenant-Id` and `X-Run-Id`.
   - Uses `settings.multi_tenancy` to resolve:
     - `tenant_id`: final tenant to associate to this request
     - `run_id`: correlation id (optional)

2. Route `create_session`:
   - Delegates to `session_service.create_session(...)`.
   - `session_service.create_session(...)` treats `request` as `SessionConfig` (`SessionCreateRequest` inherits from it).
   - Calls `session_id = await session_manager.create_session(config=request, tenant_id=tenant_ctx.tenant_id, run_id=tenant_ctx.run_id)`.

3. `SessionManager.create_session(...)`:
   - Acquires `self._lock`.
   - Checks `self._session_store.count() < settings.MAX_ACTIVE_SESSIONS`, otherwise raises `MaxSessionsExceededError`.
   - Generates a new `session_id = uuid4()`.
   - Instantiates `MCPWrapper` with LLM and MCP config.
   - Calls `await wrapper.initialize()`:
     - `MCPWrapper` wires its internal boundary helpers (`runtime.capabilities`, `runtime.tools`, `guardrails.wrapper`, `runtime.llm`, `runtime.transport`, specialized guardrail modules).
     - `mcp-use` initializes client, sessions, and tools.
     - If `mcp_servers` empty, `mcp-use` logs warnings but continues.
   - Creates `SessionData` (defined in `app/core/sessions/store.py`):
     - `session_id`, `config`, `wrapper`
     - `created_at = now()`, `last_used = now()`
     - `status = "active"`
     - `query_count = 0`
     - `tenant_id = tenant_id`, `last_run_id = run_id`
   - Stores it through `SessionStore.add(...)`.
   - Releases lock and returns `session_id`.

4. Route builds a `SessionResponse`:

```json
{
  "session_id": "<uuid>",
  "status": "created",
  "message": "Session created successfully",
  "servers": ["filesystem"]
}
```

**Failure modes:**

- Validation errors on `SessionCreateRequest` → HTTP 400.
- `MaxSessionsExceededError` → HTTP 429.
- `ConfigurationError` → HTTP 400.
- `MCPWrapperError` → HTTP 502.
- Any other unexpected error → HTTP 500.

---

### 2.2 List MCP Sessions (`GET /sessions`)

**Flow:**

1. FastAPI resolves `tenant_ctx = get_tenant_context(...)`.
2. Route delegates to `session_service.list_sessions(...)`.
3. `SessionManager.list_sessions(tenant_id)`:
   - Delegates to `SessionStore.list_sessions(tenant_id=tenant_id)`.
   - `SessionStore` iterates over active `SessionData` entries, filters by tenant when provided, and returns dictionaries such as:

```json
[
  {
    "session_id": "<uuid>",
    "status": "active",
    "created_at": "...",
    "last_used": "...",
    "query_count": 3,
    "servers": ["filesystem"],
    "llm_provider": "openai",
    "llm_model": "gpt-4.1-mini"
  }
]
```

4. Route converts them to list of `SessionInfo` Pydantic models and returns.

**Key point:** tenants **see only their own sessions** when multi-tenancy is enabled.

---

### 2.3 Get Session Info (`GET /sessions/{session_id}`)

**Flow:**

1. `tenant_ctx = get_tenant_context(...)`.
2. Route delegates to `session_service.get_session_info(...)`.
3. `session_service.get_session_info(...)` uses `session_context.get_tenant_session(...)`.
4. `SessionManager.get_session(session_id, tenant_id=...)`:
   - Delegates lookup to `SessionStore.get(...)`.
   - Validates that `session_id` exists.
   - If `tenant_id` is provided and `session_data.tenant_id != tenant_id`, raises `SessionNotFoundError`.
   - Updates `last_used`.
   - Returns `SessionData`.
5. Service maps `SessionData` to `SessionInfo` and returns.

**Failure modes:**

- Unknown session → HTTP 404.
- Tenant mismatch → HTTP 404 (masked as “not found”).

---

### 2.4 Execute MCP Query (`POST /sessions/{session_id}/query`)

**Body:** `QueryRequest`:

```json
{
  "query": "List the files in the directory",
  "max_steps": 10,
  "server_name": "filesystem"  // optional; used as hint/metadata
}
```

Backward compatibility:

- Legacy text-only requests still use `query: string`.
- Structured multimodal requests use `input`.

Structured multimodal shape:

```json
{
  "input": {
    "text": "Describe this image",
    "images": [
      {
        "source_type": "url",
        "url": "https://example.com/cat.png"
      },
      {
        "source_type": "base64",
        "mime_type": "image/png",
        "data": "iVBORw0KGgoAAAANSUhEUgAA..."
      }
    ]
  },
  "max_steps": 10,
  "server_name": "filesystem"
}
```

Supported V1.5 query shapes:

- text only via `query`
- text only via `input.text`
- image only via `input.images`
- text + image
- image sources via remote URL or inline base64

Validation / runtime notes:

- If both `query` and `input` are provided, `input` wins.
- Base64 image data is capped at `MAX_BASE64_IMAGE_DATA_LENGTH = 5_000_000` characters per image.
- For `source_type="url"`, the bridge fetches the image server-side and converts it to a provider-ready base64 data URL before building the final `HumanMessage`.
- Remote image fetch accepts only `http`/`https`, uses `IMAGE_FETCH_TIMEOUT_SECONDS = 5.0`, follows at most `MAX_REMOTE_IMAGE_REDIRECTS = 3`, and enforces `MAX_REMOTE_IMAGE_BYTES = 5_000_000`.
- Remote image fetch requires a supported `Content-Type` (`image/png`, `image/jpeg`, `image/webp`) and validates the downloaded bytes against the declared MIME type.
- Baseline SSRF hardening rejects localhost, loopback, private, link-local, multicast, reserved, and unspecified targets; redirect destinations are revalidated on every hop.
- Before-model guardrails apply only to the textual portion (`query` or `input.text`).
- The bridge does not moderate, inspect, or OCR image content.
- URL reachability depends on mcp-bridge server connectivity, not on the caller browser/client.
- Residual limitation: the SSRF defenses are intentionally lightweight and do not fully prevent DNS rebinding or every environment-specific network bypass.
- Effective image support depends on the configured provider/model; unsupported models may fail at runtime.

**Flow:**

1. `tenant_ctx = get_tenant_context(...)` (even if not explicitly used right now, session has tenant id bound).
2. Route delegates to `query_service.execute_query(...)`.
3. `query_service.execute_query(...)`:
   - Retrieves session via `await session_manager.get_session(session_id)`.
   - Binds tenant/run/session context onto the wrapper through `session_context.bind_wrapper_context(...)`.
   - Extracts `wrapper = session_data.wrapper`.
   - `start_time = loop.time()`.
   - Calls:

```python
result = await wrapper.run_query(
    query=resolve_request_query(query=request.query, input_payload=request.input),
    max_steps=request.max_steps,
    server_name=request.server_name,
)
```

   - `end_time = loop.time()`.
   - Reads `steps_used = wrapper.steps_used`.
   - Reads `server_used = getattr(wrapper, "last_server_used", None)`.
   - Returns `QueryResponse` (including `has_mcp_servers` when available from the wrapper).

4. For structured multimodal input with images:
   - `MCPWrapper.run_query(...)` keeps the public API shape unchanged.
   - After `before_model` guardrails, `QueryImageResolver` normalizes each image.
   - Inline base64 images pass through unchanged.
   - Remote URL images are fetched by `RemoteImageFetcher`, validated, and converted to internal data URLs.
   - `build_model_query(...)` receives only normalized provider-ready image inputs.

5. Builds `QueryResponse`:

```json
{
  "session_id": "<uuid>",
  "result": { "...": "..." },
  "execution_time": 8.9248,
  "steps_used": 1,
  "timestamp": "2025-12-11T...",
  "server_used": "filesystem",
  "has_mcp_servers": true
}
```

**Important behavior:**

- If `mcp_servers` is empty for the session, mcp-use logs warnings like:
  - "No MCP servers defined in config"
- However, the LLM-only execution still works.  
- `steps_used` is typically `1` (single LLM response) in such cases.


**Guardrails (before_model / after_model):**

mcp-bridge runs guardrails through a LangChain-style pipeline inside `MCPWrapper.run_query()`:

- **before_model**: runs on the user query (input) before calling the LLM.
- **after_model**: runs on the LLM output before returning it to the client.

For multimodal requests, `before_model` guardrails receive only the text associated with the request.
Image content is normalized after guardrails and the provider/model runtime receives only internal data URLs, never the original remote URL.

**Enablement rule (auto-enable):**

- If the session creation request omits the `guardrails` field entirely, guardrails are **disabled** by default.
- If the request provides a `guardrails` object and omits `guardrails.enabled`, mcp-bridge **auto-enables** guardrails (`enabled=true`).
- If `guardrails.enabled` is provided explicitly, it always wins (you can force-disable guardrails even if specific guardrail settings are present).

Currently used guardrails include:

- **PII**: redact/block on input/output (Strategy 3: shared `mode` + per-phase overrides).
- **Bias**: after_model-only detector (block/off), typically calling `bias-detector-service`.

Implementation note:

- `MCPWrapper` remains the public boundary used by the rest of the application.
- Internal MCP boundary concerns are split into focused helper modules:
  - `runtime/capabilities.py`
  - `runtime/tools.py`
  - `guardrails/wrapper.py`
  - `guardrails/pii.py`
  - `guardrails/bias.py`
  - `runtime/transport.py`
  - `runtime/llm.py`

**Async query operations (`POST /sessions/{session_id}/query-operations`)**

- Accept the same legacy `query` or structured multimodal `input` payloads.
- Public operation metadata stores only a safe multimodal summary.
- Raw base64 image data is not exposed through public async metadata.
- Remote URL downloads stay internal to the bridge and are never exposed in operation metadata.

**Failure modes:**

- Session not found or tenant mismatch → HTTP 404.
- `ConfigurationError` → HTTP 400.
- `MCPWrapperError` → HTTP 502.
- Unexpected errors → HTTP 500.

---

### 2.5 Delete Session (`DELETE /sessions/{session_id}`)

**Flow:**

1. `tenant_ctx = get_tenant_context(...)`.
2. Route delegates to `session_service.delete_session(...)`.
3. `session_service.delete_session(...)`:
   - First resolves tenant ownership through `session_context.get_tenant_session(...)`.
   - Then schedules background deletion:

```python
background_tasks.add_task(
    session_manager.delete_session,
    session_id,
    tenant_ctx.tenant_id,
)
```

4. `SessionManager.delete_session(session_id, tenant_id)`:
   - Ensures session exists and belongs to the tenant.
   - Clears pending elicitation state and background query-operation tasks.
   - Closes the `MCPWrapper` and removes the session through `SessionStore.remove(...)`.

5. Route returns:

```json
{
  "message": "Session <session_id> deleted successfully"
}
```

**Failure modes:**

- Session not found / tenant mismatch → HTTP 404.
- MCP cleanup issues → may log errors but typically not surfaced to client unless catastrophic.

---

## 3. A2A Flow: Client → mcp-bridge → A2A Agent (via a2a-sdk)

### 3.1 Current A2A Architecture (SDK-based)

- A2A agents are configured in `settings.a2a.agents` (config-driven). Each agent entry includes:
  - `enabled`, `label`
  - `card_url` (full URL to the Agent Card; used as the source of truth)
  - optional auth + headers + timeouts
  - (legacy/compat) `runtime_url` may exist, but is not required for SDK-based calls.

- At runtime, mcp-bridge uses an internal `A2AClient` wrapper (see `a2a_client.py`) that:
  1. Lazily builds a per-agent `httpx.AsyncClient` using configured headers and timeout.
  2. Derives `(base_url, relative_card_path)` from `card_url`.
  3. Connects via the official SDK (`ClientFactory.connect(...)`) to resolve the Agent Card and obtain an SDK client.
  4. Reuses the per-agent SDK client for subsequent requests.

This keeps the REST surface stable while delegating protocol specifics (Agent Card resolution, message streaming, task polling)
to the official A2A SDK.

### 3.2 List A2A Agents (`GET /a2a/agents`)

1. Route fetches `a2a_settings = settings.a2a`.
2. If `not a2a_settings.enabled`: returns `[]`.
3. Iterates over `a2a_settings.agents.items()`.
4. Returns a list of `A2AAgentSummary` entries.

Notes:
- Today this is primarily a **config listing** (useful for UIs/visual builders).
- A future enhancement can enrich this with Agent Card-derived capabilities (skills) by resolving cards on-demand.

Failure modes:
- Misconfigured `settings.a2a` may cause 500; current implementation should wrap errors with a clear message.

### 3.3 Execute A2A Message (`POST /a2a/agents/{agent_id}/messages`)

Request model: `A2AMessageRequest`
- `goal: str` (required, non-empty)
- `input: dict | null` (optional)
- `metadata: dict | null` (optional)
- `blocking: bool = true` (REST convenience flag)
- `client_task_id: str | null` (optional)

Flow (high-level):
1. Route retrieves `a2a_settings = settings.a2a`.
2. If `not a2a_settings.enabled` → HTTP 400 with structured A2A error (`code="A2A_DISABLED"`).
3. Looks up `conf = a2a_settings.agents.get(agent_id)`:
   - if missing → HTTP 404
   - if `conf.enabled is False` → HTTP 404 (treat as not found)
4. Calls `A2AClient.send_message(agent_id, goal, blocking=..., request_metadata=...)`.

SDK behavior (as implemented in `a2a_client.py`):
- Builds an SDK message object via `create_text_message_object(content=goal)`.
- Streams events from `client.send_message(...)`.
  - The SDK can yield either:
    - `(Task, UpdateEvent|None)` tuples (task-based execution), or
    - a final `Message` (message-only execution).
- If `blocking=false`, the wrapper returns as soon as it receives the first Task event (task id).
- If the agent is message-only (no task emitted), the wrapper returns a message-style result.

Response mapping:
- If a Task was observed → return `mode="task"`, include `task_id` and `status`.
- Otherwise → return `mode="blocking"` with the final message content (`task_id=null`).

Failure modes:

- All A2A endpoint errors are returned under `detail` using a consistent schema:
  - `detail.code`, `detail.message`
  - `detail.operation` (e.g. `send_message`, `get_task`)
  - `detail.agent_id` for agent-scoped endpoints
  - `detail.task_id` for task-scoped endpoints
  - optional `detail.field` (schema validation) and `detail.upstream` (pass-through/debug)

- Invalid `card_url` → 4xx/5xx surfaced as an A2A client error.
- Remote connectivity issues / timeouts → 502/504 style error (depending on error mapping).
- Remote protocol/schema errors → returned with a descriptive error payload when possible.


## 4. Blocking vs Task-Based A2A Execution

### 4.1 Current Behavior (SDK-based)

- `A2AMessageRequest.blocking` controls how long mcp-bridge waits while streaming SDK events.
- The bridge does **not** maintain a separate local task store; task identity and state live on the remote A2A agent.

Observed behaviors:
- `blocking=true`:
  - The bridge consumes the SDK event stream until completion (or timeout).
  - The response may be task-based (if the agent emits a Task) or message-only (if it does not).

- `blocking=false`:
  - The bridge returns as soon as it receives the first Task event (task id).
  - If the agent never emits a Task (message-only agent), the bridge will return a blocking-style response with `task_id=null` and `mode="blocking"`.

### 4.2 Poll Task Status (`GET /a2a/agents/{agent_id}/tasks/{task_id}`)

- This endpoint uses the official SDK to retrieve the latest task status from the remote A2A agent.
- Internally it calls `A2AClient.get_task(agent_id, task_id, history_length=...)` and maps the returned SDK Task object
  into `A2ATaskStatusResponse`.

Hardened/normalized REST behavior:
- Message-only agents (task polling not applicable) → HTTP **409** with structured error `code="A2A_TASK_NOT_APPLICABLE"` and `operation="get_task"`.
- Task id not found → HTTP **404** with structured error `code="A2A_TASK_NOT_FOUND"` and `operation="get_task"`.
- Transport/connect/timeout issues are mapped using the same structured A2A error schema (`detail.code`, `detail.message`, `detail.operation`, `detail.agent_id`, `detail.task_id` when applicable, optional `detail.upstream`).
- Returned `status` is normalized to one of: `queued|running|succeeded|failed|unknown`.



Status normalization (mapping):
- Upstream task status strings are mapped best-effort into the canonical set above.
- Missing/unknown statuses are returned as `unknown`.

## 5. Who Decides Agent Collaboration?

In the current architecture:

- **mcp-bridge** does *not* orchestrate multi-agent collaboration.
- **A2A agents** themselves may internally call other A2A agents (depending on the agent’s implementation and the A2A protocol).
- **Visual builder / client** decides:
  - When to call MCP sessions (queries)
  - When to call A2A agents
  - In which order
  - How to combine results

This aligns with the project decision to keep mcp-bridge as a **thin integration layer**, not a global orchestrator.

---

## 6. Role (or Non-Role) of NATS

- In A2A reference implementations, **NATS** is often used as a message bus for agent-to-agent communication.
- In **mcp-bridge**:
  - NATS is **not directly used or managed**.
  - If a remote A2A agent uses NATS, it is an implementation detail of that agent.
  - mcp-bridge only communicates via HTTP/JSON (and in the future via A2A’s JSON-RPC over HTTP/websocket if the SDK requires).

Therefore:

- NATS is **out of scope** for mcp-bridge’s concerns.
- No NATS configuration or dependency is needed in this project as of now.

---

## 7. Failure Modes and Constraints

### 7.1 MCP Side

- **Session limit reached**:
  - `MAX_ACTIVE_SESSIONS` enforced in `SessionManager.create_session`.
  - Client receives HTTP 429.

- **Invalid or missing MCP configuration**:
  - `ConfigurationError` can be thrown by `MCPWrapper` or `SessionManager`.
  - Client receives HTTP 400.

- **mcp-use internal errors**:
  - Typically wrapped as `MCPWrapperError`.
  - Client receives HTTP 502.

- **No MCP servers**:
  - Not an error; LLM-only mode.
  - `mcp-use` logs warnings but query works.

- **Multi-tenancy mismatches**:
  - If session belongs to a different tenant, `SessionNotFoundError` is raised.
  - Mapped to HTTP 404 to avoid leaking existence of foreign sessions.

### 7.2 A2A Side

- **Misconfiguration**:
  - Disabled `settings.a2a` → HTTP 400 (`A2A_DISABLED`); unknown/disabled `agent_id` → HTTP 404 (`A2A_AGENT_NOT_FOUND`).
  - Missing/invalid `card_url` → A2A client error (cannot resolve Agent Card).
  - Invalid auth config (e.g. missing token/header_name for the selected auth type) → A2A client error.

- **Remote agent unreachable**:
  - TCP timeout, DNS failure, etc. (during card resolution, message send, or task polling) → `httpx` error via the SDK.
  - Mapped to HTTP 502/504 with a generic error message (unless more specific mapping is implemented).

- **Remote agent returns error**:
  - Remote transport/protocol errors surfaced by the SDK are propagated as HTTP errors (best-effort mapping).

- **Compatibility / partial compliance**:
  - SDK-based integration maximizes interoperability, but real agents may differ:
    - some agents are message-only (no Task), so polling is not applicable;
    - some agents emit Tasks but with limited status granularity;
    - SDK/version mismatches may surface as schema/validation errors.
### 7.3 System Constraints

- In-memory session store:
  - Not suitable for multi-instance or long-lived production without external persistence.

- No global orchestration:
  - All broader flows must be handled by the visual builder or dedicated orchestrators, not by mcp-bridge.

---

## 8. Summary of Flows

1. **MCP session lifecycle**:
   - `POST /sessions` → `routes/sessions.py` → `session_service.create_session` → `SessionManager.create_session` → `MCPWrapper.initialize` → `SessionData` stored in `SessionStore`.
   - `POST /sessions/{id}/query` → `routes/queries.py` → `query_service.execute_query` → `MCPWrapper.run_query` → LLM + MCP tools → `QueryResponse`.
   - `GET /sessions` / `GET /sessions/{id}` → thin route → `session_service` → tenant-aware `SessionStore` lookup/list.
   - `DELETE /sessions/{id}` → thin route → `session_service.delete_session` → tenant-checked cleanup in `SessionManager`.

2. **A2A agent usage (current)**:
   - `GET /a2a/agents` → static config listing (UI-friendly).
   - `POST /a2a/agents/{id}/messages` → a2a-sdk `send_message` (Agent Card via `card_url`) → `A2AMessageResponse`.
   - `GET /a2a/agents/{id}/tasks/{task_id}` → a2a-sdk `get_task` → `A2ATaskStatusResponse`.
3. **Blocking vs task**:
   - `blocking=true` → wait for completion (may still return a task-based result if the agent emits a task).
   - `blocking=false` → return early on first Task event when available; message-only agents may still return a final message.
4. **Orchestration & NATS**:
   - Orchestration lives outside mcp-bridge.
   - NATS is not part of mcp-bridge; any NATS usage is within remote A2A agents.
