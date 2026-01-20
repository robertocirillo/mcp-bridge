"""Pydantic models for configurations
"""

from __future__ import annotations

from typing import Optional, Dict, List

from pydantic import BaseModel, Field
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


class BiasSettings(BaseModel):
    """Bias detector guardrail settings.

    MVP0 scope:
    - after_model only (output).
    - actions supported: off | block

    Strategy 3:
    - `mode` is a shared default.
    - `output_mode` is a phase-specific override (only phase used in MVP0).
    """

    output_mode: Optional[Literal["off", "block"]] = Field(
        default=None,
        description=(
            "Phase-specific override for output (after_model) bias handling. "
            "If provided, it takes precedence over `mode` for output."
        ),
    )

    mode: Literal["off", "block"] = Field(
        default="off",
        description=(
            "Shared default bias handling strategy. "
            "Allowed values: "
            "'off' disables the bias detector guardrail; "
            "'block' blocks the response (HTTP 403) with structured error code 'BIAS_DETECTED'."
        ),
    )


class GuardrailsSettings(BaseModel):
    """Session-scoped guardrails configuration."""

    enabled: bool = Field(
        default=True,
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
