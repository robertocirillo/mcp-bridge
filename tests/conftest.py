import sys
from pathlib import Path
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _ensure_test_stubs() -> None:
    """Provide minimal stubs when running tests from a partial source bundle.

    The upstream project contains these modules (config, app.models.*). The
    sandbox bundle used for this patch may omit them; when missing, tests that
    import routes and SessionConfig would fail at import time.

    This function injects lightweight, dependency-free stubs ONLY if the real
    modules are not available.
    """

    # -----------------------------
    # config (settings)
    # -----------------------------
    try:
        import config  # noqa: F401
    except Exception:
        from pydantic import BaseModel, computed_field

        config_mod = types.ModuleType("config")

        class _MultiTenancy(BaseModel):
            enabled: bool = False
            require_header: bool = False
            default_tenant_id: str = "default"

        class _A2A(BaseModel):
            agents: dict = {}

        class Settings(BaseModel):
            # Values used across the current test suite
            MAX_ACTIVE_SESSIONS: int = 100
            SESSION_TIMEOUT: int = 3600
            SUPPORTED_PROVIDERS: list[str] = ["ollama", "openai", "anthropic"]
            API_VERSION: str = "0.0.0"
            API_TITLE: str = "mcp-bridge"
            API_DESCRIPTION: str = "test-stub"

            multi_tenancy: _MultiTenancy = _MultiTenancy()
            a2a: _A2A = _A2A()

        config_mod.Settings = Settings
        config_mod.settings = Settings()
        sys.modules["config"] = config_mod

    # -----------------------------
    # app.models.config
    # -----------------------------
    try:
        import app.models.config  # noqa: F401
    except Exception:
        from pydantic import BaseModel, Field
        from typing import Any, Dict, List, Optional

        pkg_app = sys.modules.setdefault("app", types.ModuleType("app"))
        pkg_models = sys.modules.setdefault("app.models", types.ModuleType("app.models"))
        pkg_app.models = pkg_models

        mod = types.ModuleType("app.models.config")

        class LLMProviderConfig(BaseModel):
            provider: str
            model: str
            api_key: Optional[str] = None
            base_url: Optional[str] = None
            temperature: Optional[float] = None
            max_tokens: Optional[int] = None

        # Backwards/compat names used by some tests / older imports.
        class LLMProvider(LLMProviderConfig):
            pass

        class SandboxOptions(BaseModel):
            api_key: Optional[str] = None
            sandbox_template_id: str = "base"
            supergateway_command: str = "npx -y supergateway"

        class PiiGuardrailConfig(BaseModel):
            mode: Optional[str] = None
            input_mode: Optional[str] = None
            output_mode: Optional[str] = None

        class PiiSettings(PiiGuardrailConfig):
            pass

        class GuardrailsConfig(BaseModel):
            enabled: bool = True
            pii: Optional[PiiGuardrailConfig] = None

        class GuardrailsSettings(GuardrailsConfig):
            pass

        class SessionConfig(BaseModel):
            llm_provider: LLMProviderConfig
            mcp_servers: Dict[str, Dict[str, Any]] = Field(default_factory=dict)

            max_steps: int = 30
            verbose: bool = False
            sandbox: bool = False
            sandbox_options: Optional[SandboxOptions] = None
            disallowed_tools: Optional[List[str]] = None
            use_server_manager: bool = False

            guardrails: Optional[GuardrailsConfig] = None

        # Sessions endpoint expects this name too
        class SessionCreateRequest(SessionConfig):
            pass

        # A2A dependency typing
        class A2AAgentConfig(BaseModel):
            agent_id: Optional[str] = None

        mod.LLMProviderConfig = LLMProviderConfig
        mod.LLMProvider = LLMProvider
        mod.SandboxOptions = SandboxOptions
        mod.PiiGuardrailConfig = PiiGuardrailConfig
        mod.PiiSettings = PiiSettings
        mod.GuardrailsConfig = GuardrailsConfig
        mod.GuardrailsSettings = GuardrailsSettings
        mod.SessionConfig = SessionConfig
        mod.SessionCreateRequest = SessionCreateRequest
        mod.A2AAgentConfig = A2AAgentConfig

        sys.modules["app.models.config"] = mod

    # -----------------------------
    # app.models.requests / app.models.responses
    # -----------------------------
    try:
        import app.models.requests  # noqa: F401
    except Exception:
        from pydantic import BaseModel, computed_field
        from typing import Any, Dict, Optional

        mod = types.ModuleType("app.models.requests")

        try:
            from app.models.config import SessionCreateRequest as _SessionCreateRequest
        except Exception:
            _SessionCreateRequest = BaseModel  # type: ignore

        class SessionCreateRequest(_SessionCreateRequest):  # type: ignore[misc]
            pass

        class QueryRequest(BaseModel):
            query: str
            max_steps: Optional[int] = None
            server_name: Optional[str] = None

        # A2A request model (used by app.api.routes.a2a)
        class A2AMessageRequest(BaseModel):
            goal: str
            blocking: bool = True
            metadata: Optional[Dict[str, Any]] = None

        mod.SessionCreateRequest = SessionCreateRequest
        mod.QueryRequest = QueryRequest
        mod.A2AMessageRequest = A2AMessageRequest
        sys.modules["app.models.requests"] = mod

    try:
        import app.models.responses  # noqa: F401
    except Exception:
        from pydantic import BaseModel
        from typing import Any, Dict, List, Optional, Literal
        from datetime import datetime
        from enum import Enum

        mod = types.ModuleType("app.models.responses")

        class SessionResponse(BaseModel):
            session_id: str
            status: str
            message: str
            servers: List[str] = []

        class SessionInfo(BaseModel):
            session_id: str
            status: str = "active"
            created_at: Optional[datetime] = None
            last_used: Optional[datetime] = None
            query_count: int = 0
            servers: List[str] = []
            llm_provider: Optional[str] = None
            llm_model: Optional[str] = None

        class QueryResponse(BaseModel):
            session_id: str
            result: Any
            execution_time: float
            steps_used: int
            timestamp: datetime
            server_used: Optional[str] = None
            has_mcp_servers: Optional[bool] = None

        # Health models (not used by the new tests, but imported by some routes)
        class HealthResponse(BaseModel):
            status: str
            timestamp: datetime
            active_sessions: int
            supported_providers: List[str]
            features: Dict[str, Any]

        class SessionStatsResponse(BaseModel):
            total_sessions: int
            active_sessions: int
            total_queries: int
            avg_execution_time: float
            providers_usage: Dict[str, int]

        mod.SessionResponse = SessionResponse
        mod.SessionInfo = SessionInfo
        mod.QueryResponse = QueryResponse
        mod.HealthResponse = HealthResponse
        mod.SessionStatsResponse = SessionStatsResponse

        # -----------------------------
        # A2A response models (used by app.api.routes.a2a)
        # -----------------------------
        class A2ATaskState(str, Enum):
            submitted = "submitted"
            working = "working"
            input_required = "input_required"
            completed = "completed"
            canceled = "canceled"
            failed = "failed"
            unknown = "unknown"

        class A2AAgentSummary(BaseModel):
            agent_id: str
            name: str
            description: Optional[str] = None
            enabled: bool = True
            endpoint: Optional[str] = None
            card_url: Optional[str] = None

        class A2AMessageResponse(BaseModel):
            mode: Literal["blocking", "task"]
            agent_id: str
            task_id: Optional[str] = None
            status: Optional[A2ATaskState] = None
            upstream_state: Optional[str] = None
            output: Optional[Any] = None
            message: Optional[str] = None
            raw_response: Optional[Any] = None

        class A2ATaskStatusResponse(BaseModel):
            agent_id: str
            task_id: str
            status: A2ATaskState
            upstream_state: Optional[str] = None
            output: Optional[Any] = None
            message: Optional[str] = None
            raw_response: Optional[Any] = None

            @computed_field
            @property
            def is_terminal(self) -> bool:
                return self.status in {A2ATaskState.completed, A2ATaskState.canceled, A2ATaskState.failed}

        mod.A2ATaskState = A2ATaskState
        mod.A2AAgentSummary = A2AAgentSummary
        mod.A2AMessageResponse = A2AMessageResponse
        mod.A2ATaskStatusResponse = A2ATaskStatusResponse

        sys.modules["app.models.responses"] = mod

    # -----------------------------
    # app.utils.logging / app.utils.helpers
    # -----------------------------
    try:
        import app.utils.logging  # noqa: F401
    except Exception:
        pkg_utils = sys.modules.setdefault("app.utils", types.ModuleType("app.utils"))
        pkg_app = sys.modules.setdefault("app", types.ModuleType("app"))
        pkg_app.utils = pkg_utils

        mod = types.ModuleType("app.utils.logging")

        def get_logger(_name: str):
            import logging

            return logging.getLogger(_name)

        mod.get_logger = get_logger
        sys.modules["app.utils.logging"] = mod

    try:
        import app.utils.helpers  # noqa: F401
    except Exception:
        pkg_utils = sys.modules.setdefault("app.utils", types.ModuleType("app.utils"))
        pkg_app = sys.modules.setdefault("app", types.ModuleType("app"))
        pkg_app.utils = pkg_utils

        mod = types.ModuleType("app.utils.helpers")

        async def retry_async(fn, *, max_retries: int = 1, delay: float = 0.0):
            """Minimal retry helper stub used by unit tests."""
            last_exc = None
            for _ in range(max_retries + 1):
                try:
                    return await fn()
                except Exception as e:  # pragma: no cover
                    last_exc = e
                    if delay:
                        import asyncio

                        await asyncio.sleep(delay)
            raise last_exc  # type: ignore[misc]

        mod.retry_async = retry_async
        sys.modules["app.utils.helpers"] = mod

    # -----------------------------
    # a2a-sdk (optional in this sandbox)
    # -----------------------------
    try:
        import a2a  # noqa: F401
    except Exception:
        pkg_a2a = types.ModuleType("a2a")
        pkg_a2a_client = types.ModuleType("a2a.client")
        pkg_a2a_types = types.ModuleType("a2a.types")

        # Minimal stubs to satisfy imports in app.core.a2a_client
        class Role:
            user = "user"

        class TaskQueryParams:  # pragma: no cover
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        def create_text_message_object(*, role=None, content=None, **_):  # pragma: no cover
            return {"role": role, "content": content}

        class ClientConfig:  # pragma: no cover
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        class ClientFactory:  # pragma: no cover
            @staticmethod
            def create(*_, **__):
                raise RuntimeError("a2a-sdk stub: not available in test bundle")

        pkg_a2a_client.ClientConfig = ClientConfig
        pkg_a2a_client.ClientFactory = ClientFactory
        pkg_a2a_client.create_text_message_object = create_text_message_object

        pkg_a2a_types.TaskQueryParams = TaskQueryParams
        pkg_a2a_types.Role = Role

        sys.modules["a2a"] = pkg_a2a
        sys.modules["a2a.client"] = pkg_a2a_client
        sys.modules["a2a.types"] = pkg_a2a_types

    # -----------------------------
    # main module (FastAPI app entrypoint)
    # -----------------------------
    try:
        import main  # noqa: F401
    except Exception:
        from fastapi import FastAPI

        main_mod = types.ModuleType("main")
        app = FastAPI()
        try:
            from app.api.routes.a2a import router as a2a_router

            app.include_router(a2a_router)
        except Exception:
            # In minimal bundles, A2A router may not import; leave app empty.
            pass

        main_mod.app = app
        sys.modules["main"] = main_mod


_ensure_test_stubs()
