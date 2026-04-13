# Roadmap

This roadmap is directional. It highlights the work that most improves the public usefulness of `mcp-bridge` without implying dates or commitments.

## Near-term priorities

### 1. Durable runtime state

Sessions, async query operations, and pending interaction state are still in memory. The main platform gap is durable persistence that survives restarts and supports cleaner multi-instance deployments.

### 2. Public-edge hardening

The service is easiest to run behind a trusted edge today. The most important hardening work is stronger authentication and authorization hooks, rate limiting, and safer defaults for public deployments.

### 3. Better observability

Health endpoints and logs exist, but operators still need clearer metrics, tracing, and visibility into query execution, failures, and guardrail outcomes.

### 4. Simpler core boundaries

The session, query, and runtime paths should keep getting smaller and easier to follow. Refactoring priority is on reducing complexity in the hottest code paths while keeping optional integrations clearly separated from the core MCP bridge.

## Current limitations

- Active sessions and query-operation state are single-process and in-memory.
- Horizontal scaling is limited until state is shared across instances.
- Authentication, authorization, and rate limiting should be enforced upstream today.
- Uploaded assets use short-lived local temporary storage rather than durable shared storage.
- Multimodal behavior depends on the configured provider, model, and runtime capabilities.
