"""Pydantic models for configurations
"""

from __future__ import annotations

from typing import Optional, Dict, List

from pydantic import BaseModel, Field, ConfigDict
from typing_extensions import Literal


class LLMProvider(BaseModel):
    """Configuration of the LLM provider"""

    provider: str = Field(..., description="Model provider (openai, anthropic, ollama)")
    model: str = Field(..., description="Model name")
    api_key: Optional[str] = Field(None, description="API key (optional if in env)")
    base_url: Optional[str] = Field(None, description="Base URL for custom provider (e.g. Ollama)")
    temperature: Optional[float] = Field(0.7, ge=0.0, le=2.0, description="Model temperature")
    max_tokens: Optional[int] = Field(None, gt=0, description="Maximum number of tokens")


class MCPServerConfig(BaseModel):
    """Configuration of an MCP Server"""

    command: Optional[str] = Field(None, description="Command to start the server")
    args: Optional[List[str]] = Field(None, description="Command arguments")
    env: Optional[Dict[str, str]] = Field(None, description="Environment variables")
    url: Optional[str] = Field(None, description="URL for HTTP connections")

    def model_post_init(self, __context):
        """Post-initialization validation"""
        if not self.command and not self.url:
            raise ValueError("At least one between 'command' or 'url' must be specified")
        if self.command and self.url:
            raise ValueError("It is not possible to specify both 'command' and 'url'")


class SandboxOptions(BaseModel):
    """Options for E2B sandbox"""

    api_key: Optional[str] = Field(None, description="E2B API key")
    sandbox_template_id: str = Field("base", description="Sandbox template ID")
    supergateway_command: str = Field("npx -y supergateway", description="Supergateway command")
    timeout: int = Field(300, gt=0, description="Timeout in seconds")


# -----------------------------
# Guardrails (session-scoped)
# -----------------------------


class PiiSettings(BaseModel):
    """PII guardrail settings.

    LangChain-style phases:
    - before_model: input handling ("input_mode")
    - after_model: output handling ("output_mode")

    Strategy 3:
    - "mode" is a SHARED DEFAULT for both phases.
    - "input_mode" and "output_mode" are explicit per-phase overrides.
    """

    input_mode: Literal["off", "redact", "block"] = Field(
        default="block",
        description=(
            "How to handle PII detected in user input before the model is called. "
            "This is a phase-specific override. "
            "'off' disables input scanning; "
            "'redact' replaces detected entities with placeholders; "
            "'block' raises a structured GuardrailViolationError(code='PII_DETECTED', phase='before_model')."
        ),
    )

    output_mode: Optional[Literal["off", "redact", "block"]] = Field(
        default=None,
        description=(
            "Phase-specific override for output PII handling. "
            "If provided, it takes precedence over `mode` for output."
        ),
    )

    mode: Literal["off", "redact", "block"] = Field(
        default="redact",
        description=(
            "Shared default PII handling strategy. "
            "If provided, it applies to BOTH input (before_model) and output (after_model) unless overridden by "
            "`input_mode` or `output_mode`. "
            "Allowed values: "
            "'off' disables the PII guardrail for both phases; "
            "'redact' replaces detected entities with placeholders; "
            "'block' raises a structured GuardrailViolationError(code='PII_DETECTED')."
        ),
    )


class BiasCheckSettings(BaseModel):
    # Allow `model_id` field name (used to forward overrides to bias-detector-service).
    model_config = ConfigDict(protected_namespaces=())

    """Per-check overrides for the bias-detector-service guardrail.

    A check inherits all "common" bias settings from `BiasSettings` (session-level defaults)
    and can override only the model/policy fields that are typically varied during a
    "cascaded" evaluation.

    NOTE: A field is considered an override when it is *present* in the request JSON,
    even if its value is null. This enables explicit override-to-null (e.g. to omit
    `unsafe_labels` and rely on the upstream model registry policy).
    """

    name: Optional[str] = Field(
        default=None,
        description="Optional check name (for debugging and cascaded results reporting).",
    )

    threshold: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional threshold override for this check.",
    )

    top_k: Optional[int] = Field(
        default=None,
        ge=1,
        le=100,
        description="Optional top_k override for this check.",
    )

    active_categories: Optional[List[str]] = Field(
        default=None,
        description="Optional active_categories override for this check.",
    )

    unsafe_labels: Optional[List[str]] = Field(
        default=None,
        description="Optional unsafe_labels override for this check.",
    )

    model_id: Optional[str] = Field(
        default=None,
        description="Optional model_id override for this check.",
    )

    revision: Optional[str] = Field(
        default=None,
        description="Optional revision override for this check.",
    )


class BiasSettings(BaseModel):
    # Allow `model_id` field name (used to forward overrides to bias-detector-service).
    model_config = ConfigDict(protected_namespaces=())
    """Bias detector guardrail settings.

    MVP0 scope:
    - after_model only (output).
    - actions supported: off | block

    Strategy 3:
    - `mode` is a shared default.
    - `output_mode` is a phase-specific override (only phase used in MVP0).
    """

    # --- Policy (Strategy 3: shared default + phase override) ---

    output_mode: Optional[Literal["off", "block"]] = Field(
        default=None,
        description=(
            "Phase-specific override for output (after_model) bias handling. "
            "If provided, it takes precedence over `mode` for output."
        ),
    )

    mode: Literal["off", "block"] = Field(
        default="block",
        description=(
            "Shared default bias handling strategy. "
            "Allowed values: "
            "'off' disables the bias detector guardrail; "
            "'block' blocks the response (HTTP 403) with structured error code 'BIAS_DETECTED'."
        ),
    )

    # --- External bias-detector-service integration (session-scoped) ---

    base_url: Optional[str] = Field(
        default="http://bias-detector-service:9090",
        description=(
            "Optional base URL for bias-detector-service (e.g. http://bias-detector-service:9090). "
            "If set, mcp-bridge will call POST /v1/bias/classify in after_model. "
            "If unset, mcp-bridge falls back to the built-in detector selected via env vars "
            "(noop/rules)."
        ),
    )

    timeout_seconds: float = Field(
        default=5.0,
        gt=0.0,
        description="HTTP timeout for bias-detector-service requests.",
    )

    threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Classifier threshold forwarded to bias-detector-service.",
    )

    top_k: int = Field(
        default=5,
        ge=1,
        le=100,
        description="Top-K labels requested from bias-detector-service.",
    )

    return_all_scores: bool = Field(
        default=False,
        description=(
            "If true, bias-detector-service returns scores for all labels (not only top_k). "
            "Forwarded as `return_all_scores`."
        ),
    )

    return_char_spans: bool = Field(
        default=False,
        description=(
            "If true, bias-detector-service returns character spans for detected labels "
            "when supported by the model/pipeline. Forwarded as `return_char_spans`."
        ),
    )

    active_categories: Optional[List[str]] = Field(
        default=None,
        description=(
            "Optional list of active bias categories. "
            "If omitted (null), all categories are active on the detector service. "
            "If an empty list is provided, no categories are active and flagged will be false."
        ),
    )

    unsafe_labels: Optional[List[str]] = Field(
        default=None,
        description=(
            "Optional list of labels considered unsafe by the upstream bias-detector-service. "
            "If provided, the service will mark a label as flagged only when (label in unsafe_labels) "
            "and (score >= threshold). "
            "If omitted (null), the service may apply its own per-model registry policy."
        ),
    )


    model_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional model override (HF model id) forwarded to bias-detector-service. "
            "If unset, the service default model is used."
        ),
    )

    revision: Optional[str] = Field(
        default=None,
        description="Optional model revision override forwarded to bias-detector-service.",
    )


    checks: Optional[List[BiasCheckSettings]] = Field(
        default=None,
        description=(
            "Optional list of cascaded bias checks to run in after_model. "
            "If omitted (null), mcp-bridge runs a single bias-detector-service call using the "
            "session-level defaults. "
            "If provided, each element inherits all common fields from this BiasSettings and can override "
            "model_id/revision/threshold/top_k/active_categories/unsafe_labels." 
        ),
    )


class GuardrailsSettings(BaseModel):
    """Session-scoped guardrails configuration."""

    enabled: bool = Field(
        default=False,
        description=(
            "Global switch to enable/disable ALL guardrails for the session. "
            "If false, no before_model/after_model guardrail will run."
        ),
    )

    pii: PiiSettings = Field(
        default_factory=PiiSettings,
        description="PII detection/redaction/blocking settings.",
    )

    bias: BiasSettings = Field(
        default_factory=BiasSettings,
        description="Bias detector settings (MVP0: after_model only).",
    )


class SessionConfig(BaseModel):
    """Configuration to create a new session"""

    llm_provider: LLMProvider

    mcp_servers: Dict[str, MCPServerConfig] = Field(
        default_factory=dict,
        description="Optional MCP servers configuration. Can be empty for LLM-only sessions.",
    )

    max_steps: int = Field(30, gt=0, le=100, description="maximum number of steps")
    use_server_manager: bool = Field(False, description="Use the server manager for automatic selection")

    disallowed_tools: Optional[List[str]] = Field(None, description="List of disallowed tools (supports wildcards)")

    sandbox: bool = Field(False, description="Enable E2B sandbox")
    sandbox_options: Optional[SandboxOptions] = Field(None, description="Options for E2B sandbox")

    verbose: bool = Field(False, description="Enable verbose logging")

    guardrails: GuardrailsSettings = Field(
        default_factory=GuardrailsSettings,
        description="Guardrails configuration (PII, bias, etc.)",
    )

    def model_post_init(self, __context):
        """post-initialization validation"""
        if self.sandbox and not self.sandbox_options:
            self.sandbox_options = SandboxOptions()


class MultiTenancySettings(BaseModel):
    enabled: bool = False
    require_header: bool = False
    default_tenant_id: Optional[str] = "default"


class A2AAuthConfig(BaseModel):
    type: Literal["none", "api_key_header", "bearer_token"] = "none"
    header_name: Optional[str] = None
    env_var: Optional[str] = None


class A2AAgentConfig(BaseModel):
    enabled: bool = True
    label: Optional[str] = None
    description: Optional[str] = None
    card_url: str
    runtime_url: Optional[str] = None
    timeout_seconds: int = 60
    auth: Optional[A2AAuthConfig] = None
    extra_headers: Dict[str, str] = Field(default_factory=dict)


class A2ASettings(BaseModel):
    enabled: bool = True
    agents: Dict[str, A2AAgentConfig] = Field(default_factory=dict)
