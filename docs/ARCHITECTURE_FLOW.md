# ARCHITECTURE_FLOW.md

## 1. Overview

This document describes how requests flow through **mcp-bridge**, spanning:

- MCP sessions and queries (via `mcp-use`)
- A2A agent invocation (current HTTP shim, future A2A SDK)
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
   - Treats `request` as `SessionConfig` (`SessionCreateRequest` inherits from it).
   - Calls `session_id = await session_manager.create_session(config=request, tenant_id=tenant_ctx.tenant_id, run_id=tenant_ctx.run_id)`.

3. `SessionManager.create_session(...)`:
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
   - `start_time = loop.time()`.
   - Calls:

```python
result = await wrapper.run_query(
    query=request.query,
    max_steps=request.max_steps,
    server_name=request.server_name,
)
```

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

## 3. A2A Flow: Client → mcp-bridge → A2A-like Agent

### 3.1 Current A2A Architecture

- REST surface is designed with future compliance in mind:
  - `GET /a2a/agents`
  - `POST /a2a/agents/{agent_id}/messages`
  - (planned: `GET /a2a/agents/{agent_id}/tasks/{task_id}`)

- Internally, **current implementation is a custom HTTP adapter**:
  - Each configured `A2AAgentConfig` has a `runtime_url`.
  - mcp-bridge calls `runtime_url + "/tasks"` with a simple JSON payload.
  - Response is shaped into `A2AMessageResponse`.

- The official A2A SDK is **not yet integrated**; JSON-RPC, NATS, and protocol-level semantics are still future work.

### 3.2 List A2A Agents (`GET /a2a/agents`)

**Flow:**

1. Route fetches `a2a_settings = settings.a2a`.
2. If `not a2a_settings.enabled`: returns `[]`.
3. Iterates over `a2a_settings.agents.items()`.
4. For each `(agent_id, conf)` where `conf.enabled`:
   - Creates `A2AAgentSummary`:

```json
{
  "agent_id": "local_echo_agent",
  "name": "Local Echo Agent",
  "description": "Simple local A2A agent used for testing.",
  "card_url": "http://localhost:9001/.well-known/agent.json",
  "skills": [],
  "labels": []
}
```

5. Returns the list.

**Failure modes:**

- Misconfigured `settings.a2a` may cause 500; current implementation wraps in HTTP 500 with generic error.

---

### 3.3 Execute A2A Message (`POST /a2a/agents/{agent_id}/messages`)

**Body:** `A2AMessageRequest`:

```json
{
  "goal": "Test the echo agent through mcp-bridge",
  "input": { "foo": "bar", "number": 42 },
  "metadata": {},
  "blocking": true,
  "client_task_id": "optional-client-defined-id"
}
```

**Flow (current HTTP shim):**

1. Route retrieves `a2a_settings = settings.a2a`.
2. If `not a2a_settings.enabled` → HTTP 503 or 400 (depending on implementation).
3. Looks up `conf = a2a_settings.agents.get(agent_id)`:
   - If not found or `not conf.enabled` → HTTP 404.
4. Validates that `conf.runtime_url` is set; otherwise HTTP 500/400.
5. Determines **mode**:
   - `mode = "blocking"` if `request.blocking` is `True`.
   - `mode = "task"` otherwise.

> Currently, only "blocking" semantics are effectively implemented.

6. Decides **effective task id**:

```python
effective_task_id = request.client_task_id or str(uuid.uuid4())
```

7. Builds payload for the echo agent:

```json
{
  "goal": "Test the echo agent through mcp-bridge",
  "input": { "foo": "bar", "number": 42 },
  "taskId": "same-as-effective_task_id",
  "metadata": {}
}
```

8. Builds `tasks_url = conf.runtime_url.rstrip("/") + "/tasks"`.
9. Uses `httpx.AsyncClient` to send `POST tasks_url`:
   - Applies `conf.timeout_seconds` as timeout.
   - Applies authentication / headers based on `conf.auth` and `conf.extra_headers` (future extension).

10. On HTTP error status:
   - Raises HTTPException with the remote status / message.

11. On success:
   - Parses JSON as `data`:

```json
{
  "taskId": "string",
  "status": "completed",
  "output": {
    "echo_goal": "Test the echo agent through mcp-bridge",
    "echo_input": {
      "foo": "bar",
      "number": 42
    },
    "info": "This is a test echo agent. Replace this logic with real work."
  },
  "message": "Task handled successfully by Local Echo Agent."
}
```

12. Maps into `A2AMessageResponse`:

```json
{
  "mode": "blocking",
  "agent_id": "local_echo_agent",
  "task_id": "string",
  "status": "completed",
  "output": {
    "echo_goal": "Test the echo agent through mcp-bridge",
    "echo_input": { "foo": "bar", "number": 42 },
    "info": "This is a test echo agent. Replace this logic with real work."
  },
  "message": "Task handled successfully by Local Echo Agent.",
  "raw_response": {
    "taskId": "string",
    "status": "completed",
    "output": { ... },
    "message": "Task handled successfully by Local Echo Agent."
  }
}
```

**Failure modes:**

- Agent not configured or disabled → HTTP 404.
- Remote agent not reachable or times out → HTTP 504/502.
- Remote agent returns non-2xx → HTTPException with that status.
- JSON parse failure → HTTP 502.
- Any other error → HTTP 500 with "Error executing A2A message".

---

## 4. Blocking vs Task-Based A2A Execution

### 4.1 Current Behavior

- `A2AMessageRequest.blocking` is accepted and used to set `mode` in the response.
- Actual behavior is **always blocking**:
  - The route waits for the remote `/tasks` call to complete.
  - There is no separate task tracking.

So at the moment:

- `blocking=true` → returns final result (blocking).
- `blocking=false` → still behaves like blocking, but the response is labeled `"task"` logically.

### 4.2 Target Behavior with Official A2A SDK

With A2A integration, the plan is:

- `blocking=true`:
  - Use a high-level `message/send` method that blocks until completion or until a reasonable timeout.
  - Return final `output` in a single `A2AMessageResponse`.

- `blocking=false`:
  - Use a task-creation API (`message/send` returning a task handle, or `tasks/create`).
  - Generate a `task_id` consistent with A2A’s notion.
  - Return immediately with `mode="task"`, `status="pending"` and `task_id`.
  - Introduce `GET /a2a/agents/{agent_id}/tasks/{task_id}`:
    - Use A2A SDK (`tasks/get` or similar) to retrieve current state.

---

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
  - Missing `runtime_url`, disabled `settings.a2a` or `conf.enabled=False` → HTTP 404/503/500 depending on context.

- **Remote agent unreachable**:
  - TCP timeout, DNS failure, etc. → `httpx` error.
  - Mapped to HTTP 502/504 with a generic error message.

- **Remote agent returns error**:
  - Non-2xx response is propagated to client as HTTP error.

- **Protocol mismatch**:
  - Because current integration is custom, any third-party agent that expects strict A2A protocol may not be compatible.
  - This is a known limitation; the future SDK-based integration is meant to fix this.

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
   - `GET /a2a/agents` → static config listing.
   - `POST /a2a/agents/{id}/messages` → HTTP call to `runtime_url/tasks` → `A2AMessageResponse`.

3. **Blocking vs task**:
   - Exposed in API, but currently only blocking semantics are implemented; task-style semantics will be added with the A2A SDK.

4. **Orchestration & NATS**:
   - Orchestration lives outside mcp-bridge.
   - NATS is not part of mcp-bridge; any NATS usage is within remote A2A agents.

