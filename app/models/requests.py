"""
Pydantic models for HTTP requests
"""

from pydantic import BaseModel, Field
from typing import Optional, Dict, List, Any

from .config import LLMProvider, MCPServerConfig, SandboxOptions

class SessionCreateRequest(BaseModel):
    """Request to create a new session"""
    llm_provider: LLMProvider
    mcp_servers: Dict[str, MCPServerConfig] = Field(..., min_items=1)
    max_steps: int = Field(30, gt=0, le=100, description="Maximum number of agent steps")
    use_server_manager: bool = Field(False, description="Use the server manager for automatic selection")
    disallowed_tools: Optional[List[str]] = Field(None, description="Disallowed tools")
    sandbox: bool = Field(False, description="Use the E2B sandbox environment")
    sandbox_options: Optional[SandboxOptions] = Field(None, description="Options for the sandbox")
    verbose: bool = Field(False, description="Verbose mode for debugging")

class QueryRequest(BaseModel):
    """Request to execute a query"""
    query: str = Field(..., min_length=1, description="Query to execute")
    max_steps: Optional[int] = Field(None, gt=0, le=100, description="Override for maximum number of steps")
    server_name: Optional[str] = Field(None, description="Specific server name to use")

class SessionUpdateRequest(BaseModel):
    """Request to update a session"""
    max_steps: Optional[int] = Field(None, gt=0, le=100, description="New maximum number of steps")
    verbose: Optional[bool] = Field(None, description="New verbose mode")
