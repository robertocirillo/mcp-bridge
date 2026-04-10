# GITHUB_REPO_SETTINGS.md

Suggested GitHub repository metadata for `mcp-bridge`.

## Positioning notes

Keep the repository description aligned with the current product scope:

- Primary identity: REST bridge to the MCP ecosystem
- Runtime foundation: powered by `mcp-use`
- Main differentiator: session-scoped guardrail enforcement around LLM interactions
- Secondary capability: A2A support exists, but should not lead the repository metadata

Avoid framing the project as a full MCP control plane, an OpenAI-compatible drop-in layer, or an A2A-first product.

## Repository description

Recommended:

`REST bridge to the MCP ecosystem, powered by mcp-use, with session-scoped guardrail enforcement around LLM interactions.`

Shorter alternative:

`REST bridge to MCP, powered by mcp-use, with session-scoped guardrails for LLM interactions.`

## GitHub topics

Recommended topics:

- `mcp`
- `model-context-protocol`
- `rest-api`
- `fastapi`
- `mcp-use`
- `guardrails`
- `llm`
- `ai-agents`
- `openapi`
- `multimodal`

Optional only if maintainers want broader adjacent discoverability:

- `a2a`
- `docker`
- `tool-calling`

## Homepage / social preview copy

Recommended long copy:

`mcp-bridge exposes MCP capabilities through a stable REST API, powered by mcp-use. It adds session-scoped guardrail enforcement around LLM interactions so clients can consume MCP over HTTP without embedding MCP orchestration directly.`

Short social preview copy:

`REST bridge to the MCP ecosystem, powered by mcp-use, with session-scoped guardrails around LLM interactions.`

## Short tagline options

- `REST bridge to MCP with session-scoped guardrails`
- `Stable REST access to MCP, powered by mcp-use`
- `Expose MCP over REST with session-aware guardrails`
- `REST API for MCP sessions, queries, and controlled tool access`
- `HTTP bridge to MCP with managed sessions and guardrails`
