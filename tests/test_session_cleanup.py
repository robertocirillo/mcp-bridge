import asyncio
from datetime import datetime
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_session_manager
from app.api.routes.sessions import router as sessions_router
from app.core.multimodal.temp_uploads import TemporaryImageUpload, TemporaryImageUploadStore
from app.core.runtime.mcp_wrapper import MCPWrapper
from app.core.sessions.manager import SessionData, SessionManager
from app.models.responses import (
    QueryOperationInput,
    QueryOperationInteraction,
    QueryOperationMetadata,
    QueryOperationResponse,
    QueryOperationStatus,
)


class _DummyWrapper:
    def __init__(self) -> None:
        self.closed = 0

    async def close(self) -> None:
        self.closed += 1


def _session_config_stub():
    return SimpleNamespace(
        mcp_servers={},
        llm_provider=SimpleNamespace(provider="ollama", model="dummy"),
    )


def test_wrapper_close_prefers_agent_close(monkeypatch):
    monkeypatch.setattr(MCPWrapper, "_import_dependencies", lambda self: None)

    wrapper = MCPWrapper(llm_provider="ollama", model="dummy")

    events: list[str] = []

    class _Agent:
        async def close(self):
            events.append("agent.close")

    class _Client:
        async def close_all_sessions(self):
            events.append("client.close_all_sessions")

    wrapper._agent = _Agent()
    wrapper._client = _Client()
    wrapper._initialized = True

    import asyncio

    asyncio.run(wrapper.close())

    assert events == ["agent.close"]
    assert wrapper._agent is None
    assert wrapper._client is None
    assert wrapper.is_initialized is False


def test_wrapper_close_falls_back_to_client_when_agent_close_fails(monkeypatch):
    monkeypatch.setattr(MCPWrapper, "_import_dependencies", lambda self: None)

    wrapper = MCPWrapper(llm_provider="ollama", model="dummy")

    events: list[str] = []

    class _Agent:
        async def close(self):
            events.append("agent.close")
            raise RuntimeError("boom")

    class _Client:
        async def close_all_sessions(self):
            events.append("client.close_all_sessions")

    wrapper._agent = _Agent()
    wrapper._client = _Client()
    wrapper._initialized = True

    import asyncio

    asyncio.run(wrapper.close())

    assert events == ["agent.close", "client.close_all_sessions"]
    assert wrapper._agent is None
    assert wrapper._client is None
    assert wrapper.is_initialized is False


def test_delete_session_closes_only_target_session():
    manager = SessionManager()

    wrapper_one = _DummyWrapper()
    wrapper_two = _DummyWrapper()
    config = _session_config_stub()

    manager._sessions["s1"] = SessionData("s1", config, wrapper_one, tenant_id="t1")
    manager._sessions["s2"] = SessionData("s2", config, wrapper_two, tenant_id="t1")

    import asyncio

    asyncio.run(manager.delete_session("s1", tenant_id="t1"))

    assert wrapper_one.closed == 1
    assert wrapper_two.closed == 0
    assert "s1" not in manager._sessions
    assert "s2" in manager._sessions


def test_cleanup_all_closes_all_sessions():
    manager = SessionManager()

    wrapper_one = _DummyWrapper()
    wrapper_two = _DummyWrapper()
    config = _session_config_stub()

    manager._sessions["s1"] = SessionData("s1", config, wrapper_one, tenant_id="t1")
    manager._sessions["s2"] = SessionData("s2", config, wrapper_two, tenant_id="t1")

    import asyncio

    asyncio.run(manager.cleanup_all())

    assert wrapper_one.closed == 1
    assert wrapper_two.closed == 1
    assert manager._sessions == {}


def test_delete_session_cleans_up_query_operations_and_tasks():
    manager = SessionManager()

    wrapper = _DummyWrapper()
    config = _session_config_stub()
    manager._sessions["s1"] = SessionData("s1", config, wrapper, tenant_id="t1")
    manager._query_operations["s1"] = {
        "op1": QueryOperationResponse(
            operation_id="op1",
            session_id="s1",
            status=QueryOperationStatus.running,
            metadata=QueryOperationMetadata(
                created_at=datetime.now(),
                updated_at=datetime.now(),
                request=QueryOperationInput(query="slow query"),
            ),
        )
    }

    cancellations: list[str] = []
    started = asyncio.Event()

    async def _pending():
        try:
            started.set()
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancellations.append("cancelled")
            raise

    async def _exercise():
        task = asyncio.create_task(_pending())
        manager._query_operation_tasks["s1"] = {"op1": task}
        await started.wait()
        await manager.delete_session("s1", tenant_id="t1")

    asyncio.run(_exercise())

    assert cancellations == ["cancelled"]
    assert wrapper.closed == 1
    assert "s1" not in manager._sessions
    assert "s1" not in manager._query_operations
    assert "s1" not in manager._query_operation_tasks


def test_delete_session_cancels_pending_elicitations():
    manager = SessionManager()

    wrapper = _DummyWrapper()
    config = _session_config_stub()
    manager._sessions["s1"] = SessionData("s1", config, wrapper, tenant_id="t1")
    manager._query_operations["s1"] = {
        "op1": QueryOperationResponse(
            operation_id="op1",
            session_id="s1",
            status=QueryOperationStatus.input_required,
            metadata=QueryOperationMetadata(
                created_at=datetime.now(),
                updated_at=datetime.now(),
                request=QueryOperationInput(query="needs human input"),
            ),
            requires_input=True,
            pending_interaction=QueryOperationInteraction(
                interaction_id="interaction-1",
                message="Provide details",
                requested_at=datetime.now(),
            ),
        )
    }

    async def _exercise():
        future = asyncio.get_running_loop().create_future()
        manager._pending_elicitations["s1"] = {
            "op1": SimpleNamespace(
                interaction_id="interaction-1",
                future=future,
                created_at=datetime.now(),
            )
        }
        await manager.delete_session("s1", tenant_id="t1")
        return future

    future = asyncio.run(_exercise())

    assert future.cancelled() is True
    assert wrapper.closed == 1
    assert "s1" not in manager._pending_elicitations


def test_delete_route_passes_tenant_id_to_background_task():
    manager = SessionManager()
    config = _session_config_stub()
    manager._sessions["s1"] = SessionData("s1", config, _DummyWrapper(), tenant_id="tenant-a")

    received: list[tuple[str, str | None]] = []

    async def _delete_session(session_id: str, tenant_id: str | None = None):
        received.append((session_id, tenant_id))

    manager.delete_session = _delete_session  # type: ignore[method-assign]

    app = FastAPI()
    app.include_router(sessions_router, prefix="/sessions")
    app.dependency_overrides[get_session_manager] = lambda: manager

    client = TestClient(app)
    response = client.delete("/sessions/s1", headers={"X-Tenant-Id": "tenant-a"})

    assert response.status_code == 200, response.text
    assert received == [("s1", "tenant-a")]


def test_delete_session_cleans_up_temporary_upload_assets(tmp_path):
    manager = SessionManager()
    manager._temporary_upload_store = TemporaryImageUploadStore(root_dir=tmp_path, ttl_seconds=3600)

    wrapper = _DummyWrapper()
    config = _session_config_stub()
    manager._sessions["s1"] = SessionData("s1", config, wrapper, tenant_id="t1")

    session_dir = tmp_path / "s1"
    session_dir.mkdir(parents=True, exist_ok=True)
    asset_path = session_dir / "asset-1"
    asset_path.write_bytes(b"fake-image")
    manager._temporary_upload_store._assets["s1"] = {
        "asset-1": TemporaryImageUpload(
            asset_id="asset-1",
            session_id="s1",
            path=asset_path,
            mime_type="image/png",
            size_bytes=len(b"fake-image"),
            filename="cat.png",
            created_at=datetime.now(),
        )
    }

    asyncio.run(manager.delete_session("s1", tenant_id="t1"))

    assert wrapper.closed == 1
    assert not asset_path.exists()
    assert not session_dir.exists()
