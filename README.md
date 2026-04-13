# mcp-bridge

`mcp-bridge` is a REST bridge to the MCP ecosystem, powered by [`mcp-use`](https://github.com/mcp-use/mcp-use), with session-scoped guardrail enforcement around LLM interactions.

It gives applications a stable HTTP boundary for MCP-backed sessions, queries, and controlled tool access without embedding MCP orchestration directly in the caller.

## What this is

- A FastAPI service that exposes MCP sessions and query flows over REST
- A thin service layer on top of `mcp-use`
- A session-aware guardrail boundary around LLM-driven interactions and tool results

## What this is not

- Not a full MCP control plane
- Not a persistent workflow engine
- Not a built-in auth or rate-limiting layer
- Not primarily an A2A platform

## Why use it

- Keep MCP orchestration server-side and integrate over HTTP
- Create, reuse, and clean up managed sessions instead of handling runtime state in every client
- Apply guardrails consistently at the session boundary
- Support synchronous queries and asynchronous query operations through the same API
- Reach beyond query execution when needed with prompt and resource endpoints

Secondary capability: `mcp-bridge` can proxy configured A2A agents through `/a2a`, but that path is experimental and not the primary product story.

## Key capabilities

- Session lifecycle over REST: create, inspect, list, and delete sessions
- Query execution over MCP-backed sessions with `mcp-use`
- Session-scoped guardrails configured at session creation
- Asynchronous query operations for longer-running work
- Prompt and resource access for MCP servers
- Optional multi-tenancy via request headers
- Optional multimodal query support, depending on model/provider support

## Quickstart

Requirements:

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)
- At least one LLM credential configured in `.env`
- An MCP server to launch or connect to

The example below uses the filesystem MCP server through `npx`.

```bash
uv sync
cp .env.example .env
# set OPENAI_API_KEY=...
uv run python main.py
```

Once the service is running:

- Swagger / OpenAPI: `http://localhost:8000/docs`
- Health check: `http://localhost:8000/health`

Create a session:

```bash
curl -s -X POST "http://localhost:8000/sessions" \
  -H "Content-Type: application/json" \
  -d '{
    "llm_provider": {
      "provider": "openai",
      "model": "gpt-4o-mini"
    },
    "mcp_servers": {
      "filesystem": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
      }
    }
  }'
```

Run a query in that session:

```bash
curl -s -X POST "http://localhost:8000/sessions/<session_id>/query" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Use the filesystem MCP tools to list the files in /tmp.",
    "max_steps": 10
  }'
```

## Short examples

Start an async query operation:

```bash
curl -s -X POST "http://localhost:8000/sessions/<session_id>/query-operations" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Inspect /tmp and summarize what is there."
  }'
```

Poll it:

```bash
curl -s "http://localhost:8000/sessions/<session_id>/query-operations/<operation_id>"
```

Guardrails are configured per session in `POST /sessions`. For copy-paste client examples in Python and Node.js, see [examples/README.md](examples/README.md).

## Current limitations

- Sessions, async operations, and pending interaction state are stored in memory
- Restarting the service loses active runtime state
- Horizontal scaling and multi-instance coordination are not first-class yet
- Authentication, authorization, and rate limiting are expected to sit upstream
- Multimodal support depends on the configured provider and model capabilities
- A2A support is secondary and experimental compared with the MCP REST bridge

## Where next

- [examples/README.md](examples/README.md) for minimal client examples
- [docs/ROADMAP.md](docs/ROADMAP.md) for current gaps and planned work
- [docs/ARCHITECTURE_FLOW.md](docs/ARCHITECTURE_FLOW.md) for the runtime flow
- [docs/DECISIONS.md](docs/DECISIONS.md) for design notes
- `http://localhost:8000/docs` for the full API surface
- `Dockerfile` and the compose files for containerized local runs
