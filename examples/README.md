# Examples

Small, public-friendly examples for the core `mcp-bridge` flow:

1. create a session
2. run a query
3. optionally poll an async query operation
4. record or replay a short public-facing REST demo

The Python and Node.js examples are dependency-free:

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
- `MCP_BRIDGE_LLM_PROVIDER=ollama`
- `MCP_BRIDGE_LLM_MODEL=llama3.2:latest`
- `MCP_SERVER_ROOT=<repo>/examples/demo/sample-files`
- `MCP_BRIDGE_REQUEST_TIMEOUT_SECONDS=120`

Optional headers:

- `MCP_BRIDGE_TENANT_ID`
- `MCP_BRIDGE_RUN_ID`

## Bash demo

Run:

```bash
./examples/demo/filesystem_rest_demo.sh
```

What it shows:

- checks `GET /health`
- verifies that the selected provider is advertised by the bridge health response
- prints the main JSON payloads used for the REST requests
- creates a session with the filesystem MCP server via `npx -y @modelcontextprotocol/server-filesystem`
- runs the synchronous `POST /sessions/{session_id}/query` flow
- deletes the session with `DELETE /sessions/{session_id}`

Minimum prerequisites:

- `mcp-bridge` must already be running
- Ollama and the `llama3.2:latest` model must be reachable from the `mcp-bridge` runtime, or you must override provider/model via env
- `curl`, `jq`, `node`, and `npx` must be available
- by default the script uses the sample directory under `examples/demo/sample-files`

On CPU-only machines the sync query can take noticeably longer, so the demo exposes `MCP_BRIDGE_REQUEST_TIMEOUT_SECONDS` and defaults it to `120`.

Override provider/model when needed:

```bash
MCP_BRIDGE_LLM_PROVIDER=ollama \
MCP_BRIDGE_LLM_MODEL=qwen2.5:7b \
./examples/demo/filesystem_rest_demo.sh
```

Increase the sync timeout when needed:

```bash
MCP_BRIDGE_REQUEST_TIMEOUT_SECONDS=180 ./examples/demo/filesystem_rest_demo.sh
```

Point the demo at a different directory when needed:

```bash
MCP_SERVER_ROOT=/private/tmp ./examples/demo/filesystem_rest_demo.sh
```

For a short terminal cast:

- start `mcp-bridge` first in a separate terminal, with Ollama already reachable
- run `./examples/demo/filesystem_rest_demo.sh` from a clean shell
- keep the default sample directory unless you want the listing to show a different demo directory
- use a compact terminal window, about 100 columns and 22-26 rows
- record with `FORCE_COLOR=1`, for example: `asciinema rec demos/mcp-bridge-rest-demo.cast --command "FORCE_COLOR=1 ./examples/demo/filesystem_rest_demo.sh"`
- the script uses ANSI colors when stdout is a terminal; disable them with `NO_COLOR=1`

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

Use a recent Node.js version with built-in `fetch`.

What it does:

- creates a session
- starts an async query operation with `POST /sessions/{session_id}/query-operations`
- polls `GET /sessions/{session_id}/query-operations/{operation_id}` until completion
- deletes the session at the end
