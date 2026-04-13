# mcp-bridge

`mcp-bridge` is a REST bridge to the MCP ecosystem, powered by [`mcp-use`](https://github.com/mcp-use/mcp-use), with session-scoped guardrail enforcement around LLM interactions.

It exposes MCP-backed sessions and queries over HTTP so applications can integrate with the MCP ecosystem through a service boundary instead of embedding MCP orchestration directly.

## What this is

- A FastAPI service that exposes MCP sessions and query flows over REST
- A service built on `mcp-use` for MCP connectivity and LLM-backed execution
- A session-aware guardrail boundary around LLM interactions

## What this is not

- Not a full MCP control plane
- Not a persistent workflow engine
- Not a built-in auth or rate-limiting layer
- Not primarily an A2A platform

`/a2a` remains a secondary, experimental surface compared with the MCP REST bridge.

## Quickstart

Requirements:

- Python 3.12+
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

- Health check: `http://localhost:8000/health`
- OpenAPI / Swagger: `http://localhost:8000/docs`

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

For a longer-running query, use the async flow:

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

Guardrails are configured per session in `POST /sessions`.

## Current limitations

- Sessions, async operations, and pending interaction state are stored in memory
- Restarting the service loses active runtime state
- Horizontal scaling and multi-instance coordination are not first-class yet
- Authentication, authorization, and rate limiting are expected to sit upstream
- Multimodal support depends on the configured provider and model capabilities
- A2A support is secondary and experimental compared with the MCP REST bridge

## Where next

- [examples/README.md](examples/README.md) for minimal client examples
- `http://localhost:8000/docs` for the full API surface
- [docs/ROADMAP.md](docs/ROADMAP.md) for current gaps and planned work
- [LICENSE](LICENSE)
- [SECURITY.md](SECURITY.md)
