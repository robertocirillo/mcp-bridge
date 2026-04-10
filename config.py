"""Global settings for mcp-bridge."""

import os
from typing import List

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

from app import __description__, __title__, __version__
from app.models.config import A2ASettings, A2AAgentConfig, MultiTenancySettings, A2AAuthConfig

load_dotenv()


DEFAULT_CORS_ORIGINS = [
    "http://localhost",
    "http://127.0.0.1",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]


class Settings(BaseSettings):
    """Global settings for mcp-bridge."""


    model_config = SettingsConfigDict(
        env_file=".env",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    # API Settings
    API_TITLE: str = __title__
    API_DESCRIPTION: str = __description__
    API_VERSION: str = __version__

    # Server Settings
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = False

    # CORS Settings
    CORS_ORIGINS: List[str] = DEFAULT_CORS_ORIGINS.copy()

    # Session Settings
    MAX_ACTIVE_SESSIONS: int = 100
    SESSION_TIMEOUT: int = 3600  # seconds

    # MCP Settings
    DEFAULT_MAX_STEPS: int = 30
    SUPPORTED_PROVIDERS: List[str] = ["openai", "anthropic", "ollama"]

    # Bias-detector-service (optional internal dependency)
    BIAS_DETECTOR_SERVICE_BASE_URL: str = "http://bias-detector-service:9090"

    # Logging Settings
    LOG_LEVEL: str = "INFO"
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
    a2a: A2ASettings = A2ASettings(
        enabled=True,
        agents={
            # Example local A2A agent configuration.
            "local_echo_agent": A2AAgentConfig(
                card_url="http://localhost:9001/.well-known/agent.json",
                runtime_url="http://localhost:9001",
                timeout_seconds=60,
                enabled=False,
                label="Local Echo Agent",
                description="Simple local A2A agent used for testing.",
            ),
            # ✅ A2A sample: HelloWorld (protocol-compliant)
            "helloworld": A2AAgentConfig(
                enabled=True,
                label="Hello World Agent (A2A sample)",
                description="A2A protocol-compliant HelloWorld agent from a2a-samples.",
                card_url="http://localhost:9999/.well-known/agent-card.json",
                runtime_url="http://localhost:9999",
                timeout_seconds=60,
                auth=A2AAuthConfig(type="none"),
                extra_headers={},
            ),
            "helloworld_extended": A2AAgentConfig(
                enabled=True,
                label="Hello World Agent (extended card)",
                description="HelloWorld agent using authenticated extended card.",
                card_url="http://localhost:9999/agent/authenticatedExtendedCard",
                runtime_url="http://localhost:9999",
                timeout_seconds=60,
                auth=A2AAuthConfig(type="bearer_token", env_var="A2A_HELLOWORLD_BEARER_TOKEN"),
                extra_headers={},
            ),
            "langgraph": A2AAgentConfig(
                enabled=True,
                label="LangGraph Agent (A2A sample)",
                description="Task-based A2A agent from a2a-samples (LangGraph sample).",
                card_url="http://localhost:9998/.well-known/agent-card.json",
                runtime_url="http://localhost:9998",
                timeout_seconds=60,
                auth=A2AAuthConfig(type="none"),
                extra_headers={},
            ),

        },
    )

    # MultiTenancy
    multi_tenancy: MultiTenancySettings = MultiTenancySettings()

# global settings instance
settings = Settings()
