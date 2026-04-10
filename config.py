"""
MCP-BRIDGE REST API global settings
"""

import os
from typing import Any, List

from dotenv import load_dotenv
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.models.config import A2ASettings, A2AAgentConfig, MultiTenancySettings, A2AAuthConfig

load_dotenv()


class Settings(BaseSettings):
    """Global settings for MCP-BRIDGE"""


    model_config = SettingsConfigDict(
        env_file=".env",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    # API Settings
    API_TITLE: str = "mcp-bridge: REST API for mcp-use library"
    API_DESCRIPTION: str = (
        "A modular and scalable REST service to interact with MCP servers using the mcp-use library"
    )
    API_VERSION: str = "0.2.0"

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

    # Bias-detector-service (optional internal dependency)
    BIAS_DETECTOR_SERVICE_BASE_URL: str = "http://bias-detector-service:9090"

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

    A2A_ENABLED: bool = False

    # A2A
    a2a: A2ASettings = A2ASettings(
        enabled=False,
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

    @model_validator(mode="before")
    @classmethod
    def _apply_flat_a2a_enabled_override(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        flat_enabled = data.get("A2A_ENABLED", data.get("a2a_enabled"))
        if flat_enabled is None:
            return data

        a2a_value = data.get("a2a")
        if isinstance(a2a_value, dict) and "enabled" in a2a_value:
            return data
        if a2a_value is not None and hasattr(a2a_value, "enabled"):
            return data

        merged = dict(data)
        default_a2a = cls.model_fields["a2a"].default
        if isinstance(a2a_value, dict):
            merged["a2a"] = default_a2a.model_copy(deep=True, update={**a2a_value, "enabled": flat_enabled})
        else:
            merged["a2a"] = default_a2a.model_copy(deep=True, update={"enabled": flat_enabled})
        return merged

# global settings instance
settings = Settings()
