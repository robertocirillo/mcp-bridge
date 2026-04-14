# mcp-bridge

`mcp-bridge` is a REST bridge to the MCP ecosystem, powered by [`mcp-use`](https://github.com/mcp-use/mcp-use), with session-scoped guardrail enforcement around LLM interactions.

Built on the `mcp-use` runtime, it inherits broad MCP server compatibility from the underlying runtime rather than targeting only a small fixed set of MCP servers. It exposes MCP-backed sessions and queries over HTTP so applications can integrate with the MCP ecosystem through a service boundary instead of embedding MCP orchestration directly.

## What this is

- A FastAPI service that exposes MCP sessions and query flows over REST
- A service built on `mcp-use` for MCP connectivity, broad MCP server compatibility, and LLM-backed execution
- A session-aware guardrail boundary around LLM interactions

## What this is not

- Not a full MCP control plane
- Not a persistent workflow engine
- Not a built-in auth or rate-limiting layer
- Not primarily an A2A platform

`/a2a` remains a secondary, experimental surface compared with the MCP REST bridge, and is disabled by default until explicitly configured.

## Quickstart

Requirements:

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/)
- Node.js + npm (required for the quickstart example below, which launches the filesystem MCP server via `npx`)
- At least one usable LLM provider configured (cloud API key or reachable local Ollama)
- An MCP server to launch or connect to

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

Guardrails are configured per session in `POST /sessions`, so different sessions can enforce different safety policies.

## Guardrails example

For example, the session below enables a simple PII policy at session creation time:

```bash
curl -s -X POST "http://localhost:8000/sessions" \
  -H "Content-Type: application/json" \
  -d '{
    "llm_provider": {
      "provider": "openai",
      "model": "gpt-4o-mini"
    },
    "guardrails": {
      "enabled": true,
      "pii": {
        "input_mode": "block",
        "output_mode": "redact"
      }
    },
    "mcp_servers": {
      "filesystem": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
      }
    }
  }'
```

With a session like this, prompts containing PII can be blocked before they reach the model, and model output can be redacted before it is returned through the bridge.

The same session-scoped guardrail model can also be extended with external services, for example for bias detection, without changing the REST interaction pattern. When that integration is enabled, the external bias-detection service base URL is configured through `BIAS_DETECTOR_SERVICE_BASE_URL`.

## Current limitations

- Sessions, async operations, and pending interaction state are stored in memory
- Restarting the service loses active runtime state
- Horizontal scaling and multi-instance coordination are not first-class yet
- Authentication, authorization, and rate limiting are expected to sit upstream
- Multimodal support depends on the configured provider and model capabilities
- A2A support is secondary, experimental, and opt-in compared with the MCP REST bridge

## Where next

- [examples/README.md](examples/README.md) for minimal client examples
- `http://localhost:8000/docs` for the full API surface
- [docs/ROADMAP.md](docs/ROADMAP.md) for current gaps and planned work
- [LICENSE](LICENSE)
- [SECURITY.md](SECURITY.md)
