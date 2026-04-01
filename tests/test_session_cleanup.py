import asyncio
from datetime import timedelta
from datetime import datetime
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi import UploadFile
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from app.api.dependencies import get_session_manager
from app.api.routes.sessions import router as sessions_router
from app.core.exceptions import TemporaryUploadError
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
    manager._temporary_asset_store = TemporaryImageUploadStore(root_dir=tmp_path, ttl_seconds=3600)

    wrapper = _DummyWrapper()
    config = _session_config_stub()
    manager._sessions["s1"] = SessionData("s1", config, wrapper, tenant_id="t1")

    session_dir = tmp_path / "s1"
    session_dir.mkdir(parents=True, exist_ok=True)
    asset_path = session_dir / "asset-1.bin"
    asset_path.write_bytes(b"fake-image")
    manager._temporary_asset_store._assets["s1"] = {
        "asset-1": TemporaryImageUpload(
            asset_id="asset-1",
            session_id="s1",
            path=asset_path,
            mime_type="image/png",
            size_bytes=len(b"fake-image"),
            filename="cat.png",
            created_at=datetime.now(),
            metadata_path=session_dir / "asset-1.json",
        )
    }

    asyncio.run(manager.delete_session("s1", tenant_id="t1"))

    assert wrapper.closed == 1
    assert not asset_path.exists()
    assert not session_dir.exists()


def test_temporary_asset_store_reloads_metadata_after_in_memory_state_is_lost(tmp_path):
    store = TemporaryImageUploadStore(root_dir=tmp_path, ttl_seconds=3600)

    async def _exercise():
        upload = UploadFile(
            filename="cat.png",
            file=BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8),
            headers=Headers({"content-type": "image/png"}),
        )
        asset = await store.persist_image_upload(
            session_id="s1",
            upload=upload,
            index=0,
            current_total_bytes=0,
        )
        store._assets.clear()
        content = await store.read_image_bytes(session_id="s1", asset_id=asset.asset_id)
        return asset, content

    asset, content = asyncio.run(_exercise())

    assert content.startswith(b"\x89PNG\r\n\x1a\n")
    assert (tmp_path / "s1" / f"{asset.asset_id}.json").exists()


def test_temporary_asset_metadata_reload_uses_neutral_legacy_fallbacks(tmp_path):
    asset = TemporaryImageUpload.from_metadata_payload(
        session_dir=tmp_path,
        metadata_path=tmp_path / "asset-1.json",
        payload={
            "asset_id": "asset-1",
            "session_id": "s1",
            "path": "asset-1.bin",
            "mime_type": "application/octet-stream",
            "size_bytes": 12,
            "filename": "blob.bin",
            "created_at": datetime.now().isoformat(),
        },
    )

    assert asset.kind == "generic"
    assert asset.purpose == "attachment"


def test_temporary_asset_store_persist_upload_error_is_asset_neutral(tmp_path):
    store = TemporaryImageUploadStore(root_dir=tmp_path, ttl_seconds=3600)

    def _raise_runtime_error(**_kwargs):
        raise RuntimeError("boom")

    async def _exercise():
        upload = UploadFile(
            filename="cat.png",
            file=BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8),
            headers=Headers({"content-type": "image/png"}),
        )
        await store.persist_upload(
            session_id="s1",
            upload=upload,
            index=0,
            kind="image",
            purpose="input_image",
            content_validator=_raise_runtime_error,
        )

    with pytest.raises(TemporaryUploadError) as exc_info:
        asyncio.run(_exercise())

    assert "Failed to persist multipart asset at index 0" in str(exc_info.value)
    assert "kind=image" in str(exc_info.value)
    assert "purpose=input_image" in str(exc_info.value)
    assert "images[0]" not in str(exc_info.value)


def test_temporary_asset_store_sweep_expired_is_restart_safe_and_idempotent(tmp_path):
    store = TemporaryImageUploadStore(root_dir=tmp_path, ttl_seconds=1)

    async def _exercise():
        upload = UploadFile(
            filename="cat.png",
            file=BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8),
            headers=Headers({"content-type": "image/png"}),
        )
        asset = await store.persist_image_upload(
            session_id="s1",
            upload=upload,
            index=0,
            current_total_bytes=0,
        )
        expired_asset = TemporaryImageUpload(
            asset_id=asset.asset_id,
            session_id=asset.session_id,
            path=asset.path,
            mime_type=asset.mime_type,
            size_bytes=asset.size_bytes,
            filename=asset.filename,
            created_at=asset.created_at,
            kind=asset.kind,
            purpose=asset.purpose,
            metadata_path=asset.metadata_path,
            declared_content_type=asset.declared_content_type,
            storage_backend=asset.storage_backend,
            last_accessed_at=asset.created_at,
            expires_at=asset.created_at - timedelta(seconds=10),
        )
        store._assets = {"s1": {asset.asset_id: expired_asset}}
        store._write_metadata(expired_asset)
        store._assets.clear()
        await store.sweep_expired()
        await store.sweep_expired()
        return asset

    asset = asyncio.run(_exercise())

    assert not asset.path.exists()
    assert asset.metadata_path is not None
    assert not asset.metadata_path.exists()
    assert not (tmp_path / "s1").exists()
