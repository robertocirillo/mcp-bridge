# Examples

Small, public-friendly examples for the core `mcp-bridge` flow:

1. create a session
2. run a query
3. optionally poll an async query operation

These examples are dependency-free:

- Python uses the standard library only
- Node.js uses built-in APIs only

## Prerequisites

- A `mcp-bridge` instance must already be running.
- The `mcp-bridge` runtime must already have usable LLM credentials/configuration on the server side.
- The `mcp-bridge` runtime must be able to launch the configured MCP server process.
- These examples use `@modelcontextprotocol/server-filesystem` via `npx`, consistent with the main project docs.
- If you use `@modelcontextprotocol/server-filesystem`, the configured root path must exist in the runtime or container where that MCP server process executes.

These examples focus on the main product path: the REST bridge for MCP sessions and queries, with optional session-scoped guardrails configured at session creation time when needed.

## Default environment variables

- `MCP_BRIDGE_BASE_URL=http://localhost:8000`
- `MCP_BRIDGE_LLM_PROVIDER=openai`
- `MCP_BRIDGE_LLM_MODEL=gpt-4o-mini`
- `MCP_SERVER_ROOT=/tmp`

Optional headers:

- `MCP_BRIDGE_TENANT_ID`
- `MCP_BRIDGE_RUN_ID`

## Python example

Run:

```bash
python3 examples/python/session_query.py
```

What it does:

- creates a session
- runs a synchronous query with `POST /sessions/{session_id}/query`
- deletes the session at the end

## Node.js example

Run:

```bash
node examples/javascript/query_operation_poll.mjs
```

What it does:

- creates a session
- starts an async query operation with `POST /sessions/{session_id}/query-operations`
- polls `GET /sessions/{session_id}/query-operations/{operation_id}` until completion
- deletes the session at the end
