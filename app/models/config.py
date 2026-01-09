"""
Pydantic models for configurations
"""

from typing import Dict, Optional, Literal, List
from pydantic import BaseModel, Field



class LLMProvider(BaseModel):
    """LLM provider configuration"""
    provider: str = Field(..., description="Model provider (openai, anthropic, ollama)")
    model: str = Field(..., description="Model name")
    api_key: Optional[str] = Field(None, description="API key (optional if set in env)")
    base_url: Optional[str] = Field(None, description="Base URL for custom provider (e.g., Ollama)")
    temperature: Optional[float] = Field(0.7, ge=0.0, le=2.0, description="Model temperature")
    max_tokens: Optional[int] = Field(None, gt=0, description="Maximum number of tokens")

class MCPServerConfig(BaseModel):
    """MCP Server configuration"""
    command: Optional[str] = Field(None, description="Command to start the server")
    args: Optional[List[str]] = Field(None, description="Command arguments")
    env: Optional[Dict[str, str]] = Field(None, description="Environment variables")
    url: Optional[str] = Field(None, description="URL for HTTP connections")

    def model_post_init(self, __context):
        """Post-initialization validation"""
        if not self.command and not self.url:
            raise ValueError("At least one of 'command' or 'url' must be specified")
        if self.command and self.url:
            raise ValueError("Cannot specify both 'command' and 'url'")

class SandboxOptions(BaseModel):
    """Options for the E2B sandbox"""
    api_key: Optional[str] = Field(None, description="E2B API key")
    sandbox_template_id: str = Field("base", description="Sandbox template ID")
    supergateway_command: str = Field("npx -y supergateway", description="Supergateway command")
    timeout: int = Field(300, gt=0, description="Timeout in seconds")

class SessionConfig(BaseModel):
    """Configuration to create a new session"""
    llm_provider: LLMProvider
    mcp_servers: Dict[str, MCPServerConfig] = Field(..., min_length=1)
    max_steps: int = Field(30, gt=0, le=100, description="Maximum number of agent steps")
    use_server_manager: bool = Field(False, description="Use the server manager for automatic selection")
    disallowed_tools: Optional[List[str]] = Field(None, description="Disallowed tools")
    sandbox: bool = Field(False, description="Use the E2B sandbox environment")
    sandbox_options: Optional[SandboxOptions] = Field(None, description="Options for the sandbox")
    verbose: bool = Field(False, description="Verbose mode for debugging")


    def model_post_init(self, __context):
        """Post-initialization validation"""
        # If sandbox is enabled but no options are provided, use default options
        if self.sandbox and not self.sandbox_options:
            self.sandbox_options = SandboxOptions()

class A2AAuthConfig(BaseModel):
    """
    Authentication configuration for a single A2A agent.

    This is purely local to mcp-bridge and does not change the A2A protocol.
    It just tells the bridge how to add auth headers when calling the agent.
    """

    type: Literal["none", "api_key_header", "bearer_token"] = "none"
    header_name: Optional[str] = None       # e.g. "Authorization" or "X-API-Key"
    env_var: Optional[str] = None           # e.g. "PAYMENTS_AGENT_API_KEY"





class A2AAgentConfig(BaseModel):
    """
    Base configuration for a single A2A agent.

    This is intentionally minimal and protocol-agnostic:
    - card_url points to the A2A Agent Card (/.well-known/agent.json or similar)
    - runtime_url (optional) can be used later by the a2a-sdk client
    """

    # Identity / presentation
    enabled: bool = True
    label: Optional[str] = None
    description: Optional[str] = None

    # Discovery / endpoints
    card_url: str                    # Full URL to the Agent Card
    runtime_url: Optional[str] = None  # Optional base URL for JSON-RPC runtime
    timeout_seconds: int = 60

    # Auth & headers (local to mcp-bridge)
    auth: Optional[A2AAuthConfig] = None
    extra_headers: Dict[str, str] = Field(default_factory=dict)


class A2ASettings(BaseModel):
    """
    Global A2A integration settings for mcp-bridge.

    - enabled: master switch for all A2A features
    - agents: logical agent_id -> configuration
    """

    enabled: bool = True
    agents: Dict[str, A2AAgentConfig] = Field(default_factory=dict)




class MultiTenancySettings(BaseModel):
    """Multi-tenancy configuration.

    - enabled: if False, the app behaves as single-tenant.
    - require_header: if True, X-Tenant-Id must be present on incoming requests.
    - default_tenant_id: used when tenant headers are missing and require_header is False.
    """

    enabled: bool = False
    require_header: bool = False
    default_tenant_id: Optional[str] = "default"
