"""
MCP-BRIDGE REST API global settings
"""

from pydantic_settings import BaseSettings
from typing import List
import os
from dotenv import load_dotenv
from app.models.config import A2ASettings, A2AAgentConfig

load_dotenv()

class Settings(BaseSettings):
    """Global settings for MCP-BRIDGE"""
    
    # API Settings
    API_TITLE: str = "mcp-bridge: REST API for mcp-use library"
    API_DESCRIPTION: str = "A modular and scalable REST service to interact with MCP servers using the mcp-use library"
    API_VERSION: str = "0.1.0-beta"
    
    # Server Settings
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = False
    
    # CORS Settings
    CORS_ORIGINS: List[str] = ["*"]
    
    # Session Settings
    MAX_ACTIVE_SESSIONS: int = 100
    SESSION_TIMEOUT: int = 3600  # seconds
    
    # MCP Settings
    DEFAULT_MAX_STEPS: int = 30
    SUPPORTED_PROVIDERS: List[str] = ["openai", "anthropic", "ollama"]
    
    # Logging Settings
    LOG_LEVEL: str = "DEBUG"
    LOG_FORMAT: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    
    # E2B Sandbox Settings
    E2B_API_KEY: str = os.getenv("E2B_API_KEY", "")
    DEFAULT_SANDBOX_TEMPLATE: str = "base"

    OPENAI_API_KEY: str | None = None
    ANTHROPIC_API_KEY: str | None = None
    OLLAMA_BASE_URL: str | None = None

    LANGFUSE_PUBLIC_KEY: str | None = None
    LANGFUSE_SECRET_KEY: str | None = None
    LANGFUSE_HOST: str | None = None

    # A2A
  #  a2a: A2ASettings = A2ASettings()
    a2a: A2ASettings = A2ASettings(
        enabled=True,
        agents={
            "local_echo_agent": A2AAgentConfig(
                base_url="http://localhost:9001",
                card_path="/.well-known/agent.json",
                task_endpoint="/tasks",
                timeout_seconds=60,
            )
        },
    )
    
    class Config:
        env_file = ".env"
        case_sensitive = True

# global settings instance
settings = Settings()