# Roadmap

This document describes the next important improvements for `mcp-bridge`.
It is intentionally directional: no dates, no inflated backlog, and no implied commitment beyond the areas we expect to matter most.

## Current direction

### 1. Durable persistence for sessions and query operations

Today, active sessions, async query operations, and pending interaction state are in-memory only. The next step is a persistence backend that can survive restarts and support cleaner multi-instance operation without changing the public REST contract.

### 2. Stronger auth and public-edge hardening

`mcp-bridge` is currently easiest to run behind a trusted edge. Near-term hardening work is expected to focus on stronger authentication hooks, clearer authorization boundaries, rate limiting, and safer defaults for publicly exposed deployments.

### 3. Observability and metrics

The project already exposes health endpoints and structured logs, but it needs better runtime visibility. The practical target is clearer metrics, request/session tracing, and operator-friendly signals around query execution, failures, and guardrail decisions.

### 4. Hotspot refactors

Some session, query, and runtime orchestration paths are doing too much in one place. Refactoring priority is on reducing complexity in the hottest code paths so behavior stays easier to reason about, test, and extend.

### 5. Clearer separation between the core bridge and optional integrations

The core product is the MCP REST bridge plus session-scoped guardrail enforcement. Optional integrations such as A2A, E2B, and external detector services should become easier to treat as add-ons, with a cleaner boundary around the core MCP/session/query path.

## Known limitations

- Runtime state is still single-process and in-memory; restarts lose active sessions and query-operation state.
- Horizontal scaling is not first-class yet because there is no shared persistence or coordination layer.
- Authentication, authorization, and rate limiting are not built-in public defaults; they should be enforced upstream today.
- Multipart PDF support is query-only. Direct tool invocation remains supported through JSON requests, not multipart PDF forwarding.
- Uploaded assets use short-lived local temporary storage, not a durable shared asset backend.
- Effective image/PDF behavior still depends on the configured provider, model, and runtime capabilities.
