"""
Refined wrapper for mcp-use with enhanced error handling
"""

import os
import re
import asyncio
from typing import Optional, Dict, Any, List, Callable, Awaitable, Union
from dataclasses import dataclass
from fnmatch import fnmatchcase
import inspect

from app.core.exceptions import MCPWrapperError, DependencyError, ConfigurationError
from app.utils.logging import get_logger
from app.utils.helpers import retry_async
from app.models.config import SandboxOptions as SandboxOptionsModel  # rinomina per non confonderla con quella di mcp-use

logger = get_logger(__name__)


# -----------------------------
# Local MVP guardrails
# -----------------------------

# NOTE: Deterministic, dependency-free patterns.
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)

# Phone: generic international-ish pattern with a minimum amount of digits.
_PHONE_RE = re.compile(r"\b(?:\+?\d[\d\s().-]{6,}\d)\b")

# IBAN: 15..34 chars, starts with 2 letters + 2 digits.
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b", re.IGNORECASE)


def _detect_pii(text: str) -> Dict[str, int]:
    """Return detected PII types with counts (email/phone/iban)."""
    return {
        "email": len(_EMAIL_RE.findall(text)),
        "phone": len(_PHONE_RE.findall(text)),
        "iban": len(_IBAN_RE.findall(text)),
    }


def redact_pii(text: str) -> str:
    """Redact email/phone/iban occurrences from text."""
    # Order matters to avoid redacting inside placeholders.
    text = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    text = _IBAN_RE.sub("[REDACTED_IBAN]", text)
    text = _PHONE_RE.sub("[REDACTED_PHONE]", text)
    return text


def make_pii_after_model_guardrail(*, mode: str = "redact"):
    """Factory for an after_model guardrail that detects/redacts PII.

    Modes:
      - redact (default): return redacted output
      - block: raise GuardrailViolationError(code=PII_DETECTED)
    """

    normalized_mode = (mode or "redact").strip().lower()
    if normalized_mode not in {"redact", "block"}:
        # Keep deterministic behavior; treat unknown modes as redact.
        normalized_mode = "redact"

    async def _guardrail(ctx: "GuardrailContext", output: Any) -> Any:
        # Only operate on text outputs for the MVP.
        if output is None:
            return output
        text = str(output)
        counts = _detect_pii(text)
        present = [k for k, v in counts.items() if v > 0]

        if not present:
            return output

        if normalized_mode == "block":
            raise GuardrailViolationError(
                code="PII_DETECTED",
                message="PII detected in model output",
                phase="after_model",
                rule="pii",
                tenant_id=getattr(ctx, "tenant_id", None),
                run_id=getattr(ctx, "run_id", None),
                session_id=getattr(ctx, "session_id", None),
                details={
                    "types": present,
                    "counts": counts,
                    "mode": "block",
                },
            )

        # Default: redact.
        return redact_pii(text)

    return _guardrail


class MCPToolNotAllowedError(Exception):
    """Raised when a tool call is blocked by session policy."""

    def __init__(
        self,
        tool_name: str,
        *,
        tenant_id: Optional[str] = None,
        run_id: Optional[str] = None,
        session_id: Optional[str] = None,
        reason: str = "blocked by session policy",
    ):
        self.tool_name = tool_name
        self.tenant_id = tenant_id
        self.run_id = run_id
        self.session_id = session_id
        self.reason = reason
        super().__init__(f"Tool '{tool_name}' not allowed: {reason}")


class GuardrailViolationError(Exception):
    """Raised when a guardrail blocks the request or output."""

    def __init__(
        self,
        *,
        code: str = "GUARDRAIL_VIOLATION",
        message: str,
        phase: str,
        rule: Optional[str] = None,
        tenant_id: Optional[str] = None,
        run_id: Optional[str] = None,
        session_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        self.code = code
        self.message = message
        self.phase = phase  # "before_model" | "after_model"
        self.rule = rule
        self.tenant_id = tenant_id
        self.run_id = run_id
        self.session_id = session_id
        self.details = details or {}
        super().__init__(message)


@dataclass(frozen=True)
class GuardrailContext:
    """Context passed to guardrail hooks."""

    tenant_id: Optional[str] = None
    run_id: Optional[str] = None
    session_id: Optional[str] = None
    query: Optional[str] = None
    server_name: Optional[str] = None


def _matches_any(patterns: List[str], value: str) -> bool:
    for pat in patterns:
        if fnmatchcase(value, pat):
            return True
    return False


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


class _GuardedMCPSession:
    """Proxy session that enforces tool policy before calling call_tool()."""

    def __init__(self, session: Any, wrapper: "MCPWrapper"):
        self._session = session
        self._wrapper = wrapper

    async def call_tool(self, name: str, *args: Any, **kwargs: Any) -> Any:
        self._wrapper._enforce_tool_allowed(name)
        return await self._session.call_tool(name, *args, **kwargs)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._session, item)


class _GuardedMCPClient:
    """Proxy client that wraps sessions and enforces tool policy."""

    def __init__(self, client: Any, wrapper: "MCPWrapper"):
        self._client = client
        self._wrapper = wrapper

    async def get_session(self, *args: Any, **kwargs: Any) -> Any:
        session = await self._client.get_session(*args, **kwargs)
        return _GuardedMCPSession(session, self._wrapper)

    async def call_tool(self, name: str, *args: Any, **kwargs: Any) -> Any:
        self._wrapper._enforce_tool_allowed(name)
        return await self._client.call_tool(name, *args, **kwargs)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._client, item)

# Mapping provider -> LangChain class path
PROVIDER_IMPORTS = {
    "openai": ("langchain_openai", "ChatOpenAI"),
    "anthropic": ("langchain_anthropic", "ChatAnthropic"),
    "ollama": ("langchain_ollama", "ChatOllama"),
}


class MCPWrapper:
    """Enhanced wrapper for mcp-use that fully encapsulates the library"""

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
        sandbox_options: Optional[Any] = None,  # può essere dict o Pydantic model
        disallowed_tools: Optional[List[str]] = None,
        use_server_manager: bool = False,
    ):
        """
        Initializes the MCP wrapper

        Args:
            llm_provider: Model provider (openai, anthropic, ollama)
            model: Model name
            api_key: API key (optional if set in environment)
            base_url: Base URL for custom providers
            temperature: Model temperature
            max_tokens: Maximum number of tokens
            mcp_servers: MCP servers configuration
            max_steps: Maximum steps for the agent
            verbose: Verbose mode for debugging
            sandbox: Use the E2B sandbox environment
            sandbox_options: Options for the sandbox
            disallowed_tools: Tools not allowed
            use_server_manager: Use server manager for automatic selection
        """
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
        self.sandbox_options = self._normalize_sandbox_options(sandbox_options)
        self.disallowed_tools = disallowed_tools
        self.use_server_manager = use_server_manager

        # Internal state
        self._agent = None
        self._client = None
        self._initialized = False
        self._steps_used = 0
        self._last_server_used = None

        # Request/session context (for logs + structured errors)
        self.tenant_id: Optional[str] = None
        self.run_id: Optional[str] = None
        self.session_id: Optional[str] = None

        # Guardrail pipelines (LangChain-inspired hooks)
        # Each callable can be sync or async.
        # - before_model: fn(ctx) -> ctx
        # - after_model: fn(ctx, output) -> output
        self.before_model_guardrails: List[Callable[[GuardrailContext], Union[GuardrailContext, Awaitable[GuardrailContext]]]] = []
        self.after_model_guardrails: List[Callable[[GuardrailContext, Any], Union[Any, Awaitable[Any]]]] = []

        # Optional per-guardrail timeout to avoid hanging requests.
        # When set, a timeout raises GuardrailViolationError(code=MCP_GUARDRAIL_TIMEOUT).
        self.guardrail_timeout_seconds: Optional[float] = None

        # Local MVP guardrails (LangChain "after" pattern): redact PII by default.
        self.after_model_guardrails.append(make_pii_after_model_guardrail(mode="redact"))

        # Validate and import dependencies
        self._validate_config()
        self._import_dependencies()

    @staticmethod
    def _normalize_sandbox_options(sandbox_options: Optional[Any]) -> Dict[str, Any]:
        """Normalizes sandbox options to a dictionary compatible with mcp-use
           Accepts:
           - my models.config.SandboxOptions (Pydantic model)
           - dict
           - None
        """
        if sandbox_options is None:
            return {}

        # Pydantic v2
        if hasattr(sandbox_options, "model_dump"):
            return sandbox_options.model_dump(exclude_none=True)

        # Pydantic v1 (per sicurezza)
        if hasattr(sandbox_options, "dict"):
            return sandbox_options.dict(exclude_none=True)  # type: ignore[call-arg]

        # Già un dict
        if isinstance(sandbox_options, dict):
            return sandbox_options

        # Fallback generico per oggetti con attributi
        try:
            return {
                "api_key": getattr(sandbox_options, "api_key", None),
                "sandbox_template_id": getattr(sandbox_options, "sandbox_template_id", "base"),
                "supergateway_command": getattr(
                    sandbox_options,
                    "supergateway_command",
                    "npx -y supergateway",
                ),
            }
        except Exception:
            raise ConfigurationError(
                f"Unsupported sandbox_options type: {type(sandbox_options)!r}. "
                "Expected dict or Pydantic BaseModel."
            )

    def set_context(
        self,
        *,
        tenant_id: Optional[str] = None,
        run_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> None:
        """Set request/session context for logging and structured errors."""
        self.tenant_id = tenant_id
        self.run_id = run_id
        self.session_id = session_id

    async def _run_before_model_guardrails(self, ctx: GuardrailContext) -> GuardrailContext:
        timeout = getattr(self, "guardrail_timeout_seconds", None)
        for gr in self.before_model_guardrails:
            try:
                if timeout:
                    ctx = await asyncio.wait_for(_maybe_await(gr(ctx)), timeout=timeout)
                else:
                    ctx = await _maybe_await(gr(ctx))
            except asyncio.TimeoutError:
                raise GuardrailViolationError(
                    code="MCP_GUARDRAIL_TIMEOUT",
                    message="Guardrail timed out",
                    phase="before_model",
                    rule=getattr(gr, "__name__", "guardrail"),
                    tenant_id=getattr(ctx, "tenant_id", None),
                    run_id=getattr(ctx, "run_id", None),
                    session_id=getattr(ctx, "session_id", None),
                    details={"timeout_seconds": timeout},
                )
        return ctx

    async def _run_after_model_guardrails(self, ctx: GuardrailContext, output: Any) -> Any:
        timeout = getattr(self, "guardrail_timeout_seconds", None)
        for gr in self.after_model_guardrails:
            try:
                if timeout:
                    output = await asyncio.wait_for(
                        _maybe_await(gr(ctx, output)),
                        timeout=timeout,
                    )
                else:
                    output = await _maybe_await(gr(ctx, output))
            except asyncio.TimeoutError:
                raise GuardrailViolationError(
                    code="MCP_GUARDRAIL_TIMEOUT",
                    message="Guardrail timed out",
                    phase="after_model",
                    rule=getattr(gr, "__name__", "guardrail"),
                    tenant_id=getattr(ctx, "tenant_id", None),
                    run_id=getattr(ctx, "run_id", None),
                    session_id=getattr(ctx, "session_id", None),
                    details={"timeout_seconds": timeout},
                )
        return output

    def _enforce_tool_allowed(self, tool_name: str) -> None:
        """Last-gate enforcement before any MCP tool call."""
        if not self.disallowed_tools:
            return
        denied = _matches_any(self.disallowed_tools, tool_name)
        logger.info(
            "mcp_tool_policy_decision",
            extra={
                "tenant_id": self.tenant_id,
                "run_id": self.run_id,
                "session_id": self.session_id,
                "tool_name": tool_name,
                "allowed": not denied,
            },
        )
        if denied:
            raise MCPToolNotAllowedError(
                tool_name,
                tenant_id=self.tenant_id,
                run_id=self.run_id,
                session_id=self.session_id,
            )


    def _validate_config(self):
        """Validates the initial configuration"""
        if not self.llm_provider:
            raise ConfigurationError("LLM provider not specified")

        if not self.model:
            raise ConfigurationError("Model not specified")

        # if not self.mcp_servers:
        #     raise ConfigurationError("No MCP servers configured")
        if self.has_mcp_servers:
            # Validate MCP servers
            for name, config in self.mcp_servers.items():
                if not config.get("command") and not config.get("url"):
                    raise ConfigurationError(
                        f"Server {name}: must have 'command' or 'url'"
                    )

    def _import_dependencies(self):
        """Imports required dependencies with enhanced error handling"""
        # Import mcp-use
        try:
            from mcp_use import MCPAgent, MCPClient
            from mcp_use.types.sandbox import SandboxOptions

            self.MCPAgent = MCPAgent
            self.MCPClient = MCPClient
            self.SandboxOptions = SandboxOptions
            logger.debug("mcp-use successfully imported")
        except ImportError as e:
            raise DependencyError(f"mcp-use not installed: {e}")

        # Import LangChain provider
        if self.llm_provider not in PROVIDER_IMPORTS:
            raise ConfigurationError(f"Unsupported provider: {self.llm_provider}")

        module_name, class_name = PROVIDER_IMPORTS[self.llm_provider]
        try:
            module = __import__(module_name, fromlist=[class_name])
            self.ChatLLM = getattr(module, class_name)
            logger.debug(f"{module_name} successfully imported")
        except ImportError as e:
            raise DependencyError(f"{module_name} not installed: {e}")

    def _create_llm(self):
        """Creates the LLM model instance with error handling"""
        try:
            # Costruzione centralizzata dei kwargs (base + provider-specific)
            kwargs = self._build_llm_kwargs()

            llm = self.ChatLLM(**kwargs)
            logger.debug(
                f"LLM {self.llm_provider}/{self.model} successfully created "
                f"with kwargs={ {k: v for k, v in kwargs.items() if k != 'api_key'} }"
            )
            return llm

        except Exception as e:
            raise MCPWrapperError(f"Error creating LLM model: {e}")


    def _build_llm_kwargs(self) -> Dict[str, Any]:
        """
        Costruisce i kwargs di base per il modello LLM e delega
        la parte provider-specific a _apply_provider_specific_kwargs.
        """
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "temperature": self.temperature,
        }

        if self.max_tokens:
            kwargs["max_tokens"] = self.max_tokens

        return self._apply_provider_specific_kwargs(kwargs)


    def _apply_provider_specific_kwargs(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Aggiunge ai kwargs le opzioni specifiche del provider
        (API key, base_url, ecc.).
        """
        # OpenAI / Anthropic: gestione API key
        if self.llm_provider in ("openai", "anthropic"):
            env_key = f"{self.llm_provider.upper()}_API_KEY"
            api_key = self.api_key or os.getenv(env_key)

            if not api_key:
                raise ConfigurationError(
                    f"Missing API key for provider '{self.llm_provider}'. "
                    f"Provide it explicitly or set {env_key} env var."
                )

            kwargs["api_key"] = api_key
            return kwargs

        # Ollama: gestione base_url
        if self.llm_provider == "ollama":
            base_url = self.base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
            kwargs["base_url"] = base_url
            return kwargs

        # Provider non supportato
        raise ConfigurationError(f"Unsupported LLM provider: {self.llm_provider}")


    async def initialize(self):
        """Initializes the MCP agent and clients with automatic retry"""
        if self._initialized:
            logger.debug("MCPWrapper already initialized")
            return

        try:
            await retry_async(self._initialize_internal, max_retries=3, delay=1.0)
            self._initialized = True
            logger.info("MCPWrapper successfully initialized")

        except Exception as e:
            logger.error(f"Initialization error after all attempts: {e}")
            raise MCPWrapperError(f"Initialization failed: {e}")

    async def _initialize_internal(self):
        """Internal initialization logic"""
        llm = self._create_llm()

        # Configure MCP client
        client_kwargs = {"config": {"mcpServers": self.mcp_servers}}

        if self.sandbox:
            client_kwargs["sandbox"] = True
            if self.sandbox_options:
                client_kwargs["sandbox_options"] = {
                    "api_key": self.sandbox_options.get("api_key", os.getenv("E2B_API_KEY")),
                    "sandbox_template_id": self.sandbox_options.get("sandbox_template_id", "base"),
                    "supergateway_command": self.sandbox_options.get("supergateway_command", "npx -y supergateway"),
                }

        self._client = self.MCPClient(**client_kwargs)

        # Strong enforcement: wrap client/session to block disallowed tools deterministically
        if self.disallowed_tools:
            self._client = _GuardedMCPClient(self._client, self)

        # Create the agent
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
        """
        Executes a query using the MCP agent

        Args:
            query: The query to process
            max_steps: Override for maximum steps (optional)
            server_name: Specific server name to use (optional)

        Returns:
            The agent's response as a string
        """
        if not self._initialized:
            await self.initialize()

        # Guardrails: before_model (validation/normalization)
        ctx = GuardrailContext(
            tenant_id=self.tenant_id,
            run_id=self.run_id,
            session_id=self.session_id,
            query=query,
            server_name=server_name,
        )
        ctx = await self._run_before_model_guardrails(ctx)
        query = ctx.query or ""

        if not query.strip():
            raise ValueError("Empty query not allowed")

        try:
            logger.debug(f"Executing query: {query[:100]}...")

            # Prepare parameters
            run_kwargs: Dict[str, Any] = {"query": query}

            if max_steps is not None:
                run_kwargs["max_steps"] = max_steps

            if server_name:
                if server_name not in self.mcp_servers:
                    raise ConfigurationError(f"Server '{server_name}' not configured")
                run_kwargs["server_name"] = server_name
                self._last_server_used = server_name

            # Define a separate async function for retry
            async def execute_agent_run():
                return await self._agent.run(**run_kwargs)

            # Execute the query with retry
            result = await retry_async(
                execute_agent_run, max_retries=2, delay=0.5
            )

            # Update stats
            self._steps_used = getattr(self._agent, "steps_used", 0)

            if not self._last_server_used and hasattr(self._agent, "last_server_used"):
                self._last_server_used = self._agent.last_server_used

            logger.debug(f"Query completed in {self._steps_used} steps")
            output = str(result)
            output = await self._run_after_model_guardrails(ctx, output)
            return output

        except MCPToolNotAllowedError:
            raise
        except GuardrailViolationError:
            raise
        except Exception as e:
            logger.error(f"Query execution error: {e}")
            raise MCPWrapperError(f"Query execution failed: {e}")

    async def close(self):
        """Closes connections and releases resources"""
        if self._client:
            try:
                await self._client.close_all_sessions()
                logger.debug("MCP client closed successfully")
            except Exception as e:
                logger.warning(f"Error closing MCP client: {e}")

        self._agent = None
        self._client = None
        self._initialized = False
        logger.debug("MCPWrapper closed")

    @property
    def steps_used(self) -> int:
        """Returns the number of steps used in the last run"""
        return self._steps_used

    @property
    def last_server_used(self) -> Optional[str]:
        """Returns the last server used"""
        return self._last_server_used

    @property
    def is_initialized(self) -> bool:
        """Indicates if the wrapper has been initialized"""
        return self._initialized

    def get_config_summary(self) -> Dict[str, Any]:
        """Returns a summary of the configuration"""
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
        """Tests the connection to configured MCP servers"""
        if not self._initialized:
            await self.initialize()

        results: Dict[str, bool] = {}
        for server_name in self.mcp_servers.keys():
            try:
                await self.run_query("ping", max_steps=1, server_name=server_name)
                results[server_name] = True
            except Exception as e:
                logger.warning(f"Connection test failed for {server_name}: {e}")
                results[server_name] = False

        return results

    def __repr__(self) -> str:
        return (
            f"MCPWrapper(provider={self.llm_provider}, model={self.model}, "
            f"servers={list(self.mcp_servers.keys())}, initialized={self._initialized})"
        )
