from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 16
PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
MULTIPART_DIRECT_TOOL_INVOCATION_NOT_SUPPORTED_MESSAGE = (
    "Multipart direct tool invocation with uploaded documents is not supported in 0.2.1. "
    "Use POST /sessions/{session_id}/query-operations with JSON arguments. "
    "If the MCP server is path-based, pass a file_path reachable by that server."
)


def _build_test_api(
    monkeypatch,
    *,
    upload_root: Path | None = None,
    release_async_query: asyncio.Event | None = None,
):
    from app.api.dependencies import get_session_manager
    from app.core.multimodal import QueryImageResolver, ensure_image_input_supported, ensure_pdf_input_supported
    from app.core.multimodal.model_query import build_model_query
    from app.core.multimodal.temp_uploads import TemporaryImageUploadStore
    from app.core.sessions.manager import SessionManager
    from config import settings

    mgr = SessionManager()
    if upload_root is not None:
        mgr._temporary_asset_store = TemporaryImageUploadStore(root_dir=upload_root, ttl_seconds=3600)

    monkeypatch.setattr("app.api.dependencies._session_manager", mgr, raising=False)
    monkeypatch.setattr(settings.multi_tenancy, "enabled", True, raising=False)
    monkeypatch.setattr(settings.multi_tenancy, "require_header", False, raising=False)
    monkeypatch.setattr(settings.multi_tenancy, "default_tenant_id", "default", raising=False)

    class DummyMCPWrapper:
        def __init__(
            self,
            llm_provider: str,
            model: str,
            api_key=None,
            base_url=None,
            temperature: float = 0.0,
            max_tokens=None,
            mcp_servers=None,
            max_steps: int = 30,
            verbose: bool = False,
            sandbox: bool = False,
            sandbox_options=None,
            disallowed_tools=None,
            use_server_manager: bool = False,
        ):
            _ = (api_key, base_url, temperature, max_tokens, verbose, sandbox, sandbox_options, disallowed_tools)
            _ = use_server_manager
            self.llm_provider = llm_provider
            self.model = model
            self.mcp_servers = mcp_servers or {}
            self.has_mcp_servers = bool(self.mcp_servers)
            self.max_steps = max_steps
            self.steps_used = 0
            self.last_server_used = None
            self.tenant_id = None
            self.run_id = None
            self.session_id = None
            self._query_image_resolver = QueryImageResolver(upload_store=mgr.temporary_upload_store)

        def set_context(self, *, tenant_id=None, run_id=None, session_id=None):
            self.tenant_id = tenant_id
            self.run_id = run_id
            self.session_id = session_id

        def set_elicitation_handler(self, _handler):
            return None

        def set_task_status_handler(self, _handler):
            return None

        async def initialize(self):
            return None

        async def close(self):
            return None

        def query_operation_scope(self, **_kwargs):
            class _Scope:
                async def __aenter__(self_inner):
                    return None

                async def __aexit__(self_inner, exc_type, exc, tb):
                    return False

            return _Scope()

        async def run_query(self, query, max_steps=None, server_name=None) -> str:
            self.steps_used = max_steps or 2
            self.last_server_used = server_name

            if isinstance(query, str):
                return f"QUERY:{query}"

            if query.images:
                ensure_image_input_supported(provider=self.llm_provider, model=self.model)
                if release_async_query is not None and any(image.source_type == "upload" for image in query.images):
                    await release_async_query.wait()
            if query.documents:
                ensure_pdf_input_supported(provider=self.llm_provider, model=self.model)
                if release_async_query is not None and any(document.source_type == "upload" for document in query.documents):
                    await release_async_query.wait()

            prepared = await self._query_image_resolver.resolve(query, session_id=self.session_id)
            message = build_model_query(prepared, provider=self.llm_provider)
            blocks = getattr(message, "content", [])
            image_count = sum(1 for block in blocks if block.get("type") == "image_url")
            document_count = sum(1 for block in blocks if block.get("type") in {"file", "document"})
            text_values = [block.get("text", "") for block in blocks if block.get("type") == "text"]
            return f"TEXT:{' '.join(text_values)}|IMAGES:{image_count}|PDFS:{document_count}"

        async def call_tool(self, tool_name, arguments=None, server_name=None):
            self.last_server_used = server_name
            return {
                "tool_name": tool_name,
                "server_name": server_name,
                "arguments": arguments or {},
            }

    monkeypatch.setattr("app.core.sessions.manager.MCPWrapper", DummyMCPWrapper)

    from app.api.routes.queries import router as queries_router
    from app.api.routes.sessions import router as sessions_router

    app = FastAPI()
    app.include_router(sessions_router, prefix="/sessions")
    app.include_router(queries_router, prefix="/sessions")
    app.dependency_overrides[get_session_manager] = lambda: mgr
    return app, mgr


def _build_test_client(monkeypatch, **kwargs):
    app, mgr = _build_test_api(monkeypatch, **kwargs)
    return TestClient(app), mgr


def _create_session(client: TestClient, *, provider: str = "ollama", model: str = "llava") -> str:
    payload = {
        "llm_provider": {"provider": provider, "model": model, "temperature": 0},
        "mcp_servers": {},
    }
    response = client.post("/sessions", json=payload, headers={"X-Tenant-Id": "tenant-a"})
    assert response.status_code == 200, response.text
    return response.json()["session_id"]


async def _create_session_async(client: AsyncClient, *, provider: str = "ollama", model: str = "llava") -> str:
    payload = {
        "llm_provider": {"provider": provider, "model": model, "temperature": 0},
        "mcp_servers": {},
    }
    response = await client.post("/sessions", json=payload, headers={"X-Tenant-Id": "tenant-a"})
    assert response.status_code == 200, response.text
    return response.json()["session_id"]


async def _wait_for_operation_status(
    client: AsyncClient,
    *,
    session_id: str,
    operation_id: str,
    expected_status: str,
) -> dict[str, object]:
    deadline = time.monotonic() + 2.0
    last_body = None
    while time.monotonic() < deadline:
        response = await client.get(
            f"/sessions/{session_id}/query-operations/{operation_id}",
            headers={"X-Tenant-Id": "tenant-a"},
        )
        assert response.status_code == 200, response.text
        last_body = response.json()
        if last_body["status"] == expected_status:
            return last_body
        await asyncio.sleep(0.01)

    raise AssertionError(f"Operation {operation_id} did not reach status {expected_status}: {last_body}")


def test_multipart_query_accepts_text_plus_one_image(monkeypatch):
    client, _mgr = _build_test_client(monkeypatch)
    session_id = _create_session(client)

    response = client.post(
        f"/sessions/{session_id}/query-multipart",
        data={"text": "describe this image", "max_steps": "7"},
        files=[("images", ("cat.png", PNG_BYTES, "image/png"))],
        headers={"X-Tenant-Id": "tenant-a"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["session_id"] == session_id
    assert body["result"] == "TEXT:describe this image|IMAGES:1|PDFS:0"
    assert body["steps_used"] == 7


def test_multipart_query_accepts_text_plus_one_image_without_optional_fields(monkeypatch):
    client, _mgr = _build_test_client(monkeypatch)
    session_id = _create_session(client)

    response = client.post(
        f"/sessions/{session_id}/query-multipart",
        data={"text": "describe this image"},
        files=[("images", ("cat.png", PNG_BYTES, "image/png"))],
        headers={"X-Tenant-Id": "tenant-a"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result"] == "TEXT:describe this image|IMAGES:1|PDFS:0"
    assert body["steps_used"] == 2


def test_multipart_query_normalizes_swagger_empty_optional_fields(monkeypatch):
    client, _mgr = _build_test_client(monkeypatch)
    session_id = _create_session(client)

    response = client.post(
        f"/sessions/{session_id}/query-multipart",
        data={
            "text": "describe this image",
            "max_steps": "",
            "documents": "",
        },
        files=[("images", ("cat.png", PNG_BYTES, "image/png"))],
        headers={"X-Tenant-Id": "tenant-a"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result"] == "TEXT:describe this image|IMAGES:1|PDFS:0"
    assert body["steps_used"] == 2


def test_multipart_query_rejects_invalid_max_steps_string(monkeypatch):
    client, _mgr = _build_test_client(monkeypatch)
    session_id = _create_session(client)

    response = client.post(
        f"/sessions/{session_id}/query-multipart",
        data={"text": "describe this image", "max_steps": "abc"},
        files=[("images", ("cat.png", PNG_BYTES, "image/png"))],
        headers={"X-Tenant-Id": "tenant-a"},
    )

    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "MCP_SCHEMA_ERROR"
    assert detail["operation"] == "execute_multipart_query"
    assert detail["message"] == "Field 'max_steps' must be a valid integer"


def test_multipart_query_accepts_multiple_images_within_limits(monkeypatch):
    client, _mgr = _build_test_client(monkeypatch)
    session_id = _create_session(client)

    response = client.post(
        f"/sessions/{session_id}/query-multipart",
        data={"text": "compare them"},
        files=[
            ("images", ("first.png", PNG_BYTES, "image/png")),
            ("images", ("second.jpg", JPEG_BYTES, "image/jpeg")),
        ],
        headers={"X-Tenant-Id": "tenant-a"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["result"] == "TEXT:compare them|IMAGES:2|PDFS:0"


def test_multipart_query_cleans_up_temporary_uploads_after_completion(monkeypatch, tmp_path):
    client, _mgr = _build_test_client(monkeypatch, upload_root=tmp_path)
    session_id = _create_session(client)

    response = client.post(
        f"/sessions/{session_id}/query-multipart",
        data={"text": "describe this image"},
        files=[("images", ("cat.png", PNG_BYTES, "image/png"))],
        headers={"X-Tenant-Id": "tenant-a"},
    )

    assert response.status_code == 200, response.text
    assert not (tmp_path / session_id).exists()


def test_multipart_query_accepts_pdf_for_pdf_capable_model(monkeypatch):
    client, _mgr = _build_test_client(monkeypatch)
    session_id = _create_session(client, provider="openai", model="gpt-4o")

    response = client.post(
        f"/sessions/{session_id}/query-multipart",
        data={"text": "summarize this pdf"},
        files=[("documents", ("report.pdf", PDF_BYTES, "application/pdf"))],
        headers={"X-Tenant-Id": "tenant-a"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["result"] == "TEXT:summarize this pdf|IMAGES:0|PDFS:1"


def test_multipart_query_rejects_pdf_for_non_pdf_model(monkeypatch):
    client, _mgr = _build_test_client(monkeypatch)
    session_id = _create_session(client, provider="ollama", model="llava")

    response = client.post(
        f"/sessions/{session_id}/query-multipart",
        data={"text": "summarize this pdf"},
        files=[("documents", ("report.pdf", PDF_BYTES, "application/pdf"))],
        headers={"X-Tenant-Id": "tenant-a"},
    )

    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "MCP_PDF_INPUT_NOT_SUPPORTED"
    assert detail["operation"] == "execute_multipart_query"


def test_multipart_query_rejects_non_pdf_upload_with_clear_error(monkeypatch):
    client, _mgr = _build_test_client(monkeypatch)
    session_id = _create_session(client, provider="openai", model="gpt-4o")

    response = client.post(
        f"/sessions/{session_id}/query-multipart",
        data={"text": "summarize this"},
        files=[("documents", ("notes.txt", b"not-a-pdf", "text/plain"))],
        headers={"X-Tenant-Id": "tenant-a"},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "MCP_SCHEMA_ERROR"
    assert detail["operation"] == "execute_multipart_query"
    assert "not a supported PDF" in detail["message"]


def test_query_operations_multipart_openapi_is_query_only(monkeypatch):
    client, _mgr = _build_test_client(monkeypatch)

    response = client.get("/openapi.json")

    assert response.status_code == 200, response.text
    body = response.json()
    multipart_operation = body["paths"]["/sessions/{session_id}/query-operations-multipart"]["post"]
    properties = multipart_operation["requestBody"]["content"]["multipart/form-data"]["schema"]["properties"]

    assert "text" in properties
    assert "max_steps" in properties
    assert "server_name" in properties
    assert "images" in properties
    assert "documents" in properties
    assert "tool_name" not in properties
    assert "arguments" not in properties


@pytest.mark.asyncio
async def test_multipart_query_operation_accepts_text_plus_one_image(monkeypatch, tmp_path):
    app, _mgr = _build_test_api(monkeypatch, upload_root=tmp_path)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        session_id = await _create_session_async(client)

        create_response = await client.post(
            f"/sessions/{session_id}/query-operations-multipart",
            data={"text": "describe this image", "max_steps": "7"},
            files=[("images", ("cat.png", PNG_BYTES, "image/png"))],
            headers={"X-Tenant-Id": "tenant-a"},
        )

        assert create_response.status_code == 200, create_response.text
        create_body = create_response.json()
        assert create_body["status"] == "queued"
        assert create_body["metadata"]["request"]["input"]["text_present"] is True
        assert create_body["metadata"]["request"]["input"]["image_count"] == 1
        image_summary = create_body["metadata"]["request"]["input"]["images"][0]
        assert image_summary["source_type"] == "upload"
        assert image_summary["mime_type"] == "image/png"
        assert image_summary["data_size_bytes"] == len(PNG_BYTES)

        final_body = await _wait_for_operation_status(
            client,
            session_id=session_id,
            operation_id=create_body["operation_id"],
            expected_status="completed",
        )

        assert final_body["result"]["result"] == "TEXT:describe this image|IMAGES:1|PDFS:0"
        assert final_body["result"]["steps_used"] == 7


@pytest.mark.asyncio
async def test_multipart_query_operation_normalizes_swagger_empty_optional_fields(monkeypatch, tmp_path):
    app, _mgr = _build_test_api(monkeypatch, upload_root=tmp_path)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        session_id = await _create_session_async(client)

        create_response = await client.post(
            f"/sessions/{session_id}/query-operations-multipart",
            data={
                "text": "describe this image",
                "max_steps": "",
                "documents": "",
                "tool_name": "",
                "arguments": "",
            },
            files=[("images", ("cat.png", PNG_BYTES, "image/png"))],
            headers={"X-Tenant-Id": "tenant-a"},
        )

        assert create_response.status_code == 200, create_response.text
        create_body = create_response.json()
        final_body = await _wait_for_operation_status(
            client,
            session_id=session_id,
            operation_id=create_body["operation_id"],
            expected_status="completed",
        )

        assert final_body["result"]["result"] == "TEXT:describe this image|IMAGES:1|PDFS:0"
        assert final_body["result"]["steps_used"] == 2


@pytest.mark.asyncio
async def test_multipart_query_operation_accepts_multiple_images_within_limits(monkeypatch, tmp_path):
    app, _mgr = _build_test_api(monkeypatch, upload_root=tmp_path)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        session_id = await _create_session_async(client)

        create_response = await client.post(
            f"/sessions/{session_id}/query-operations-multipart",
            data={"text": "compare them"},
            files=[
                ("images", ("first.png", PNG_BYTES, "image/png")),
                ("images", ("second.jpg", JPEG_BYTES, "image/jpeg")),
            ],
            headers={"X-Tenant-Id": "tenant-a"},
        )

        assert create_response.status_code == 200, create_response.text
        create_body = create_response.json()
        final_body = await _wait_for_operation_status(
            client,
            session_id=session_id,
            operation_id=create_body["operation_id"],
            expected_status="completed",
        )

        assert final_body["result"]["result"] == "TEXT:compare them|IMAGES:2|PDFS:0"


@pytest.mark.asyncio
async def test_multipart_query_operation_cleans_up_temporary_uploads_after_completion(monkeypatch, tmp_path):
    release_async_query = asyncio.Event()
    app, _mgr = _build_test_api(
        monkeypatch,
        upload_root=tmp_path,
        release_async_query=release_async_query,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        session_id = await _create_session_async(client)

        create_response = await client.post(
            f"/sessions/{session_id}/query-operations-multipart",
            data={"text": "describe this image"},
            files=[("images", ("cat.png", PNG_BYTES, "image/png"))],
            headers={"X-Tenant-Id": "tenant-a"},
        )
        assert create_response.status_code == 200, create_response.text
        create_body = create_response.json()

        await _wait_for_operation_status(
            client,
            session_id=session_id,
            operation_id=create_body["operation_id"],
            expected_status="running",
        )

        session_dir = tmp_path / session_id
        assert session_dir.exists()
        session_files = list(session_dir.iterdir())
        assert len(session_files) == 2
        assert any(path.suffix == ".bin" for path in session_files)
        assert any(path.suffix == ".json" for path in session_files)

        release_async_query.set()

        await _wait_for_operation_status(
            client,
            session_id=session_id,
            operation_id=create_body["operation_id"],
            expected_status="completed",
        )

        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and session_dir.exists():
            await asyncio.sleep(0.01)

        assert not session_dir.exists()


@pytest.mark.asyncio
async def test_multipart_query_operation_accepts_pdf_for_pdf_capable_model(monkeypatch, tmp_path):
    app, _mgr = _build_test_api(monkeypatch, upload_root=tmp_path)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        session_id = await _create_session_async(client, provider="openai", model="gpt-4o")

        create_response = await client.post(
            f"/sessions/{session_id}/query-operations-multipart",
            data={"text": "summarize this pdf"},
            files=[("documents", ("report.pdf", PDF_BYTES, "application/pdf"))],
            headers={"X-Tenant-Id": "tenant-a"},
        )

        assert create_response.status_code == 200, create_response.text
        create_body = create_response.json()
        assert create_body["metadata"]["request"]["input"]["document_count"] == 1
        document_summary = create_body["metadata"]["request"]["input"]["documents"][0]
        assert document_summary["mime_type"] == "application/pdf"
        assert document_summary["data_size_bytes"] == len(PDF_BYTES)

        final_body = await _wait_for_operation_status(
            client,
            session_id=session_id,
            operation_id=create_body["operation_id"],
            expected_status="completed",
        )

        assert final_body["result"]["result"] == "TEXT:summarize this pdf|IMAGES:0|PDFS:1"


@pytest.mark.asyncio
async def test_multipart_query_operation_rejects_direct_tool_invocation_with_documents(monkeypatch, tmp_path):
    app, _mgr = _build_test_api(monkeypatch, upload_root=tmp_path)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        session_id = await _create_session_async(client, provider="ollama", model="llava")

        response = await client.post(
            f"/sessions/{session_id}/query-operations-multipart",
            data={
                "tool_name": "analyze_pdf",
                "server_name": "filesystem",
                "arguments": '{"topic":"contracts"}',
            },
            files=[("documents", ("report.pdf", PDF_BYTES, "application/pdf"))],
            headers={"X-Tenant-Id": "tenant-a"},
        )

        assert response.status_code == 400, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "MCP_SCHEMA_ERROR"
        assert detail["operation"] == "create_multipart_query_operation"
        assert detail["message"] == MULTIPART_DIRECT_TOOL_INVOCATION_NOT_SUPPORTED_MESSAGE
        assert not (tmp_path / session_id).exists()


@pytest.mark.asyncio
async def test_multipart_query_operation_rejects_tool_name_without_documents(monkeypatch, tmp_path):
    app, _mgr = _build_test_api(monkeypatch, upload_root=tmp_path)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        session_id = await _create_session_async(client, provider="ollama", model="llava")

        response = await client.post(
            f"/sessions/{session_id}/query-operations-multipart",
            data={
                "tool_name": "analyze_pdf",
                "server_name": "filesystem",
                "arguments": '{"topic":"contracts"}',
            },
            headers={"X-Tenant-Id": "tenant-a"},
        )

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert detail["code"] == "MCP_SCHEMA_ERROR"
        assert detail["operation"] == "create_multipart_query_operation"
        assert detail["message"] == MULTIPART_DIRECT_TOOL_INVOCATION_NOT_SUPPORTED_MESSAGE
        assert not (tmp_path / session_id).exists()


@pytest.mark.asyncio
async def test_multipart_query_operation_rejects_arguments_without_tool_name(monkeypatch, tmp_path):
    app, _mgr = _build_test_api(monkeypatch, upload_root=tmp_path)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        session_id = await _create_session_async(client, provider="ollama", model="llava")

        response = await client.post(
            f"/sessions/{session_id}/query-operations-multipart",
            data={
                "server_name": "filesystem",
                "arguments": '{"topic":"contracts"}',
            },
            headers={"X-Tenant-Id": "tenant-a"},
        )

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert detail["code"] == "MCP_SCHEMA_ERROR"
        assert detail["operation"] == "create_multipart_query_operation"
        assert detail["message"] == MULTIPART_DIRECT_TOOL_INVOCATION_NOT_SUPPORTED_MESSAGE
        assert not (tmp_path / session_id).exists()


@pytest.mark.asyncio
async def test_multipart_query_operation_rejects_non_pdf_upload_with_clear_error(monkeypatch, tmp_path):
    app, _mgr = _build_test_api(monkeypatch, upload_root=tmp_path)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        session_id = await _create_session_async(client, provider="openai", model="gpt-4o")

        response = await client.post(
            f"/sessions/{session_id}/query-operations-multipart",
            data={"text": "summarize this"},
            files=[("documents", ("notes.txt", b"not-a-pdf", "text/plain"))],
            headers={"X-Tenant-Id": "tenant-a"},
        )

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert detail["code"] == "MCP_SCHEMA_ERROR"
        assert detail["operation"] == "create_multipart_query_operation"
        assert "not a supported PDF" in detail["message"]


def test_multipart_query_rejects_non_image_upload_with_clear_error(monkeypatch):
    client, _mgr = _build_test_client(monkeypatch)
    session_id = _create_session(client)

    response = client.post(
        f"/sessions/{session_id}/query-multipart",
        data={"text": "describe this"},
        files=[("images", ("notes.txt", b"not-an-image", "text/plain"))],
        headers={"X-Tenant-Id": "tenant-a"},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "MCP_SCHEMA_ERROR"
    assert detail["operation"] == "execute_multipart_query"
    assert "not a supported image" in detail["message"]


@pytest.mark.asyncio
async def test_multipart_query_operation_rejects_non_image_upload_with_clear_error(monkeypatch, tmp_path):
    app, _mgr = _build_test_api(monkeypatch, upload_root=tmp_path)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        session_id = await _create_session_async(client)

        response = await client.post(
            f"/sessions/{session_id}/query-operations-multipart",
            data={"text": "describe this"},
            files=[("images", ("notes.txt", b"not-an-image", "text/plain"))],
            headers={"X-Tenant-Id": "tenant-a"},
        )

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert detail["code"] == "MCP_SCHEMA_ERROR"
        assert detail["operation"] == "create_multipart_query_operation"
        assert "not a supported image" in detail["message"]


def test_multipart_query_rejects_images_for_text_only_model(monkeypatch):
    client, _mgr = _build_test_client(monkeypatch)
    session_id = _create_session(client, model="llama3.1")

    response = client.post(
        f"/sessions/{session_id}/query-multipart",
        data={"text": "describe this image"},
        files=[("images", ("cat.png", PNG_BYTES, "image/png"))],
        headers={"X-Tenant-Id": "tenant-a"},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "MCP_IMAGE_INPUT_NOT_SUPPORTED"
    assert detail["operation"] == "execute_multipart_query"
    assert detail["reason"] == "text_only"


@pytest.mark.asyncio
async def test_multipart_query_operation_fails_with_coherent_error_for_text_only_model(monkeypatch, tmp_path):
    app, _mgr = _build_test_api(monkeypatch, upload_root=tmp_path)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        session_id = await _create_session_async(client, model="llama3.1")

        create_response = await client.post(
            f"/sessions/{session_id}/query-operations-multipart",
            data={"text": "describe this image"},
            files=[("images", ("cat.png", PNG_BYTES, "image/png"))],
            headers={"X-Tenant-Id": "tenant-a"},
        )

        assert create_response.status_code == 400, create_response.text
        detail = create_response.json()["detail"]
        assert detail["code"] == "MCP_IMAGE_INPUT_NOT_SUPPORTED"
        assert detail["reason"] == "text_only"
        assert not (tmp_path / session_id).exists()


def test_existing_json_query_route_still_works(monkeypatch):
    client, _mgr = _build_test_client(monkeypatch)
    session_id = _create_session(client, model="dummy")

    response = client.post(
        f"/sessions/{session_id}/query",
        json={"query": "hello"},
        headers={"X-Tenant-Id": "tenant-a"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["result"] == "QUERY:hello"


@pytest.mark.asyncio
async def test_existing_json_query_operation_route_still_works(monkeypatch, tmp_path):
    app, _mgr = _build_test_api(monkeypatch, upload_root=tmp_path)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        session_id = await _create_session_async(client, model="dummy")

        create_response = await client.post(
            f"/sessions/{session_id}/query-operations",
            json={"query": "hello async"},
            headers={"X-Tenant-Id": "tenant-a"},
        )

        assert create_response.status_code == 200, create_response.text
        create_body = create_response.json()
        final_body = await _wait_for_operation_status(
            client,
            session_id=session_id,
            operation_id=create_body["operation_id"],
            expected_status="completed",
        )

        assert final_body["result"]["result"] == "QUERY:hello async"


@pytest.mark.asyncio
async def test_existing_json_query_operation_direct_tool_invocation_still_works(monkeypatch, tmp_path):
    app, _mgr = _build_test_api(monkeypatch, upload_root=tmp_path)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        session_id = await _create_session_async(client, model="dummy")

        create_response = await client.post(
            f"/sessions/{session_id}/query-operations",
            json={
                "server_name": "filesystem",
                "tool_name": "analyze_pdf",
                "arguments": {"topic": "contracts"},
            },
            headers={"X-Tenant-Id": "tenant-a"},
        )

        assert create_response.status_code == 200, create_response.text
        create_body = create_response.json()
        assert create_body["metadata"]["request"] == {
            "server_name": "filesystem",
            "tool_name": "analyze_pdf",
            "arguments": {"topic": "contracts"},
        }

        final_body = await _wait_for_operation_status(
            client,
            session_id=session_id,
            operation_id=create_body["operation_id"],
            expected_status="completed",
        )

        assert final_body["result"]["result"] == {
            "tool_name": "analyze_pdf",
            "server_name": "filesystem",
            "arguments": {"topic": "contracts"},
        }
