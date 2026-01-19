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

---

## 2. MCP Flow: Client → MCP-Bridge → MCP-Use → LLM + MCP Servers

### 2.1 Create MCP Session (`POST /sessions`)

**Actors:**

- Client (visual builder, workflow engine, etc.)
- FastAPI route `create_session`
- `SessionManager`
- `MCPWrapper` (mcp-use wrapper)

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
  "use_server_manager": false,
  "guardrails": {
    "enabled": true,
    "pii": {
      "mode": "redact",
      "input_mode": "block",
      "output_mode": "redact"
    }
  }
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
   - Treats `request` as `SessionConfig` (`SessionCreateRequest` inherits from it).
   - Calls `session_id = await session_manager.create_session(config=request, tenant_id=tenant_ctx.tenant_id, run_id=tenant_ctx.run_id)`.

3. `SessionManager.create_session(...)`:
   - Resolves and applies **session-scoped guardrails** (LangChain-style `before_model` / `after_model`) on the `MCPWrapper`.
     - `guardrails.enabled=false` disables all guardrails for the session.
     - For PII, `mode` is a shared default and `input_mode` / `output_mode` can override per phase.
     - PII output handling is also applied to **MCP tool results** before they are fed back into the agent context:
       - `output_mode=redact` => redact string values recursively
       - `output_mode=block` => block the request (HTTP 403) if PII is detected in tool results
       - `output_mode=off` or `guardrails.enabled=false` => no tool-result processing (tool policy is still enforced)


   - Acquires `self._lock`.
   - Checks `len(self._sessions) < settings.MAX_ACTIVE_SESSIONS`, otherwise raises `MaxSessionsExceededError`.
   - Generates a new `session_id = uuid4()`.
   - Instantiates `MCPWrapper` with LLM and MCP config.
   - Calls `await wrapper.initialize()`:
     - `mcp-use` initializes client, sessions, and tools.
     - If `mcp_servers` empty, `mcp-use` logs warnings but continues.
   - Creates `SessionData`:
     - `session_id`, `config`, `wrapper`
     - `created_at = now()`, `last_used = now()`
     - `query_count = 0`
     - `tenant_id = tenant_id`, `last_run_id = run_id`
   - Stores in `self._sessions[session_id]`.
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
- `ConfigurationError` / `MCPWrapperError` → HTTP 502.
- Any other unexpected error → HTTP 500.

---

### 2.2 List MCP Sessions (`GET /sessions`)

**Flow:**

1. FastAPI resolves `tenant_ctx = get_tenant_context(...)`.
2. Route calls `sessions_data = await session_manager.list_sessions(tenant_id=tenant_ctx.tenant_id)`.
3. `SessionManager.list_sessions(tenant_id)`:
   - Iterates over `self._sessions.values()`.
   - If `tenant_id` is not `None`, filters to matching sessions.
   - Returns a list of dictionaries, e.g.:

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
    "llm_model": "gpt-4.1-mini",
    "tenant_id": "tenant-123"
  }
]
```

4. Route converts them to list of `SessionInfo` Pydantic models and returns.

**Key point:** tenants **see only their own sessions** when multi-tenancy is enabled.

---

### 2.3 Get Session Info (`GET /sessions/{session_id}`)

**Flow:**

1. `tenant_ctx = get_tenant_context(...)`.
2. Route calls `session_data = await session_manager.get_session_for_tenant(session_id, tenant_ctx.tenant_id)` (or equivalent logic inside `get_session`).
3. `SessionManager`:
   - Validates that `session_id` exists.
   - If multi-tenancy is enabled and `session_data.tenant_id != tenant_id`, raises `SessionNotFoundError`.
   - Updates `last_used`.
   - Returns `SessionData`.
4. Route maps `SessionData` to `SessionInfo` and returns.

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

**Flow:**

1. `tenant_ctx = get_tenant_context(...)` (even if not explicitly used right now, session has tenant id bound).
2. Route:
   - Retrieves session via `await session_manager.get_session(session_id)` (which already enforces tenant ownership in newer versions).
   - Extracts `wrapper = session_data.wrapper`.
   - Applies **before_model guardrails** (e.g. input PII) inside `wrapper.run_query(...)` before calling the model.
   - `start_time = loop.time()`.
   - Calls:

```python
result = await wrapper.run_query(
    query=request.query,
    max_steps=request.max_steps,
    server_name=request.server_name,
)
```

   - Applies **after_model guardrails** (e.g. output PII) inside `wrapper.run_query(...)` before returning the response.
   - MCP tool calls are proxied so that:
     - tool policy (`disallowed_tools`) is enforced **before** each tool execution (independent of `guardrails.enabled`)
     - tool results can be post-processed **before** they are incorporated into the agent run
       - `guardrails.enabled=false` or PII `output_mode=off` => no tool-result processing
       - PII `output_mode=redact` => recursively redact string values
       - PII `output_mode=block` => block the request with HTTP 403 (`detail.code=PII_DETECTED`, `phase=tool_result`)
   - `end_time = loop.time()`.
   - `session_data.register_query()` (increments `query_count`).
   - Reads `steps_used = wrapper.steps_used`.
   - Reads `server_used = getattr(wrapper, "last_server_used", None)`.

3. Builds `QueryResponse`:

```json
{
  "session_id": "<uuid>",
  "result": { "...": "..." },
  "execution_time": 8.9248,
  "steps_used": 1,
  "timestamp": "2025-12-11T...",
  "server_used": "filesystem"
}
```

**Important behavior:**

- If `mcp_servers` is empty for the session, mcp-use logs warnings like:
  - "No MCP servers defined in config"
- However, the LLM-only execution still works.  
- `steps_used` is typically `1` (single LLM response) in such cases.

**Failure modes:**

- Session not found or tenant mismatch → HTTP 404.
- Input guardrail violations (e.g. `PII_DETECTED` in `before_model` with `block`) → HTTP 403 with structured `detail`.
- `ConfigurationError` → HTTP 400.
- `MCPWrapperError` → HTTP 502.
- Unexpected errors → HTTP 500.

---

### 2.5 Delete Session (`DELETE /sessions/{session_id}`)

**Flow:**

1. `tenant_ctx = get_tenant_context(...)`.
2. Route:
   - Schedules background deletion:

```python
background_tasks.add_task(
    session_manager.delete_session_for_tenant,
    session_id,
    tenant_ctx.tenant_id,
)
```

   or an equivalent method that checks the tenant id.

3. `SessionManager.delete_session_for_tenant(session_id, tenant_id)`:
   - Ensures session exists and belongs to the tenant.
   - Closes the `MCPWrapper` (if close logic exists) and removes it from `_sessions`.

4. Route returns:

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
   - `POST /sessions` → `SessionManager.create_session` → `MCPWrapper.initialize` → `SessionData` stored.
   - `POST /sessions/{id}/query` → `MCPWrapper.run_query` → LLM + MCP tools → `QueryResponse`.
   - `GET /sessions` → tenant-filtered sessions list.
   - `GET /sessions/{id}` → tenant-checked session info.
   - `DELETE /sessions/{id}` → tenant-checked cleanup.

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
