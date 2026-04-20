# Changelog

All notable project-level changes should be recorded here.

## [0.2.1] - 2026-04-20

### Fixed

- Handle empty optional multipart query form fields as absent values instead of raising an error
- Cover multipart query endpoints with regression tests for empty optional form fields

## [0.2.0] - 2026-04-10

Initial changelog entry for the current release line.

### Highlights

- Positions `mcp-bridge` as a REST bridge to the MCP ecosystem powered by `mcp-use`
- Exposes session, query, health, and MCP capability APIs with session-scoped guardrails
- Keeps multipart PDF support query-only; JSON direct tool invocation remains supported
- Includes optional A2A endpoints as a secondary, experimental capability
- Stores active runtime state in memory in the current release line
