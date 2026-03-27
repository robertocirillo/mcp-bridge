"""
Facade around mcp-use.

This module remains the single boundary used by the rest of the application,
while the implementation details live in the runtime and guardrails subpackages.
"""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import json
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

from app.core.clients.bias_detector_client import BiasDetectorClient
from app.core.guardrails import wrapper as mcp_wrapper_guardrails
from app.core.runtime import capabilities as mcp_wrapper_capabilities
from app.core.runtime import tools as mcp_wrapper_tools
from app.core.exceptions import (
    ConfigurationError,
    MCPWrapperError,
    QueryOperationElicitationDeclinedError,
)
from app.core.guardrails.runner import GuardrailExecutionContext, GuardrailRunner
from app.core.model_query import (
    ModelQueryInput,
    build_model_query,
    describe_query_input,
    extract_query_text,
    has_query_visual_input,
    replace_query_text,
    sanitize_multimodal_error,
)
from app.core.multimodal_image_fetch import RemoteImageFetchError
from app.core.multimodal_image_resolver import QueryImageResolver
from app.core.audit.mcp_audit import AuditEvent, InMemoryAuditRecorder, utc_now_iso
from app.core.guardrails.policy_engine import ToolInvocationContext, ToolInvocationDecision, ToolPolicy, ToolPolicyEngine
from app.core.runtime.task_runtime import BridgeTaskStatusNotification, install_task_notification_runtime_patch
from app.core.runtime.errors import GuardrailViolationError, MCPToolNotAllowedError
from app.core.guardrails.bias import (
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
from app.core.guardrails.pii import (
    _detect_pii,
    make_pii_after_model_guardrail,
    make_pii_before_model_guardrail,
    redact_pii,
)
from app.core.runtime.llm import create_llm, import_runtime_dependencies, normalize_sandbox_options
from app.core.runtime.transport import _GuardedMCPClient, _GuardedMCPSession
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
        self._base_client = None
        self._llm = None
        self._initialized = False
        self._steps_used = 0
        self._last_server_used = None
        self._active_server_name = None

        # Store correlation identifiers so every decision and audit event can be attributed.
        self.tenant_id: Optional[str] = None
        self.run_id: Optional[str] = None
        self.session_id: Optional[str] = None
        self._elicitation_handler: Optional[Callable[..., Awaitable[Any]]] = None
        self._task_status_handler: Optional[Callable[..., Awaitable[Any]]] = None
        self._query_operation_context: contextvars.ContextVar[Optional[Dict[str, Optional[str]]]] = (
            contextvars.ContextVar("mcp_wrapper_query_operation_context", default=None)
        )
        self._task_operation_contexts: Dict[str, Dict[str, Optional[str]]] = {}
        self._query_image_resolver = QueryImageResolver()

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
        install_task_notification_runtime_patch()
        logger.debug("mcp-use and provider runtime imported with task notification patch")

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

    def set_elicitation_handler(
        self,
        handler: Optional[Callable[..., Awaitable[Any]]],
    ) -> None:
        # Public wrapper contract: callers may set, replace, or clear the bridge elicitation hook.
        # Passing None is allowed and simply disables elicitation wiring for this wrapper instance.
        self._elicitation_handler = handler

    def set_task_status_handler(
        self,
        handler: Optional[Callable[..., Awaitable[Any]]],
    ) -> None:
        self._task_status_handler = handler

    @asynccontextmanager
    async def query_operation_scope(
        self,
        *,
        operation_id: str,
        tenant_id: Optional[str] = None,
        run_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ):
        # Keep operation identity task-local so concurrent query operations do not leak state.
        token = self._query_operation_context.set(
            {
                "operation_id": operation_id,
                "tenant_id": tenant_id or self.tenant_id,
                "run_id": run_id or self.run_id,
                "session_id": session_id or self.session_id,
            }
        )
        try:
            yield
        finally:
            self._query_operation_context.reset(token)

    @staticmethod
    def _normalize_mode(mode: Optional[str], *, default: str, allowed: set[str]) -> str:
        # Normalize user-provided mode values and fall back to a safe default on invalid input.
        return mcp_wrapper_guardrails.normalize_mode(mode, default=default, allowed=allowed)

    def _replace_guardrail(
        self,
        *,
        pipeline_attr: str,
        slot_attr: str,
        new_guardrail: Any,
    ) -> None:
        # Replace the currently installed guardrail instance without disturbing the rest of the pipeline.
        mcp_wrapper_guardrails.replace_guardrail(
            self,
            pipeline_attr=pipeline_attr,
            slot_attr=slot_attr,
            new_guardrail=new_guardrail,
        )

    def set_pii_mode(self, mode: Optional[str]) -> None:
        # Configure how model output and tool results should handle detected PII.
        mcp_wrapper_guardrails.set_pii_mode(self, mode)

    def set_pii_input_mode(self, mode: Optional[str]) -> None:
        # Configure how user input should be handled before the model is invoked.
        mcp_wrapper_guardrails.set_pii_input_mode(self, mode)

    def set_bias_mode(self, mode: Optional[str]) -> None:
        # Configure the local after-model bias guardrail when no external detector is in use.
        mcp_wrapper_guardrails.set_bias_mode(self, mode)

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
        mcp_wrapper_guardrails.set_bias_settings(
            self,
            mode=mode,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            threshold=threshold,
            top_k=top_k,
            active_categories=active_categories,
            unsafe_labels=unsafe_labels,
            model_id=model_id,
            revision=revision,
            return_all_scores=return_all_scores,
            return_char_spans=return_char_spans,
            checks=checks,
            fail_closed=fail_closed,
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
        return mcp_wrapper_guardrails.get_guardrail_runner(self)

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
        return mcp_wrapper_guardrails.wrap_tool_result(
            self,
            tool_name,
            result,
            arguments=arguments,
        )

    async def _run_before_model_guardrails(self, ctx: GuardrailContext) -> GuardrailContext:
        # Execute input guardrails through the shared runner so timeouts and audit behavior stay consistent.
        return await mcp_wrapper_guardrails.run_before_model_guardrails(self, ctx)

    async def _run_after_model_guardrails(self, ctx: GuardrailContext, output: Any) -> Any:
        # Execute output guardrails through the shared runner so blocking and redaction are centralized.
        return await mcp_wrapper_guardrails.run_after_model_guardrails(self, ctx, output)

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

    def _resolve_capability_server_name(self, server_name: Optional[str]) -> str:
        configured_servers = list(self.mcp_servers.keys())
        if server_name:
            if server_name not in self.mcp_servers:
                raise ConfigurationError(f"Server '{server_name}' not configured")
            return server_name

        if not configured_servers:
            raise ConfigurationError("No MCP servers configured for this session")

        if len(configured_servers) == 1:
            return configured_servers[0]

        raise ConfigurationError(
            "server_name is required when multiple MCP servers are configured"
        )

    async def _invoke_optional_client_method(
        self,
        client: Any,
        *,
        method_name: str,
        call_variants: List[tuple[tuple[Any, ...], Dict[str, Any]]],
    ) -> Any:
        return await mcp_wrapper_capabilities.invoke_optional_client_method(
            self,
            client,
            method_name=method_name,
            call_variants=call_variants,
        )

    async def _lookup_capability_session(
        self,
        client: Any,
        *,
        server_name: str,
        allow_missing: bool,
    ) -> Any:
        return await mcp_wrapper_capabilities.lookup_capability_session(
            self,
            client,
            server_name=server_name,
            allow_missing=allow_missing,
        )

    async def _create_capability_session(self, client: Any, *, server_name: str) -> Any:
        return await mcp_wrapper_capabilities.create_capability_session(
            self,
            client,
            server_name=server_name,
        )

    async def _get_capability_session(self, server_name: str) -> Any:
        return await mcp_wrapper_capabilities.get_capability_session(self, server_name)

    async def _invoke_capability_method(
        self,
        session: Any,
        *,
        operation: str,
        method_names: List[str],
        call_variants: List[tuple[tuple[Any, ...], Dict[str, Any]]],
        server_name: str,
    ) -> Any:
        return await mcp_wrapper_capabilities.invoke_capability_method(
            self,
            session,
            operation=operation,
            method_names=method_names,
            call_variants=call_variants,
            server_name=server_name,
        )

    async def _run_capability_operation(
        self,
        *,
        operation: str,
        method_names: List[str],
        call_variants: List[tuple[tuple[Any, ...], Dict[str, Any]]],
        server_name: Optional[str],
    ) -> Any:
        return await mcp_wrapper_capabilities.run_capability_operation(
            self,
            operation=operation,
            method_names=method_names,
            call_variants=call_variants,
            server_name=server_name,
        )

    async def list_prompts(self, server_name: Optional[str] = None) -> Any:
        return await mcp_wrapper_capabilities.list_prompts(
            self,
            server_name=server_name,
        )

    async def get_prompt(
        self,
        prompt_name: str,
        *,
        arguments: Optional[Dict[str, Any]] = None,
        server_name: Optional[str] = None,
    ) -> Any:
        return await mcp_wrapper_capabilities.get_prompt(
            self,
            prompt_name,
            arguments=arguments,
            server_name=server_name,
        )

    async def render_prompt(
        self,
        prompt_name: str,
        *,
        arguments: Optional[Dict[str, Any]] = None,
        server_name: Optional[str] = None,
    ) -> Any:
        return await mcp_wrapper_capabilities.render_prompt(
            self,
            prompt_name,
            arguments=arguments,
            server_name=server_name,
        )

    async def list_resources(self, server_name: Optional[str] = None) -> Any:
        return await mcp_wrapper_capabilities.list_resources(
            self,
            server_name=server_name,
        )

    async def read_resource(
        self,
        uri: str,
        *,
        server_name: Optional[str] = None,
    ) -> Any:
        return await mcp_wrapper_capabilities.read_resource(
            self,
            uri,
            server_name=server_name,
        )

    @staticmethod
    def _coerce_mapping(value: Any) -> Optional[Dict[str, Any]]:
        if value is None:
            return None
        if isinstance(value, dict):
            return value
        if hasattr(value, "model_dump"):
            dumped = value.model_dump(by_alias=True, exclude_none=False)
            if isinstance(dumped, dict):
                return dumped
        if hasattr(value, "dict"):
            dumped = value.dict(exclude_none=False)  # type: ignore[call-arg]
            if isinstance(dumped, dict):
                return dumped
        if hasattr(value, "__dict__"):
            dumped = vars(value)
            if isinstance(dumped, dict):
                return dict(dumped)
        return None

    @classmethod
    def _extract_nested_value(cls, value: Any, *keys: str) -> Any:
        current = value
        for key in keys:
            if current is None:
                return None
            if isinstance(current, dict):
                current = current.get(key)
                continue
            if hasattr(current, key):
                current = getattr(current, key)
                continue
            dumped = cls._coerce_mapping(current)
            if dumped is None:
                return None
            current = dumped.get(key)
        return current

    @staticmethod
    def _truncate_log_value(value: Any, *, limit: int = 2000) -> str:
        try:
            rendered = json.dumps(value, default=str, ensure_ascii=True)
        except Exception:
            rendered = str(value)
        if len(rendered) > limit:
            return f"{rendered[:limit]}...(truncated)"
        return rendered

    @classmethod
    def _validate_task_tool_arguments(
        cls,
        *,
        tool_name: str,
        tool_definition: Any,
        arguments: Dict[str, Any],
    ) -> None:
        return mcp_wrapper_tools.validate_task_tool_arguments(
            cls,
            tool_name=tool_name,
            tool_definition=tool_definition,
            arguments=arguments,
        )

    @classmethod
    def _extract_tool_task_support(cls, tool: Any) -> Optional[str]:
        return mcp_wrapper_tools.extract_tool_task_support(cls, tool)

    @classmethod
    def _extract_task_request_capability(cls, capabilities: Any) -> Optional[bool]:
        return mcp_wrapper_tools.extract_task_request_capability(cls, capabilities)

    def _get_operation_context_snapshot(self) -> Optional[Dict[str, Optional[str]]]:
        operation_context = self._query_operation_context.get()
        if not operation_context:
            return None
        operation_id = operation_context.get("operation_id")
        if not operation_id:
            return None
        return {
            "operation_id": str(operation_id),
            "session_id": str(operation_context.get("session_id") or self.session_id or ""),
            "tenant_id": operation_context.get("tenant_id") or self.tenant_id,
            "run_id": operation_context.get("run_id") or self.run_id,
            "server_name": self._active_server_name,
            "last_elicitation_action": None,
        }

    def _set_task_elicitation_action(self, task_id: Optional[str], action: str) -> None:
        if not task_id:
            return
        task_context = self._task_operation_contexts.get(task_id)
        if task_context is not None:
            task_context["last_elicitation_action"] = action

    @classmethod
    def _extract_related_task_id(cls, context: Any, params: Any) -> Optional[str]:
        for source in (
            cls._extract_nested_value(params, "meta"),
            cls._extract_nested_value(params, "_meta"),
            cls._extract_nested_value(context, "meta"),
            cls._extract_nested_value(context, "_meta"),
            cls._extract_nested_value(context, "request_meta"),
        ):
            if source is None:
                continue
            for related_task_key in (
                "io.modelcontextprotocol/related-task",
                "relatedTask",
                "related-task",
                "related_task",
            ):
                related_task = cls._extract_nested_value(source, related_task_key)
                if related_task is None:
                    continue
                for task_id_key in ("taskId", "task_id", "id"):
                    task_id = cls._extract_nested_value(related_task, task_id_key)
                    if task_id is not None:
                        return str(task_id)
        return None

    def _resolve_elicitation_operation_context(
        self,
        *,
        context: Any,
        params: Any,
    ) -> Optional[Dict[str, Optional[str]]]:
        task_id = self._extract_related_task_id(context, params)
        if task_id is not None:
            task_context = self._task_operation_contexts.get(task_id)
            if task_context is not None:
                return task_context
        return self._get_operation_context_snapshot()

    @classmethod
    def _get_protocol_client_session(cls, session: Any) -> Any:
        return mcp_wrapper_tools.get_protocol_client_session(cls, session)

    @classmethod
    def _get_server_capabilities(cls, session: Any) -> Any:
        return mcp_wrapper_tools.get_server_capabilities(cls, session)

    async def _get_tool_definition(
        self,
        *,
        session: Any,
        server_name: str,
        tool_name: str,
    ) -> Any:
        return await mcp_wrapper_tools.get_tool_definition(
            self,
            session=session,
            server_name=server_name,
            tool_name=tool_name,
        )

    async def _send_raw_mcp_request(
        self,
        *,
        client_session: Any,
        method: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return await mcp_wrapper_tools.send_raw_mcp_request(
            self,
            client_session=client_session,
            method=method,
            params=params,
        )

    def _coerce_call_tool_result(self, result: Dict[str, Any]) -> Any:
        return mcp_wrapper_tools.coerce_call_tool_result(result)

    async def _call_tool_with_task_support(
        self,
        *,
        session: Any,
        tool_definition: Any,
        tool_name: str,
        arguments: Dict[str, Any],
        server_name: str,
    ) -> Any:
        return await mcp_wrapper_tools.call_tool_with_task_support(
            self,
            session=session,
            tool_definition=tool_definition,
            tool_name=tool_name,
            arguments=arguments,
            server_name=server_name,
        )

    async def call_tool(
        self,
        tool_name: str,
        *,
        arguments: Optional[Dict[str, Any]] = None,
        server_name: Optional[str] = None,
    ) -> Any:
        return await mcp_wrapper_tools.call_tool(
            self,
            tool_name,
            arguments=arguments,
            server_name=server_name,
        )

    @staticmethod
    def _serialize_request_context(context: Any) -> Dict[str, Any]:
        if context is None:
            return {}

        serialized: Dict[str, Any] = {}
        for attr in ("request_id", "session_id", "client_id", "server_name"):
            value = getattr(context, attr, None)
            if value is not None:
                serialized[attr] = value
        return serialized

    @staticmethod
    def _extract_requested_schema(params: Any) -> Optional[Dict[str, Any]]:
        schema = getattr(params, "requestedSchema", None)
        if schema is None:
            schema = getattr(params, "requested_schema", None)
        return schema if isinstance(schema, dict) else schema

    @classmethod
    def _extract_task_status_notification(cls, message: Any) -> Optional[Dict[str, Any]]:
        if isinstance(message, BridgeTaskStatusNotification):
            method = message.method
            params = message.params
        else:
            message_root = getattr(message, "root", None)
            if message_root is not None:
                message = message_root
            method = getattr(message, "method", None)
            params = getattr(message, "params", None)
            if method is None and isinstance(message, dict):
                method = message.get("method")
                params = message.get("params")

        if method != "notifications/tasks/status":
            return None

        params_mapping = cls._coerce_mapping(params) or {}
        task_id = cls._extract_nested_value(params_mapping, "taskId")
        if task_id is None:
            task_id = cls._extract_nested_value(params_mapping, "task_id")
        status = cls._extract_nested_value(params_mapping, "status")
        if task_id is None or status is None:
            return None

        return {
            "task_id": str(task_id),
            "status": str(status).strip().lower(),
            "ttl": cls._extract_nested_value(params_mapping, "ttl"),
            "created_at": cls._extract_nested_value(params_mapping, "createdAt"),
            "last_updated_at": cls._extract_nested_value(params_mapping, "lastUpdatedAt"),
            "poll_interval": cls._extract_nested_value(params_mapping, "pollInterval"),
            "status_message": cls._extract_nested_value(params_mapping, "statusMessage"),
        }

    async def _handle_runtime_message(self, message: Any) -> None:
        task_status = self._extract_task_status_notification(message)
        if task_status is None:
            return

        task_id = task_status["task_id"]
        operation_context = self._task_operation_contexts.get(task_id)
        logger.info(
            "mcp_task_status_notification server=%s task_id=%s status=%s correlated=%s payload=%s",
            (operation_context or {}).get("server_name") or self._active_server_name,
            task_id,
            task_status["status"],
            operation_context is not None,
            self._truncate_log_value(task_status),
        )
        if operation_context is None:
            return

        handler = self._task_status_handler
        if handler is None:
            return

        try:
            await handler(
                session_id=str(operation_context["session_id"] or self.session_id or ""),
                operation_id=str(operation_context["operation_id"]),
                payload={
                    **task_status,
                    "server_name": operation_context.get("server_name") or self._active_server_name,
                },
            )
        except Exception:
            logger.exception(
                "Failed to process task status notification for task_id=%s operation_id=%s",
                task_id,
                operation_context.get("operation_id"),
            )

    def _build_runtime_elicitation_result(
        self,
        *,
        action: str = "accept",
        content: Any = None,
    ) -> Any:
        if action != "accept":
            content = None
        try:
            from mcp.types import ElicitResult

            return ElicitResult(action=action, content=content)
        except Exception:
            return SimpleNamespace(action=action, content=content)

    async def _await_runtime_elicitation_content(self, context: Any, params: Any) -> Any:
        handler = self._elicitation_handler
        if handler is None:
            raise MCPWrapperError("Elicitation callback is not configured")

        operation_context = self._resolve_elicitation_operation_context(context=context, params=params)
        if not operation_context or not operation_context.get("operation_id"):
            raise MCPWrapperError(
                "Elicitation requires a stateful query operation; POST /sessions/{id}/query remains unsupported"
            )

        return await handler(
            session_id=str(operation_context["session_id"] or self.session_id or ""),
            operation_id=str(operation_context["operation_id"]),
            payload={
                "message": str(getattr(params, "message", "") or ""),
                "requested_schema": self._extract_requested_schema(params),
                "request_context": self._serialize_request_context(context),
                "server_name": operation_context.get("server_name") or self._active_server_name,
            },
        )

    async def _handle_runtime_elicitation(self, context: Any, params: Any) -> Any:
        content = await self._await_runtime_elicitation_content(context, params)
        return self._build_runtime_elicitation_result(content=content)

    async def _handle_protocol_elicitation(self, context: Any, params: Any) -> Any:
        task_id = self._extract_related_task_id(context, params)
        try:
            content = await self._await_runtime_elicitation_content(context, params)
        except QueryOperationElicitationDeclinedError:
            self._set_task_elicitation_action(task_id, "decline")
            return self._build_runtime_elicitation_result(action="decline")
        except asyncio.CancelledError:
            self._set_task_elicitation_action(task_id, "cancel")
            return self._build_runtime_elicitation_result(action="cancel")

        self._set_task_elicitation_action(task_id, "accept")
        return self._build_runtime_elicitation_result(action="accept", content=content)

    def _build_client_kwargs(
        self,
        *,
        server_names: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        selected_servers = self.mcp_servers
        if server_names is not None:
            selected_servers = {
                name: self.mcp_servers[name]
                for name in server_names
            }

        client_kwargs: Dict[str, Any] = {"config": {"mcpServers": selected_servers}}
        if self._elicitation_handler is not None:
            client_kwargs["elicitation_callback"] = self._handle_protocol_elicitation
        client_kwargs["message_handler"] = self._handle_runtime_message

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

        return client_kwargs

    def _create_runtime_handles(
        self,
        *,
        server_names: Optional[List[str]] = None,
    ) -> tuple[Any, Any, Any]:
        llm = self._llm
        if llm is None:
            llm = self._create_llm()
            self._llm = llm

        base_client = self.MCPClient(**self._build_client_kwargs(server_names=server_names))
        client = _GuardedMCPClient(base_client, self)

        agent_kwargs = {
            "llm": llm,
            "client": client,
            "max_steps": self.max_steps,
            "use_server_manager": self.use_server_manager,
            "verbose": self.verbose,
        }
        if self.disallowed_tools:
            agent_kwargs["disallowed_tools"] = self.disallowed_tools

        agent = self.MCPAgent(**agent_kwargs)
        return base_client, client, agent

    @staticmethod
    def _agent_run_supports_server_name(agent: Any) -> bool:
        run = getattr(agent, "run", None)
        if run is None:
            return False
        try:
            signature = inspect.signature(run)
        except (TypeError, ValueError):
            return False
        return "server_name" in signature.parameters

    async def _close_runtime_handles(self, *, agent: Any, client: Any) -> None:
        client_closed = False
        if agent and hasattr(agent, "close"):
            try:
                maybe_awaitable = agent.close()
                if inspect.isawaitable(maybe_awaitable):
                    await maybe_awaitable
                client_closed = True
            except Exception as exc:
                logger.warning("Error closing MCP agent runtime: %s", exc)

        if client and not client_closed:
            try:
                await client.close_all_sessions()
            except Exception as exc:
                logger.warning("Error closing MCP client runtime: %s", exc)

    @asynccontextmanager
    async def _temporary_query_agent(self, *, server_name: str):
        _, client, agent = self._create_runtime_handles(server_names=[server_name])
        try:
            yield agent
        finally:
            await self._close_runtime_handles(agent=agent, client=client)

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
        self._llm = self._create_llm()
        self._base_client, self._client, self._agent = self._create_runtime_handles()

    async def run_query(
        self,
        query: ModelQueryInput,
        max_steps: Optional[int] = None,
        server_name: Optional[str] = None,
    ) -> str:
        # Lazily initialize the runtime so wrapper construction stays cheap.
        if not self._initialized:
            await self.initialize()

        # Preserve the previous server context because nested or sequential runs may reuse the wrapper.
        previous_active_server_name = getattr(self, "_active_server_name", None)
        query_input = query
        ctx = GuardrailContext(
            tenant_id=self.tenant_id,
            run_id=self.run_id,
            session_id=self.session_id,
            query=extract_query_text(query_input),
            server_name=server_name,
        )

        try:
            # Run input guardrails before the query reaches the model or any MCP tool.
            ctx = await self._run_before_model_guardrails(ctx)
            guarded_query_input = replace_query_text(query_input, text=ctx.query)
            if not (ctx.query or "").strip() and not has_query_visual_input(guarded_query_input):
                raise ValueError("Empty query not allowed")

            # Track the active server so policy and audit logic can attribute tool calls correctly.
            self._active_server_name = server_name
            self._last_server_used = None
            logger.debug("Executing query: %s", describe_query_input(guarded_query_input))

            if server_name and server_name not in self.mcp_servers:
                raise ConfigurationError(f"Server '{server_name}' not configured")

            prepared_query_input = await self._query_image_resolver.resolve(guarded_query_input)

            # Build the agent run payload and optionally scope it to a specific configured server.
            run_kwargs: Dict[str, Any] = {"query": build_model_query(prepared_query_input)}
            if max_steps is not None:
                run_kwargs["max_steps"] = max_steps
            if server_name:
                self._last_server_used = server_name

            agent = self._agent
            if agent is None:
                raise MCPWrapperError("MCP agent not initialized")

            supports_server_name = self._agent_run_supports_server_name(agent)

            async def execute_agent_run(target_agent: Any) -> Any:
                # Keep the actual agent call isolated so retry logic can wrap only the fragile step.
                agent_run_kwargs = dict(run_kwargs)
                if server_name and self._agent_run_supports_server_name(target_agent):
                    agent_run_kwargs["server_name"] = server_name
                return await target_agent.run(**agent_run_kwargs)

            if server_name and len(self.mcp_servers) > 1 and not supports_server_name:
                logger.debug(
                    "Agent runtime does not support server_name; using scoped runtime for server '%s'",
                    server_name,
                )
                async with self._temporary_query_agent(server_name=server_name) as scoped_agent:
                    async def execute_scoped_agent_run() -> Any:
                        return await execute_agent_run(scoped_agent)

                    result = await retry_async(
                        execute_scoped_agent_run,
                        max_retries=2,
                        delay=0.5,
                    )
                    agent = scoped_agent
            else:
                # Retry transient runtime failures without re-running initialization.
                async def execute_default_agent_run() -> Any:
                    return await execute_agent_run(agent)

                result = await retry_async(
                    execute_default_agent_run,
                    max_retries=2,
                    delay=0.5,
                )

            self._steps_used = getattr(agent, "steps_used", 0)

            # Fall back to the agent-reported server when no explicit server was pinned for the query.
            if not self._last_server_used and hasattr(agent, "last_server_used"):
                self._last_server_used = agent.last_server_used

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
        except RemoteImageFetchError as exc:
            logger.warning("Remote image resolution failed: %s", sanitize_multimodal_error(exc))
            raise
        except Exception as exc:
            # Collapse unexpected runtime failures into the public wrapper error type.
            sanitized_error = sanitize_multimodal_error(exc)
            logger.error("Query execution error: %s", sanitized_error)
            raise MCPWrapperError(f"Query execution failed: {sanitized_error}")
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

        agent = self._agent
        client = self._client
        await self._close_runtime_handles(agent=agent, client=client)
        if agent:
            logger.debug("MCP agent closed successfully")
        elif client:
            logger.debug("MCP client closed successfully")

        self._agent = None
        self._client = None
        self._base_client = None
        self._llm = None
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
