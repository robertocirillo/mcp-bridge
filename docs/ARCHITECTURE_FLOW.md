# Architecture Flow

`mcp-bridge` exposes MCP-backed workflows over REST. It owns session state, request validation, and guardrail enforcement at the service boundary, while delegating MCP protocol work to `mcp-use` and configured MCP servers.

## Core boundaries

- API layer: `app/api/routes` and `app/api/services` expose HTTP endpoints, resolve tenant context, and map domain errors to stable responses.
- Session/runtime layer: `app/core/sessions` and `app/core/runtime/mcp_wrapper.py` create sessions, keep in-memory runtime state, and drive query execution.
- Guardrails and multimodal layer: `app/core/guardrails`, `app/core/multimodal`, and `app/core/session_assets` validate inputs, apply session-scoped checks, and prepare image/PDF uploads.
- External systems: LLM providers, MCP servers, optional A2A agents, and optional detector services remain outside the bridge.

## Main runtime flows

### 1. Create a session

- `POST /sessions` accepts LLM settings, MCP server definitions, and optional guardrail configuration.
- `X-Tenant-Id` and `X-Run-Id` are resolved before session creation when multi-tenancy is enabled.
- `SessionManager` creates an `MCPWrapper`, initializes `mcp-use`, and stores the session in memory.
- Sessions may be MCP-backed or LLM-only when `mcp_servers` is empty.

### 2. Execute a query

- `POST /sessions/{session_id}/query` accepts either a legacy text `query` or structured `input`.
- The request is normalized and capability-checked before execution, including multimodal validation for images and uploaded PDFs.
- Session lookup is tenant-aware.
- Guardrails run around model interaction, and tool policy is enforced before MCP tool calls.
- `MCPWrapper` delegates LLM and MCP execution to `mcp-use`, which talks to the configured providers and servers.

### 3. Run asynchronous work

- `POST /sessions/{session_id}/query-operations` starts a long-running query operation or direct tool invocation.
- Operation state is kept in the in-memory query operation store.
- Clients poll `GET /sessions/{session_id}/query-operations/{operation_id}` for status and may resume paused operations through `/resume` when user interaction is required.

### 4. Access session-scoped MCP capabilities

- Prompt and resource endpoints reuse the existing session boundary instead of creating separate runtime state.
- These endpoints stay thin: they resolve the tenant-scoped session, select the MCP server, and forward the request through the wrapper.

## Optional integrations

- `/a2a` exposes a separate REST surface for remote A2A agents through the official SDK.
- A2A is intentionally secondary to the MCP session/query path and is not used as an orchestration engine inside the bridge.
- External detector services can be attached to guardrail flows without changing the core session model.

## Practical limits

- Active sessions, query operations, and pending interaction state are in-memory today.
- Public-edge concerns such as authentication, authorization, and rate limiting are expected to be enforced upstream.
- The bridge does not orchestrate multi-step workflows across MCP and A2A; callers own that composition.
