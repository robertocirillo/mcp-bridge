"""
Pydantic models for configurations
"""

from pydantic import BaseModel, Field, AnyHttpUrl
from typing import Optional, Dict, List



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
    mcp_servers: Dict[str, MCPServerConfig] = Field(..., min_items=1)
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



class A2AAgentConfig(BaseModel):
    """Configuration for a remote A2A agent/server."""

    base_url: AnyHttpUrl
    card_path: str = "/.well-known/agent.json"
    task_endpoint: str = "/tasks"
    auth_header: Optional[str] = None       # e.g. "Authorization"
    auth_token: Optional[str] = None        # e.g. "Bearer xxx"
    timeout_seconds: int = 60


class A2ASettings(BaseModel):
    """Global A2A settings."""

    enabled: bool = True
    agents: Dict[str, A2AAgentConfig] = {}

