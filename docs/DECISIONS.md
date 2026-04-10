# DECISIONS.md

This file captures key decisions, rejected alternatives, non-goals, and open questions for **mcp-bridge – MCP + A2A integration**.

---

## 1. Accepted Decisions

### D1 – MCP and A2A are separated, coordinated by the visual builder

**Status:** Accepted

**Decision:**

- mcp-bridge exposes **MCP** endpoints and **A2A** endpoints, but **does not orchestrate** between them.
- The **visual builder (client)** is responsible for:
  - Creating MCP sessions
  - Executing MCP queries
  - Calling A2A agents
  - Deciding the order and combining the results

**Rationale:**

- Keeps mcp-bridge small, composable, and easier to reason about.
- Avoids tight coupling between MCP and A2A semantics.
- Leaves freedom for different orchestration strategies, including future ones.

---

### D2 – Multi-tenancy is implemented at the REST/bridge layer

**Status:** Accepted

**Decision:**

- Multi-tenancy is introduced via:
  - `X-Tenant-Id` and `X-Run-Id` HTTP headers
  - `TenantContext` (Pydantic model)
  - `MultiTenancySettings` in `Settings`:
    - `enabled: bool`
    - `require_header: bool`
    - `default_tenant_id: str | None`
- `SessionManager` stores `tenant_id` and `last_run_id` in `SessionData`.
- Session listing (`GET /sessions`), access (`GET /sessions/{id}`), and deletion (`DELETE /sessions/{id}`) are **tenant-aware**:
  - Tenants only see and can delete their own sessions.

**Rationale:**

- Simple and explicit.
- Decouples from MCP and A2A protocols.
- Compatible with both single-tenant and multi-tenant usage.

---

### D3 – Session creation without MCP servers is allowed (LLM-only)

**Status:** Accepted

**Decision:**

- `SessionConfig.mcp_servers` may be an **empty dict**.
- `MCPWrapper` and `mcp-use` must handle the case of **0 MCP servers**.
- mcp-use logs warnings like `"No MCP servers defined in config"`, but execution proceeds.

**Rationale:**

- Visual builder can use mcp-bridge as a pure LLM gateway when needed.
- Avoids forcing the presence of at least one MCP server for every session.

---

### D4 – A2A integration uses the official A2A SDK (a2a-sdk)

**Status:** Accepted

**Decision:**

- mcp-bridge integrates A2A agents using the official **a2a-sdk**.
- The bridge does **not** maintain a parallel custom HTTP shim in production code.
- Agent discovery remains configuration-driven (by `agent_id`), and the SDK resolves the Agent Card from `card_url`.
- The REST surface remains stable:
  - `GET /a2a/agents`
  - `POST /a2a/agents/{agent_id}/messages`
  - `GET /a2a/agents/{agent_id}/tasks/{task_id}`

**Notes:**

- The REST field `blocking` is a REST convenience flag (not an A2A protocol field).
- `blocking=false` does **not** guarantee that the agent returns a Task: some agents may return a final `Message` directly (so `task_id` can be null).
- REST `mode` must reflect the actual response:
  - `mode="task"` only when `task_id` is present
  - otherwise `mode="blocking"`

- `GET /a2a/agents/{agent_id}/tasks/{task_id}` hardened contract:
  - message-only agents → HTTP 409 `A2A_TASK_NOT_APPLICABLE`
  - task not found → HTTP 404 `A2A_TASK_NOT_FOUND`
  - `status` in A2A responses uses the A2A task-state vocabulary:
    `submitted|working|input-required|completed|canceled|failed|unknown`
  - `upstream_state` carries the raw upstream state string when the SDK/agent uses a different representation
  - `queued|running|completed|failed|cancelled` belong to MCP async query operations (`QueryOperationStatus`), not to A2A task-status responses
  - errors include `operation` (e.g. `send_message`, `get_task`) and relevant identifiers (`agent_id`, `task_id` when applicable) in the structured `detail` payload

**Rationale:**

- Avoid implementing bridge-specific behavior that would be thrown away later.
- Maximize interoperability with third-party A2A servers.
- Align early with the official protocol semantics and SDK evolution.


---

### D5 – Tenant ID is generated and owned by the visual builder

**Status:** Accepted

**Decision:**

- `tenant_id` is provided by the **visual builder (client)** via `X-Tenant-Id`.
- mcp-bridge does **not** generate or manage long-lived tenant IDs.
- Configuration (`settings.multi_tenancy`) defines:
  - If multi-tenancy is enabled
  - Whether header is required
  - Default tenant ID for single-tenant or fallback scenarios.

**Rationale:**

- Visual builder already has a notion of users/projects; best place to define tenants.
- mcp-bridge just enforces isolation based on the given IDs.

---

### D6 – A2A does not embed tenant_id in protocol payload

**Status:** Accepted

**Decision:**

- A2A requests (`A2AMessageRequest`) do **not** send `tenant_id` to the agent body.
- Tenancy is handled at the bridge level, not as part of A2A protocol payloads.

**Rationale:**

- Avoids diverging from the official A2A protocol.
- Third-party A2A agents should not be forced to know about tenant IDs.
- Keeps compatibility with future official A2A features.

---

### D7 – API breaking changes are allowed at this stage

**Status:** Accepted

**Decision:**

- Project is not in production; no external users depending on API stability.
- Breaking API changes are allowed if they:
  - Improve alignment with MCP / A2A standards
  - Simplify long-term maintenance
  - Clean up earlier experimentation.

**Rationale:**

- Encourages better design early in the project.
- Avoids long-term technical debt.

---

### D8 – Bias guardrail is service-first, pass-through, and supports cascaded checks

**Status:** Accepted

**Decision:**

- Bias detection runs **after_model** and is performed by an external **bias-detector-service** when `guardrails.bias.base_url` is set.
- mcp-bridge stays **dumb**:
  - no local interpretation of label semantics
  - only **pass-through** of detector configuration and results
  - blocks only when the detector returns `flagged=true` (and mode is `block`)
- The session bias config provides **common defaults** (e.g. `model_id`, `threshold`, `unsafe_labels`) and supports a `checks: []` list
  to execute multiple detector calls **in the same after_model pass** with per-check overrides.
- Fail-closed behavior: when bias is enabled in `block` mode and the detector is unavailable,
  mcp-bridge blocks with HTTP 503 `detail.code="BIAS_DETECTOR_UNAVAILABLE"`.

**Rationale:**

- Keeps the bridge thin and policy-less, while enabling flexible governance in the dedicated detector service.
- Cascaded checks validate multiple models/policies without requiring multiple user queries or multiple sessions.
- Fail-closed is safer for governance-critical deployments.

---

### D9 – Forward optional detector debug features (scores/spans) without changing semantics

**Status:** Accepted

**Decision:**

- mcp-bridge forwards optional detector request flags when configured:
  - `return_all_scores`
  - `return_char_spans`
- When blocking with `BIAS_DETECTED`, mcp-bridge preserves and returns detector details including:
  - `flagged_labels`
  - derived `flagged_label_scores` (score/threshold/margin)
  - `checks_results` (request/response per cascaded check)

**Rationale:**

- Improves observability/debugging for clients (UIs/workflows) without introducing local label logic.
- Enables practical debugging of edge cases where the LLM output differs from the user prompt.

---

### D10 – MCPWrapper remains the façade while policy, guardrail execution, and audit are separated

**Status:** Accepted

**Decision:**

- `MCPWrapper` remains the public/session-facing façade for the MCP boundary.
- `mcp_wrapper.py` remains the single public entry point for the MCP backend boundary.
- The internal split across focused private `mcp_wrapper_*` modules is the chosen direction for the MCP boundary as long as the rest of the application still depends on `MCPWrapper`.
- Tool policy evaluation is handled by `ToolPolicyEngine`.
- Guardrail execution is handled by `GuardrailRunner`.
- Audit/event recording uses the shared audit layer (`AuditEvent`, recorder).
- The recent cleanup around guardrail execution, invocation-context handling, and audit-event recording is part of this same consolidation inside the existing `MCPWrapper` boundary.
- Additional boundary concerns are delegated to focused internal modules:
  - `runtime/capabilities.py`
  - `runtime/tools.py`
  - `guardrails/wrapper.py`
  - `runtime/llm.py`
  - `runtime/transport.py`
  - `guardrails/pii.py`
  - `guardrails/bias.py`
  - `mcp_wrapper_errors.py`
- `MCPRuntimeAdapter` is not introduced now.
- `MCPRuntimeAdapter` should be reconsidered only if a concrete, reusable runtime seam emerges later.
- The MCP runtime boundary stays the same:
  - tool policy is enforced before every MCP tool call
  - query-level guardrails run around query execution (`before_model`, `after_model`)
  - tool-result guardrails run separately on each MCP tool result inside the agent/tool loop

**Rationale:**

- Keeps the runtime behavior and REST API stable while making the boundary easier to reason about.
- Preserves a clear separation between orchestration (`MCPWrapper`) and execution primitives (policy, guardrails, audit, transport, LLM bootstrap).
- Avoids rewriting or forking `mcp-use`.
- Avoids turning `mcp_wrapper.py` into a god module while preserving backend swap flexibility.
- Avoids introducing another abstraction layer before there is a proven reusable runtime seam.

---

## 2. Rejected Alternatives

### R1 – Using A2A as MCP tools (A2A-over-MCP)

**Status:** Rejected

**Idea:**

- Expose A2A agents as MCP tools so that MCP sessions could directly call A2A through MCP.

**Reasons for Rejection:**

- Would violate or distort A2A protocol semantics.
- Creates a tight, confusing coupling between MCP and A2A.
- Harder to keep up with evolving A2A spec.
- Visual builder orchestration becomes less explicit.

---

### R2 – Letting mcp-bridge become a global multi-agent orchestrator

**Status:** Rejected

**Idea:**

- mcp-bridge could orchestrate flows across multiple agents (MCP + A2A), decide who calls whom, chain tasks, etc.

**Reasons for Rejection:**

- Too complex and opinionated.
- Hard to maintain and debug.
- Conflicts with the goal of being a **bridge**, not an orchestrator.
- The visual builder is already in a better position to orchestrate.

---

### R3 – Binding multi-tenancy deeply into A2A protocol

**Status:** Rejected

**Idea:**

- Extend A2A payloads with mandatory `tenant_id` so that remote agents know about tenants.

**Reasons for Rejection:**

- Not part of the official A2A spec.
- Risk of incompatibility with third-party agents.
- Risk of breaking future A2A versions that might introduce their own notions of tenancy or identity.

---

### R4 – Forcing at least one MCP server per session

**Status:** Rejected

**Idea:**

- Disallow sessions without MCP servers (i.e., strictly require at least one MCP server in `mcp_servers`).

**Reasons for Rejection:**

- Unnecessarily restricts valid use cases (LLM-only sessions).
- `mcp-use` is capable of handling 0 MCP servers (with warnings).

---

## 3. Explicit Non-Goals

### NG1 – Persistent storage and clustering

- mcp-bridge currently uses **in-memory session storage**.
- No guarantee of persistence across restarts.
- No built-in support for multi-instance clustering or distributed sessions.
- This may change in the future, but is **explicitly not a requirement right now**.

### NG2 – Acting as an A2A gateway/broker for all traffic

- mcp-bridge’s role is not to be a universal A2A gateway (e.g., multiplexing A2A traffic between many agents for many unrelated clients).
- It is focused on supporting **a visual builder** that needs MCP + A2A in one place.

### NG3 – Implementing NATS or message bus inside mcp-bridge

- Handling NATS or other messaging backplanes is **not** a responsibility of mcp-bridge.
- Any message bus is internal to A2A deployments or other orchestrators.

### NG4 – User authentication & authorization

- mcp-bridge does not handle user auth.
- `tenant_id` is a technical routing key, not a security identity.
- Real authn/authz should be handled upstream (e.g. by the visual builder or API gateway).

---

## 4. Open Questions

These are intentionally left undecided for future iterations.

### Q1 – Per-tenant configuration for A2A agents

- Should different tenants see different subsets of A2A agents?
- How should configuration be structured? Examples:
  - `settings.a2a.agents` as a global map + per-tenant overrides
  - Tenant-aware config store (database) with dynamic reloading
- Security implications if some agents handle sensitive data.

### Q2 – Per-tenant LLM configuration

- Should each tenant have its own default LLM provider/model?
- How to cleanly pass per-tenant API keys without leaking them between tenants?
- Should there be a dedicated tenant configuration store for LLM settings?

### Q3 – Correlating MCP queries and A2A calls via run_id

- `run_id` is available in `TenantContext` and stored in `SessionData.last_run_id`.
- A2A currently ignores `run_id`.
- Should `run_id` be added as metadata to A2A requests (optionally)?
- How should logs/traces be structured to leverage `run_id`?

### Q4 – SDK coverage and compatibility across third-party A2A agents

- Which `a2a-sdk` version should be pinned, and how do we manage breaking API changes?
- How should we expose agent task capabilities (e.g. a `supports_tasks` hint) so clients can avoid polling for message-only agents?
- What is the minimum subset of A2A features we must support first (tasks/get, streaming, extensions, etc.)?
- How should we gracefully handle partially compliant agents (fallbacks, clearer error reporting, capability checks)?


### Q5 – Error model unification

- Should MCP and A2A errors be normalized into a common error schema?
- Or is it better to pass through more provider-specific errors?
- How much detail should be exposed to visual builders vs hidden in logs?

### Q6 – Long-running tasks and cancellation

- When A2A supports non-blocking mode:
  - How will cancellations be surfaced from client → mcp-bridge → agent?
  - Do we need a `DELETE /a2a/agents/{agent_id}/tasks/{task_id}`?

---

## 5. Guidance for Future Contributors

- Before changing MCP-related behavior:
  - Re-read `PROJECT_CONTEXT.md` and this file.
  - Confirm that you are not re-introducing previously rejected patterns (e.g. A2A-as-tools).

- Before touching A2A integration:
  - Ensure any change moves us closer to **official A2A SDK compliance**, not farther.
  - Keep REST surface stable where possible.

- Before altering multi-tenancy:
  - Revisit D2, D5, D6 and NG4.
  - Avoid mixing authentication with tenant routing.

- If in doubt:
  - Add new items to **Open Questions** instead of making big implicit decisions.
