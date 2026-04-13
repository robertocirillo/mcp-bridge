"""Global settings for mcp-bridge."""

import os
from typing import List

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

from app import __description__, __title__, __version__
from app.models.config import A2ASettings, MultiTenancySettings

load_dotenv()


DEFAULT_CORS_ORIGINS = [
    "http://localhost",
    "https://localhost",
    "http://127.0.0.1",
    "https://127.0.0.1",
    "http://[::1]",
    "https://[::1]",
    "http://localhost:3000",
    "https://localhost:3000",
    "http://127.0.0.1:3000",
    "https://127.0.0.1:3000",
    "http://[::1]:3000",
    "https://[::1]:3000",
    "http://localhost:5173",
    "https://localhost:5173",
    "http://127.0.0.1:5173",
    "https://127.0.0.1:5173",
    "http://[::1]:5173",
    "https://[::1]:5173",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "https://localhost:8000",
    "https://127.0.0.1:8000",
    "http://[::1]:8000",
    "https://[::1]:8000",
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

    # A2A stays experimental and opt-in. Enable and configure agents explicitly via env/config.
    a2a: A2ASettings = A2ASettings(enabled=False, agents={})

    # MultiTenancy
    multi_tenancy: MultiTenancySettings = MultiTenancySettings()

# global settings instance
settings = Settings()
