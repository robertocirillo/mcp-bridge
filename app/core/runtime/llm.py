from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.core.exceptions import ConfigurationError, DependencyError, MCPWrapperError

PROVIDER_IMPORTS = {
    "openai": ("langchain_openai", "ChatOpenAI"),
    "anthropic": ("langchain_anthropic", "ChatAnthropic"),
    "ollama": ("langchain_ollama", "ChatOllama"),
}


@dataclass(frozen=True)
class RuntimeDependencies:
    MCPAgent: Any
    MCPClient: Any
    SandboxOptions: Any
    ChatLLM: Any


def normalize_sandbox_options(sandbox_options: Optional[Any]) -> Dict[str, Any]:
    if sandbox_options is None:
        return {}

    if hasattr(sandbox_options, "model_dump"):
        return sandbox_options.model_dump(exclude_none=True)

    if hasattr(sandbox_options, "dict"):
        return sandbox_options.dict(exclude_none=True)  # type: ignore[call-arg]

    if isinstance(sandbox_options, dict):
        return sandbox_options

    try:
        return {
            "api_key": getattr(sandbox_options, "api_key", None),
            "sandbox_template_id": getattr(sandbox_options, "sandbox_template_id", "base"),
            "supergateway_command": getattr(
                sandbox_options,
                "supergateway_command",
                "npx -y supergateway",
            ),
        }
    except Exception:
        raise ConfigurationError(
            f"Unsupported sandbox_options type: {type(sandbox_options)!r}. "
            "Expected dict or Pydantic BaseModel."
        )


def import_runtime_dependencies(llm_provider: str) -> RuntimeDependencies:
    try:
        from mcp_use import MCPAgent, MCPClient
        from mcp_use.types.sandbox import SandboxOptions
    except ImportError as exc:
        raise DependencyError(f"mcp-use not installed: {exc}")

    if llm_provider not in PROVIDER_IMPORTS:
        raise ConfigurationError(f"Unsupported provider: {llm_provider}")

    module_name, class_name = PROVIDER_IMPORTS[llm_provider]
    try:
        module = __import__(module_name, fromlist=[class_name])
        chat_llm = getattr(module, class_name)
    except ImportError as exc:
        raise DependencyError(f"{module_name} not installed: {exc}")

    return RuntimeDependencies(
        MCPAgent=MCPAgent,
        MCPClient=MCPClient,
        SandboxOptions=SandboxOptions,
        ChatLLM=chat_llm,
    )


def build_llm_kwargs(
    *,
    llm_provider: str,
    model: str,
    temperature: float,
    max_tokens: Optional[int],
    api_key: Optional[str],
    base_url: Optional[str],
) -> Dict[str, Any]:
    import os

    kwargs: Dict[str, Any] = {
        "model": model,
        "temperature": temperature,
    }
    if max_tokens:
        kwargs["max_tokens"] = max_tokens

    if llm_provider in ("openai", "anthropic"):
        env_key = f"{llm_provider.upper()}_API_KEY"
        resolved_api_key = api_key or os.getenv(env_key)
        if not resolved_api_key:
            raise ConfigurationError(
                f"Missing API key for provider '{llm_provider}'. "
                f"Provide it explicitly or set {env_key} env var."
            )
        kwargs["api_key"] = resolved_api_key
        return kwargs

    if llm_provider == "ollama":
        kwargs["base_url"] = base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        return kwargs

    raise ConfigurationError(f"Unsupported LLM provider: {llm_provider}")


def create_llm(
    chat_llm_cls: Any,
    *,
    llm_provider: str,
    model: str,
    temperature: float,
    max_tokens: Optional[int],
    api_key: Optional[str],
    base_url: Optional[str],
) -> Any:
    try:
        kwargs = build_llm_kwargs(
            llm_provider=llm_provider,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_key,
            base_url=base_url,
        )
        return chat_llm_cls(**kwargs)
    except ConfigurationError:
        raise
    except Exception as exc:
        raise MCPWrapperError(f"Error creating LLM model: {exc}")
