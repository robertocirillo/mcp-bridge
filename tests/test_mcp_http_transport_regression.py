from types import SimpleNamespace

import pytest

from app.core.mcp_task_runtime import install_task_notification_runtime_patch
from app.core.session_manager import SessionManager
from app.models.requests import SessionCreateRequest


def test_session_config_preserves_http_transport_metadata() -> None:
    request = SessionCreateRequest.model_validate(
        {
            "llm_provider": {"provider": "ollama", "model": "dummy", "temperature": 0},
            "mcp_servers": {
                "simple_task_interactive": {
                    "transport": "streamable-http",
                    "url": "http://127.0.0.1:8010/mcp",
                    "headers": {"X-Test": "true"},
                    "timeout": 12,
                }
            },
        }
    )

    converted = SessionManager._convert_mcp_servers(request.mcp_servers)

    assert converted == {
        "simple_task_interactive": {
            "transport": "streamable-http",
            "url": "http://127.0.0.1:8010/mcp",
            "headers": {"X-Test": "true"},
            "timeout": 12,
        }
    }


@pytest.mark.asyncio
async def test_explicit_streamable_http_transport_skips_sse_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    import mcp_use.client as client_module
    import mcp_use.connectors.http as http_module

    install_task_notification_runtime_patch()

    attempts = {"streamable": 0, "sse": 0}

    class _StreamableHttpConnectionManager:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def start(self):
            attempts["streamable"] += 1
            return object(), object()

        async def stop(self) -> None:
            return None

    class _SseConnectionManager:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def start(self):
            attempts["sse"] += 1
            raise AssertionError("SSE fallback should not be attempted for pinned streamable-http transport")

        async def stop(self) -> None:
            return None

    class _ClientSession:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def initialize(self):
            return SimpleNamespace(
                capabilities=SimpleNamespace(
                    tools=False,
                    resources=False,
                    prompts=False,
                )
            )

    monkeypatch.setattr(http_module, "StreamableHttpConnectionManager", _StreamableHttpConnectionManager)
    monkeypatch.setattr(http_module, "SseConnectionManager", _SseConnectionManager)
    monkeypatch.setattr(http_module, "ClientSession", _ClientSession)

    connector = client_module.create_connector_from_config(
        {
            "transport": "streamable-http",
            "url": "http://127.0.0.1:8010/mcp",
        }
    )

    await connector.connect()

    assert getattr(connector, "_bridge_transport", None) == "streamable-http"
    assert connector.transport_type == "streamable HTTP"
    assert attempts == {"streamable": 1, "sse": 0}


@pytest.mark.asyncio
async def test_explicit_streamable_http_transport_propagates_streamable_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import mcp_use.client as client_module
    import mcp_use.connectors.http as http_module

    install_task_notification_runtime_patch()

    attempts = {"streamable": 0, "sse": 0}

    class _StreamableHttpConnectionManager:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def start(self):
            attempts["streamable"] += 1
            raise RuntimeError("streamable init failed")

        async def stop(self) -> None:
            return None

    class _SseConnectionManager:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def start(self):
            attempts["sse"] += 1
            raise AssertionError("SSE fallback should not run when streamable-http is pinned")

        async def stop(self) -> None:
            return None

    monkeypatch.setattr(http_module, "StreamableHttpConnectionManager", _StreamableHttpConnectionManager)
    monkeypatch.setattr(http_module, "SseConnectionManager", _SseConnectionManager)

    connector = client_module.create_connector_from_config(
        {
            "transport": "streamable-http",
            "url": "http://127.0.0.1:8010/mcp",
        }
    )

    with pytest.raises(RuntimeError, match="streamable init failed"):
        await connector.connect()

    assert attempts == {"streamable": 1, "sse": 0}
