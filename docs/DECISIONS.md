# Decisions

This file keeps only the decisions that still explain the current shape of `mcp-bridge`.

## Accepted decisions

### 1. The project stays a thin REST bridge over MCP runtime components

- The primary product is the session/query HTTP API in front of MCP.
- `mcp-use` remains the runtime layer for MCP-backed execution rather than being reimplemented inside this repository.
- The bridge owns HTTP contracts, lifecycle, validation, guardrails, and error mapping.

Why this matters:
- It keeps the service small enough to reason about.
- It avoids forking MCP runtime behavior that already exists upstream.

### 2. The session is the main runtime boundary

- LLM settings, MCP server definitions, guardrail options, and tenant context are bound to a session.
- Query execution, prompt/resource access, and async query operations all reuse that same session-scoped runtime.
- Session and operation state are currently stored in memory.

Why this matters:
- It provides a stable place to enforce policy and track execution state.
- It keeps the public API simpler than exposing raw runtime primitives directly.

### 3. Multi-tenancy is enforced at the HTTP layer

- `X-Tenant-Id` and `X-Run-Id` are resolved by the API layer, not embedded into MCP or A2A protocol payloads.
- Session lookup, listing, and deletion are tenant-aware when multi-tenancy is enabled.
- Tenant identifiers are supplied by the caller; the bridge enforces isolation but does not mint tenant identities.

Why this matters:
- It keeps tenancy concerns explicit and decoupled from downstream protocols.
- It supports both single-tenant and multi-tenant deployments with the same core runtime model.

### 4. Sessions may be MCP-backed or LLM-only

- `mcp_servers` may be empty.
- A session without MCP servers still supports model interaction and guardrail behavior.

Why this matters:
- It keeps the API usable for clients that need a guarded LLM boundary before adding MCP servers.
- It avoids making MCP server configuration mandatory for every session.

### 5. Optional integrations should stay optional

- A2A support remains secondary to the MCP session/query path and is exposed through a separate REST surface.
- The bridge uses the official A2A SDK rather than a custom protocol shim.
- External detector services are integrated as pass-through guardrail dependencies instead of becoming core bridge logic.

Why this matters:
- It preserves a clear core product boundary.
- It reduces the chance of project-specific protocol drift.

## Non-goals

- `mcp-bridge` is not intended to be a workflow orchestrator that coordinates MCP and A2A steps internally.
- It is not a persistent control plane today; durable state and multi-instance coordination are still future work.
- It does not treat edge security concerns such as authentication, authorization, and rate limiting as built-in defaults yet.
