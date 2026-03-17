"""
Facade around mcp-use.

This module remains the single boundary used by the rest of the application,
while the implementation details live in mcp_wrapper_* helper modules.
"""

from __future__ import annotations

import os
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

from app.core.bias_detector_client import BiasDetectorClient
from app.core.exceptions import ConfigurationError, MCPWrapperError
from app.core.guardrail_runner import GuardrailExecutionContext, GuardrailRunner
from app.core.mcp_audit import AuditEvent, InMemoryAuditRecorder, utc_now_iso
from app.core.mcp_policy_engine import ToolInvocationContext, ToolInvocationDecision, ToolPolicy, ToolPolicyEngine
from app.core.mcp_wrapper_errors import GuardrailViolationError, MCPToolNotAllowedError
from app.core.mcp_wrapper_guardrails_bias import (
    BiasDetectionResult,
    BiasDetector,
    NoOpBiasDetector,
    RuleBasedBiasDetector,
    _extract_user_visible_answer,
    get_bias_detector,
    initialize_bias_detector_from_env,
    make_bias_after_model_guardrail,
    make_bias_after_model_guardrail_service,
    set_bias_detector,
)
from app.core.mcp_wrapper_guardrails_pii import (
    _detect_pii,
    _detect_pii_in_obj,
    _redact_pii_in_obj,
    make_pii_after_model_guardrail,
    make_pii_before_model_guardrail,
    redact_pii,
)
from app.core.mcp_wrapper_llm import create_llm, import_runtime_dependencies, normalize_sandbox_options
from app.core.mcp_wrapper_transport import _GuardedMCPClient, _GuardedMCPSession
from app.utils.helpers import retry_async
from app.utils.logging import get_logger

logger = get_logger(__name__)

GuardrailContext = GuardrailExecutionContext


class MCPWrapper:
    """Boundary object that encapsulates mcp-use and related runtime policies."""

    def __init__(
        self,
        llm_provider: str,
        model: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        mcp_servers: Optional[Dict[str, Dict[str, Any]]] = None,
        max_steps: int = 30,
        verbose: bool = False,
        sandbox: bool = False,
        sandbox_options: Optional[Any] = None,
        disallowed_tools: Optional[List[str]] = None,
        use_server_manager: bool = False,
    ) -> None:
        # Persist the runtime configuration that defines this session-scoped MCP boundary.
        self.llm_provider = llm_provider.lower()
        self.model = model
        self.api_key = api_key
        self.base_url = base_url or (os.getenv("OLLAMA_BASE_URL") if llm_provider == "ollama" else None)
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.mcp_servers = mcp_servers or {}
        self.has_mcp_servers = bool(self.mcp_servers)
        self.max_steps = max_steps
        self.verbose = verbose
        self.sandbox = sandbox
        self.sandbox_options = normalize_sandbox_options(sandbox_options)
        self.disallowed_tools = disallowed_tools
        self.use_server_manager = use_server_manager

        # Initialize the collaborators that enforce policy, guardrails, and audit recording.
        self.tool_policy_engine = ToolPolicyEngine(deny_patterns=self.disallowed_tools or [])
        self.audit_recorder = InMemoryAuditRecorder()
        self.guardrail_runner = GuardrailRunner(
            audit_recorder=self.audit_recorder,
            violation_error_cls=GuardrailViolationError,
        )

        # Keep runtime handles and execution state separate from immutable configuration.
        self._agent = None
        self._client = None
        self._initialized = False
        self._steps_used = 0
        self._last_server_used = None
        self._active_server_name = None

        # Store correlation identifiers so every decision and audit event can be attributed.
        self.tenant_id: Optional[str] = None
        self.run_id: Optional[str] = None
        self.session_id: Optional[str] = None

        # Maintain independent guardrail pipelines for input and output phases.
        self.before_model_guardrails: List[Callable[[GuardrailContext], Union[GuardrailContext, Awaitable[GuardrailContext]]]] = []
        self.after_model_guardrails: List[Callable[[GuardrailContext, Any], Union[Any, Awaitable[Any]]]] = []

        # Keep global toggles and shared resources for runtime guardrail execution.
        self.guardrails_enabled = True
        self.guardrail_timeout_seconds: Optional[float] = None
        self._bias_detector_service: Optional[BiasDetectorClient] = None

        # Start with safe defaults and materialize the matching guardrail functions below.
        self.pii_mode = "redact"
        self.pii_input_mode = "block"
        self.bias_mode = "off"

        self._pii_after_model_guardrail = None
        self._pii_before_model_guardrail = None
        self._bias_after_model_guardrail = None

        # Validate configuration, import optional dependencies, and install default guardrails eagerly.
        self._validate_config()
        self._import_dependencies()
        self.set_pii_mode(self.pii_mode)
        self.set_pii_input_mode(self.pii_input_mode)
        self.set_bias_mode(self.bias_mode)

    def _validate_config(self) -> None:
        # Fail fast on invalid wrapper configuration before any runtime resources are created.
        if not self.llm_provider:
            raise ConfigurationError("LLM provider not specified")
        if not self.model:
            raise ConfigurationError("Model not specified")

        if not self.has_mcp_servers:
            return

        # Require each server entry to define at least one transport mechanism.
        for name, config in self.mcp_servers.items():
            if not config.get("command") and not config.get("url"):
                raise ConfigurationError(f"Server {name}: must have 'command' or 'url'")

    def _import_dependencies(self) -> None:
        # Resolve the mcp-use runtime classes and provider-specific LLM implementation lazily.
        runtime = import_runtime_dependencies(self.llm_provider)
        self.MCPAgent = runtime.MCPAgent
        self.MCPClient = runtime.MCPClient
        self.SandboxOptions = runtime.SandboxOptions
        self.ChatLLM = runtime.ChatLLM
        logger.debug("mcp-use and provider runtime imported")

    def _create_llm(self) -> Any:
        # Build the configured chat model through the dedicated helper so provider differences stay isolated.
        llm = create_llm(
            self.ChatLLM,
            llm_provider=self.llm_provider,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            api_key=self.api_key,
            base_url=self.base_url,
        )
        logger.debug("LLM %s/%s successfully created", self.llm_provider, self.model)
        return llm

    def set_context(
        self,
        *,
        tenant_id: Optional[str] = None,
        run_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> None:
        # Update the correlation context used by policy decisions, guardrails, and audit events.
        self.tenant_id = tenant_id
        self.run_id = run_id
        self.session_id = session_id

    @staticmethod
    def _normalize_mode(mode: Optional[str], *, default: str, allowed: set[str]) -> str:
        # Normalize user-provided mode values and fall back to a safe default on invalid input.
        normalized = (mode or default).strip().lower()
        return normalized if normalized in allowed else default

    def _replace_guardrail(
        self,
        *,
        pipeline_attr: str,
        slot_attr: str,
        new_guardrail: Any,
    ) -> None:
        # Replace the currently installed guardrail instance without disturbing the rest of the pipeline.
        pipeline = list(getattr(self, pipeline_attr, []) or [])
        current = getattr(self, slot_attr, None)
        replaced = False

        if current is not None:
            for idx, guardrail in enumerate(pipeline):
                if guardrail is current:
                    replaced = True
                    if new_guardrail is None:
                        del pipeline[idx]
                    else:
                        pipeline[idx] = new_guardrail
                    break
            if not replaced:
                pipeline = [guardrail for guardrail in pipeline if guardrail is not current]

        if not replaced and new_guardrail is not None:
            pipeline.append(new_guardrail)

        setattr(self, pipeline_attr, pipeline)
        setattr(self, slot_attr, new_guardrail)

    def set_pii_mode(self, mode: Optional[str]) -> None:
        # Configure how model output and tool results should handle detected PII.
        normalized = self._normalize_mode(mode, default="redact", allowed={"off", "redact", "block"})
        self.pii_mode = normalized
        new_guardrail = None if normalized == "off" else make_pii_after_model_guardrail(mode=normalized)
        self._replace_guardrail(
            pipeline_attr="after_model_guardrails",
            slot_attr="_pii_after_model_guardrail",
            new_guardrail=new_guardrail,
        )

    def set_pii_input_mode(self, mode: Optional[str]) -> None:
        # Configure how user input should be handled before the model is invoked.
        normalized = self._normalize_mode(mode, default="block", allowed={"off", "redact", "block"})
        self.pii_input_mode = normalized
        new_guardrail = None if normalized == "off" else make_pii_before_model_guardrail(mode=normalized)
        self._replace_guardrail(
            pipeline_attr="before_model_guardrails",
            slot_attr="_pii_before_model_guardrail",
            new_guardrail=new_guardrail,
        )

    def set_bias_mode(self, mode: Optional[str]) -> None:
        # Configure the local after-model bias guardrail when no external detector is in use.
        normalized = self._normalize_mode(mode, default="off", allowed={"off", "block"})
        self.bias_mode = normalized
        new_guardrail = None if normalized == "off" else make_bias_after_model_guardrail(mode=normalized)
        self._replace_guardrail(
            pipeline_attr="after_model_guardrails",
            slot_attr="_bias_after_model_guardrail",
            new_guardrail=new_guardrail,
        )

    def set_bias_settings(
        self,
        *,
        mode: Optional[str],
        base_url: Optional[str] = None,
        timeout_seconds: float = 5.0,
        threshold: float = 0.5,
        top_k: int = 5,
        active_categories: Optional[List[str]] = None,
        unsafe_labels: Optional[List[str]] = None,
        model_id: Optional[str] = None,
        revision: Optional[str] = None,
        return_all_scores: bool = False,
        return_char_spans: bool = False,
        checks: Optional[List[Any]] = None,
        fail_closed: bool = True,
    ) -> None:
        # Configure either local or service-backed bias checks and install the resulting guardrail.
        normalized = self._normalize_mode(mode, default="off", allowed={"off", "block"})
        self.bias_mode = normalized

        # Remove the bias guardrail entirely when the feature is disabled.
        if normalized == "off":
            self._replace_guardrail(
                pipeline_attr="after_model_guardrails",
                slot_attr="_bias_after_model_guardrail",
                new_guardrail=None,
            )
            return

        # Fall back to the local rule-based guardrail when no detector service is configured.
        if not base_url:
            self.set_bias_mode(normalized)
            return

        try:
            # Build the HTTP client once and reuse it across queries for the current wrapper instance.
            self._bias_detector_service = BiasDetectorClient(
                base_url=base_url,
                timeout_seconds=float(timeout_seconds),
            )
        except Exception as exc:
            raise ConfigurationError(f"Invalid bias-detector-service configuration: {exc}")

        # Install the service-backed after-model guardrail with the configured thresholds and checks.
        new_guardrail = make_bias_after_model_guardrail_service(
            client=self._bias_detector_service,
            mode=normalized,
            threshold=float(threshold),
            top_k=int(top_k),
            active_categories=active_categories,
            unsafe_labels=unsafe_labels,
            model_id=model_id,
            revision=revision,
            return_all_scores=bool(return_all_scores),
            return_char_spans=bool(return_char_spans),
            checks=checks,
            fail_closed=bool(fail_closed),
        )
        self._replace_guardrail(
            pipeline_attr="after_model_guardrails",
            slot_attr="_bias_after_model_guardrail",
            new_guardrail=new_guardrail,
        )

    def set_tool_policy_engine(self, engine: ToolPolicyEngine) -> None:
        # Allow callers to swap in a fully prepared policy engine.
        self.tool_policy_engine = engine

    def configure_tool_policies(
        self,
        *,
        allow_patterns: Optional[List[str]] = None,
        deny_patterns: Optional[List[str]] = None,
        policies: Optional[List[ToolPolicy]] = None,
    ) -> None:
        # Rebuild the policy engine from declarative allow/deny patterns and optional rich policies.
        self.tool_policy_engine = ToolPolicyEngine(
            allow_patterns=allow_patterns,
            deny_patterns=deny_patterns if deny_patterns is not None else (self.disallowed_tools or []),
            policies=policies,
        )

    def get_audit_events(self) -> List[AuditEvent]:
        # Expose a snapshot of the in-memory audit trail collected for this wrapper.
        return self.audit_recorder.list_events()

    def _record_audit_event(
        self,
        *,
        event_type: str,
        outcome: str,
        tool_name: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        # Lazily recreate the recorder if tests or callers replaced internal state unexpectedly.
        recorder = getattr(self, "audit_recorder", None)
        if recorder is None:
            recorder = InMemoryAuditRecorder()
            self.audit_recorder = recorder

        try:
            # Persist a structured event so external layers can inspect runtime decisions after execution.
            recorder.record(
                AuditEvent(
                    event_type=event_type,
                    timestamp=utc_now_iso(),
                    tenant_id=self.tenant_id,
                    run_id=self.run_id,
                    session_id=self.session_id,
                    tool_name=tool_name,
                    outcome=outcome,
                    details=details or {},
                )
            )
        except Exception:
            # Audit failures must never break the main query path.
            logger.debug("Failed to record audit event", exc_info=True)

    def set_guardrails_enabled(self, enabled: bool) -> None:
        # Toggle all guardrail execution without changing the configured pipelines.
        self.guardrails_enabled = bool(enabled)

    def _get_guardrail_runner(self) -> GuardrailRunner:
        # Keep the runner bound to the current recorder so guardrail events remain observable.
        runner = getattr(self, "guardrail_runner", None)
        recorder = getattr(self, "audit_recorder", None)
        if recorder is None:
            recorder = InMemoryAuditRecorder()
            self.audit_recorder = recorder

        if runner is None or getattr(runner, "audit_recorder", None) is not recorder:
            # Rebuild the runner when internal state was reset or a new recorder was injected.
            runner = GuardrailRunner(
                audit_recorder=recorder,
                violation_error_cls=GuardrailViolationError,
            )
            self.guardrail_runner = runner
        return runner

    def _extract_tool_arguments(self, args: Any, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        # Normalize positional and keyword tool call arguments into a single audit-friendly mapping.
        if kwargs:
            return dict(kwargs)
        if not args:
            return {}
        if len(args) == 1 and isinstance(args[0], dict):
            return dict(args[0])
        return {"args": list(args)}

    def _wrap_tool_result(
        self,
        tool_name: str,
        result: Any,
        *,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> Any:
        # Run per-tool-result guardrails through the shared runner before results are returned to the agent.
        ctx = GuardrailContext(
            tenant_id=self.tenant_id,
            run_id=self.run_id,
            session_id=self.session_id,
            server_name=getattr(self, "_active_server_name", None),
            tool_name=tool_name,
            arguments=arguments or {},
        )
        outcome = self._get_guardrail_runner().tool_result(
            ctx,
            result,
            enabled=getattr(self, "guardrails_enabled", True),
            pii_mode=getattr(self, "pii_mode", "redact"),
            redact_result=_redact_pii_in_obj,
            detect_result_pii=_detect_pii_in_obj,
        )
        return outcome.value

    async def _run_before_model_guardrails(self, ctx: GuardrailContext) -> GuardrailContext:
        # Execute input guardrails through the shared runner so timeouts and audit behavior stay consistent.
        outcome = await self._get_guardrail_runner().before_model(
            ctx,
            getattr(self, "before_model_guardrails", []),
            enabled=getattr(self, "guardrails_enabled", True),
            timeout_seconds=getattr(self, "guardrail_timeout_seconds", None),
        )
        return outcome.value

    async def _run_after_model_guardrails(self, ctx: GuardrailContext, output: Any) -> Any:
        # Execute output guardrails through the shared runner so blocking and redaction are centralized.
        outcome = await self._get_guardrail_runner().after_model(
            ctx,
            output,
            getattr(self, "after_model_guardrails", []),
            enabled=getattr(self, "guardrails_enabled", True),
            timeout_seconds=getattr(self, "guardrail_timeout_seconds", None),
        )
        return outcome.value

    def _evaluate_tool_invocation_policy(
        self,
        tool_name: str,
        *,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> ToolInvocationDecision:
        # Evaluate tool access against the current policy engine with full invocation context.
        engine = getattr(self, "tool_policy_engine", None)
        if engine is None:
            engine = ToolPolicyEngine(deny_patterns=getattr(self, "disallowed_tools", None) or [])
            self.tool_policy_engine = engine

        # Include correlation and server metadata so policies can evolve without changing call sites.
        ctx = ToolInvocationContext(
            tool_name=tool_name,
            arguments=arguments or {},
            tenant_id=self.tenant_id,
            run_id=self.run_id,
            session_id=self.session_id,
            server_name=getattr(self, "_active_server_name", None),
        )
        return engine.evaluate(ctx)

    def _enforce_tool_allowed(self, tool_name: str, *args: Any, **kwargs: Any) -> None:
        # Resolve arguments, evaluate policy, and emit both logs and audit events for every tool decision.
        arguments = self._extract_tool_arguments(args, kwargs)
        decision = self._evaluate_tool_invocation_policy(tool_name, arguments=arguments)
        server_name = getattr(self, "_active_server_name", None)

        logger.info(
            "mcp_tool_policy_decision",
            extra={
                "tenant_id": self.tenant_id,
                "run_id": self.run_id,
                "session_id": self.session_id,
                "tool_name": tool_name,
                "allowed": decision.allowed,
                "reason": decision.reason,
                "risk_class": decision.risk_class,
            },
        )
        self._record_audit_event(
            event_type="tool_policy_decision",
            outcome="allowed" if decision.allowed else "blocked",
            tool_name=tool_name,
            details={
                "reason": decision.reason,
                "risk_class": decision.risk_class,
                "validation_errors": list(decision.validation_errors),
                "arguments_present": bool(arguments),
                "matched_policy": getattr(decision.matched_policy, "pattern", None),
                "server_name": server_name,
            },
        )

        if not decision.allowed:
            # Raise a structured boundary error so callers can surface a stable blocked-tool response.
            raise MCPToolNotAllowedError(
                tool_name,
                tenant_id=self.tenant_id,
                run_id=self.run_id,
                session_id=self.session_id,
                reason=decision.reason,
            )

    async def initialize(self) -> None:
        # Initialize runtime resources once and retry transient setup failures.
        if self._initialized:
            logger.debug("MCPWrapper already initialized")
            return

        try:
            # Delegate the concrete setup to the internal initializer so retry logic stays narrow.
            await retry_async(self._initialize_internal, max_retries=3, delay=1.0)
            self._initialized = True
            logger.info("MCPWrapper successfully initialized")
        except Exception as exc:
            logger.error("Initialization error after all attempts: %s", exc)
            raise MCPWrapperError(f"Initialization failed: {exc}")

    async def _initialize_internal(self) -> None:
        # Create the LLM first because both agent-only and MCP-backed modes depend on it.
        llm = self._create_llm()
        client_kwargs: Dict[str, Any] = {"config": {"mcpServers": self.mcp_servers}}

        # Forward sandbox settings only when sandbox execution was explicitly enabled.
        if self.sandbox:
            client_kwargs["sandbox"] = True
            if self.sandbox_options:
                client_kwargs["sandbox_options"] = {
                    "api_key": self.sandbox_options.get("api_key", os.getenv("E2B_API_KEY")),
                    "sandbox_template_id": self.sandbox_options.get("sandbox_template_id", "base"),
                    "supergateway_command": self.sandbox_options.get(
                        "supergateway_command",
                        "npx -y supergateway",
                    ),
                }

        # Wrap the raw MCP client so every tool call flows through wrapper policy and guardrail hooks.
        base_client = self.MCPClient(**client_kwargs)
        self._client = _GuardedMCPClient(base_client, self)

        # Build the agent on top of the guarded client while preserving existing mcp-use options.
        agent_kwargs = {
            "llm": llm,
            "client": self._client,
            "max_steps": self.max_steps,
            "use_server_manager": self.use_server_manager,
            "verbose": self.verbose,
        }
        if self.disallowed_tools:
            agent_kwargs["disallowed_tools"] = self.disallowed_tools

        self._agent = self.MCPAgent(**agent_kwargs)

    async def run_query(
        self,
        query: str,
        max_steps: Optional[int] = None,
        server_name: Optional[str] = None,
    ) -> str:
        # Lazily initialize the runtime so wrapper construction stays cheap.
        if not self._initialized:
            await self.initialize()

        # Preserve the previous server context because nested or sequential runs may reuse the wrapper.
        previous_active_server_name = getattr(self, "_active_server_name", None)
        ctx = GuardrailContext(
            tenant_id=self.tenant_id,
            run_id=self.run_id,
            session_id=self.session_id,
            query=query,
            server_name=server_name,
        )

        try:
            # Run input guardrails before the query reaches the model or any MCP tool.
            ctx = await self._run_before_model_guardrails(ctx)
            query = ctx.query or ""
            if not query.strip():
                raise ValueError("Empty query not allowed")

            # Track the active server so policy and audit logic can attribute tool calls correctly.
            self._active_server_name = server_name
            logger.debug("Executing query: %s...", query[:100])

            # Build the agent run payload and optionally scope it to a specific configured server.
            run_kwargs: Dict[str, Any] = {"query": query}
            if max_steps is not None:
                run_kwargs["max_steps"] = max_steps
            if server_name:
                if server_name not in self.mcp_servers:
                    raise ConfigurationError(f"Server '{server_name}' not configured")
                run_kwargs["server_name"] = server_name
                self._last_server_used = server_name

            async def execute_agent_run() -> Any:
                # Keep the actual agent call isolated so retry logic can wrap only the fragile step.
                return await self._agent.run(**run_kwargs)

            # Retry transient runtime failures without re-running initialization.
            result = await retry_async(execute_agent_run, max_retries=2, delay=0.5)
            self._steps_used = getattr(self._agent, "steps_used", 0)

            # Fall back to the agent-reported server when no explicit server was pinned for the query.
            if not self._last_server_used and hasattr(self._agent, "last_server_used"):
                self._last_server_used = self._agent.last_server_used

            # Apply output guardrails and strip internal traces before returning the final answer.
            output = await self._run_after_model_guardrails(ctx, str(result))
            output = _extract_user_visible_answer(output)

            effective_server_name = server_name or self._last_server_used
            # Record successful query execution with the effective runtime metadata for observability.
            self._record_audit_event(
                event_type="query_execution",
                outcome="completed",
                details={
                    "max_steps": max_steps if max_steps is not None else self.max_steps,
                    "server_name": effective_server_name,
                    "steps_used": self._steps_used,
                },
            )
            return output

        except MCPToolNotAllowedError:
            # Tool-policy blocks are recorded explicitly so they can be distinguished from other failures.
            self._record_audit_event(
                event_type="query_execution",
                outcome="blocked",
                details={"reason": "tool_policy", "server_name": server_name},
            )
            raise
        except GuardrailViolationError:
            # Guardrail blocks are recorded explicitly so they can be distinguished from policy blocks.
            self._record_audit_event(
                event_type="query_execution",
                outcome="blocked",
                details={"reason": "guardrail", "server_name": server_name},
            )
            raise
        except Exception as exc:
            # Collapse unexpected runtime failures into the public wrapper error type.
            logger.error("Query execution error: %s", exc)
            raise MCPWrapperError(f"Query execution failed: {exc}")
        finally:
            # Always restore the previous server context to avoid leaking state across calls.
            self._active_server_name = previous_active_server_name

    async def close(self) -> None:
        # Tear down auxiliary services before closing the MCP client itself.
        if getattr(self, "_bias_detector_service", None) is not None:
            try:
                await self._bias_detector_service.close()
            except Exception:
                pass
            self._bias_detector_service = None

        # Close all managed MCP sessions when the guarded client was initialized.
        if self._client:
            try:
                await self._client.close_all_sessions()
                logger.debug("MCP client closed successfully")
            except Exception as exc:
                logger.warning("Error closing MCP client: %s", exc)

        self._agent = None
        self._client = None
        self._initialized = False
        logger.debug("MCPWrapper closed")

    @property
    def steps_used(self) -> int:
        # Expose the number of steps consumed by the last agent run.
        return self._steps_used

    @property
    def last_server_used(self) -> Optional[str]:
        # Expose the last explicit or discovered server used during query execution.
        return self._last_server_used

    @property
    def is_initialized(self) -> bool:
        # Expose whether runtime resources were already created.
        return self._initialized

    def get_config_summary(self) -> Dict[str, Any]:
        # Return a lightweight snapshot that is safe to surface in diagnostics.
        return {
            "llm_provider": self.llm_provider,
            "model": self.model,
            "max_steps": self.max_steps,
            "sandbox": self.sandbox,
            "servers": list(self.mcp_servers.keys()),
            "use_server_manager": self.use_server_manager,
            "initialized": self._initialized,
        }

    async def test_connection(self) -> Dict[str, bool]:
        # Probe each configured server through the normal query path so policy and guardrails still apply.
        if not self._initialized:
            await self.initialize()

        results: Dict[str, bool] = {}
        for server_name in self.mcp_servers.keys():
            try:
                # Use a minimal query to verify that the target server can be reached end-to-end.
                await self.run_query("ping", max_steps=1, server_name=server_name)
                results[server_name] = True
            except Exception as exc:
                logger.warning("Connection test failed for %s: %s", server_name, exc)
                results[server_name] = False
        return results


__all__ = [
    "BiasDetectionResult",
    "BiasDetector",
    "NoOpBiasDetector",
    "RuleBasedBiasDetector",
    "MCPToolNotAllowedError",
    "GuardrailViolationError",
    "GuardrailContext",
    "MCPWrapper",
    "_GuardedMCPClient",
    "_GuardedMCPSession",
    "_detect_pii",
    "_extract_user_visible_answer",
    "get_bias_detector",
    "initialize_bias_detector_from_env",
    "make_bias_after_model_guardrail",
    "make_bias_after_model_guardrail_service",
    "make_pii_after_model_guardrail",
    "make_pii_before_model_guardrail",
    "redact_pii",
    "set_bias_detector",
]
