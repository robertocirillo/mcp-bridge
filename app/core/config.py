"""
Pydantic models for configurations
"""

from pydantic import BaseModel, Field
from typing import Optional, Dict, List, Any

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

class SessionConfig(BaseModel):
    """Configuration to create a new session"""
    llm_provider: LLMProvider
    # Allow sessions without MCP servers:
    # - request can omit "mcp_servers"
    # - or send "mcp_servers": {}
    mcp_servers: Dict[str, MCPServerConfig] = Field(
        default_factory=dict,
        description="Optional MCP servers configuration. Can be empty for LLM-only sessions.",
    )
    max_steps: int = Field(30, gt=0, le=100, description="maximum number of steps")
    use_server_manager: bool = Field(False, description="Use the server manager for automatic selection")
    disallowed_tools: Optional[List[str]] = Field(None, description="List of disallowed tools")
    sandbox: bool = Field(False, description="Enable E2B sandbox")
    sandbox_options: Optional[SandboxOptions] = Field(None, description="Options for E2B sandbox")
    verbose: bool = Field(False, description="Enable verbose logging")

    def model_post_init(self, __context):
        """post-initialization validation"""
        # use default sandbox options if sandbox is enabled but no options provided
        if self.sandbox and not self.sandbox_options:
            self.sandbox_options = SandboxOptions()