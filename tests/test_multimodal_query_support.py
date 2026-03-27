import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import replace

import httpx
import pytest
from pydantic import ValidationError

from app.core.model_query import build_model_query
from app.core.multimodal_image_fetch import RemoteImageFetcher
from app.core.multimodal_image_resolver import QueryImageResolver
from app.core.sessions.query_operation_store import serialize_query_operation_error
from app.models.requests import (
    MAX_BASE64_IMAGE_DATA_LENGTH,
    ImageInput,
    QueryInputPayload,
    QueryOperationCreateRequest,
    QueryRequest,
    SessionCreateRequest,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
PNG_BASE64 = "iVBORw0KGgoAAAAAAAAAAAAAAAAAAAAA"


def _build_remote_image_fetcher(handler, monkeypatch: pytest.MonkeyPatch) -> RemoteImageFetcher:
    transport = httpx.MockTransport(handler)
    fetcher = RemoteImageFetcher(
        client_factory=lambda: httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(5.0),
        )
    )

    async def _resolve_host_ips(_hostname: str) -> set[str]:
        return {"93.184.216.34"}

    monkeypatch.setattr(fetcher, "_resolve_host_ips", _resolve_host_ips)
    return fetcher


def test_query_request_validation_supports_legacy_and_structured_inputs():
    legacy = QueryRequest.model_validate({"query": "  hello  "})
    assert legacy.query == "hello"
    assert legacy.input is None

    structured = QueryRequest.model_validate(
        {
            "input": {
                "text": "describe this image",
                "images": [
                    {
                        "source_type": "url",
                        "url": "https://example.com/cat.png",
                    }
                ],
            }
        }
    )
    assert structured.query is None
    assert structured.input is not None
    assert structured.input.text == "describe this image"
    assert len(structured.input.images) == 1


def test_base64_image_within_limit_is_valid():
    image = ImageInput.model_validate(
        {
            "source_type": "base64",
            "mime_type": "image/png",
            "data": "A" * MAX_BASE64_IMAGE_DATA_LENGTH,
        }
    )

    assert image.data == "A" * MAX_BASE64_IMAGE_DATA_LENGTH


def test_base64_image_over_limit_is_rejected():
    with pytest.raises(ValidationError) as exc_info:
        ImageInput.model_validate(
            {
                "source_type": "base64",
                "mime_type": "image/png",
                "data": "A" * (MAX_BASE64_IMAGE_DATA_LENGTH + 1),
            }
        )

    assert f"maximum supported base64 length of {MAX_BASE64_IMAGE_DATA_LENGTH} characters" in str(exc_info.value)


@pytest.mark.parametrize(
    "payload, expected_message",
    [
        (
            {"input": {"images": [{"source_type": "url"}]}},
            "Field 'url' is required when source_type='url'",
        ),
        (
            {
                "input": {
                    "images": [
                        {
                            "source_type": "base64",
                            "data": "ZmFrZQ==",
                        }
                    ]
                }
            },
            "Field 'mime_type' is required when source_type='base64'",
        ),
        (
            {},
            "At least one of 'query' or 'input' must be provided",
        ),
    ],
)
def test_query_request_validation_rejects_invalid_multimodal_shapes(payload, expected_message):
    with pytest.raises(ValidationError) as exc_info:
        QueryRequest.model_validate(payload)

    assert expected_message in str(exc_info.value)


def test_query_request_whitespace_only_query_without_input_is_rejected():
    with pytest.raises(ValidationError) as exc_info:
        QueryRequest.model_validate({"query": "   "})

    assert "At least one of 'query' or 'input' must be provided" in str(exc_info.value)


def test_query_request_whitespace_only_query_with_valid_input_uses_input():
    request = QueryRequest.model_validate(
        {
            "query": "   ",
            "input": {
                "text": "  describe this image  ",
            },
        }
    )

    assert request.query is None
    assert request.input is not None
    assert request.input.text == "describe this image"


def test_query_operation_create_request_whitespace_only_query_without_input_or_tool_is_rejected():
    with pytest.raises(ValidationError) as exc_info:
        QueryOperationCreateRequest.model_validate({"query": "   "})

    assert "Exactly one of query/input or 'tool_name' must be provided" in str(exc_info.value)


@pytest.mark.asyncio
async def test_build_model_query_converts_structured_payload_to_human_message(monkeypatch):
    payload = QueryInputPayload.model_validate(
        {
            "text": "what is shown here?",
            "images": [
                {
                    "source_type": "url",
                    "url": "https://example.com/dog.png",
                },
                {
                    "source_type": "base64",
                    "mime_type": "image/png",
                    "data": "ZmFrZV9pbWFnZQ==",
                },
            ],
        }
    )

    async def _remote_png(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            headers={"content-type": "image/png", "content-length": str(len(PNG_BYTES))},
            content=PNG_BYTES,
        )

    resolver = QueryImageResolver(
        remote_image_fetcher=_build_remote_image_fetcher(_remote_png, monkeypatch)
    )
    message = build_model_query(await resolver.resolve(payload))
    assert hasattr(message, "content")
    assert message.content == [
        {"type": "text", "text": "what is shown here?"},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{PNG_BASE64}"}},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,ZmFrZV9pbWFnZQ=="}},
    ]


def test_image_input_repr_hides_base64_payload():
    image = ImageInput.model_validate(
        {
            "source_type": "base64",
            "mime_type": "image/png",
            "data": "ZmFrZV9zZWNyZXRfYmxvYg==",
        }
    )

    assert "ZmFrZV9zZWNyZXRfYmxvYg==" not in repr(image)


def test_query_input_payload_text_is_trimmed():
    payload = QueryInputPayload.model_validate({"text": "  hello input  "})

    assert payload.text == "hello input"


class _DummyAgent:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.steps_used = 0
        self.last_server_used = None

    async def run(self, *, query, max_steps=None, server_name=None):
        self.calls.append(
            {
                "query": query,
                "max_steps": max_steps,
                "server_name": server_name,
            }
        )
        self.steps_used = 2
        self.last_server_used = server_name
        return "MODEL_RESULT"


def _build_wrapper(monkeypatch: pytest.MonkeyPatch):
    from app.core.runtime.mcp_wrapper import MCPWrapper

    monkeypatch.setattr(MCPWrapper, "_import_dependencies", lambda self: None)
    wrapper = MCPWrapper(
        llm_provider="ollama",
        model="dummy",
        mcp_servers={"alpha": {"url": "http://example.com/mcp"}},
    )
    wrapper.before_model_guardrails = []
    wrapper.after_model_guardrails = []
    wrapper._initialized = True
    wrapper._agent = _DummyAgent()
    return wrapper


@pytest.mark.asyncio
async def test_wrapper_run_query_keeps_legacy_string_queries_unchanged(monkeypatch):
    wrapper = _build_wrapper(monkeypatch)

    result = await wrapper.run_query("hello legacy", max_steps=4, server_name="alpha")

    assert result == "MODEL_RESULT"
    sent_query = wrapper._agent.calls[0]["query"]
    assert sent_query == "hello legacy"


@pytest.mark.asyncio
async def test_wrapper_run_query_allows_image_only_and_guardrails_only_see_text(monkeypatch):
    from app.core.runtime.mcp_wrapper import GuardrailContext

    wrapper = _build_wrapper(monkeypatch)
    seen_queries: list[str | None] = []

    def _record_guardrail(ctx: GuardrailContext) -> GuardrailContext:
        seen_queries.append(ctx.query)
        return ctx

    wrapper.before_model_guardrails = [_record_guardrail]
    wrapper._query_image_resolver = QueryImageResolver(
        remote_image_fetcher=_build_remote_image_fetcher(
            lambda _request: httpx.Response(
                status_code=200,
                headers={"content-type": "image/png", "content-length": str(len(PNG_BYTES))},
                content=PNG_BYTES,
            ),
            monkeypatch,
        )
    )

    payload = QueryInputPayload.model_validate(
        {
            "images": [
                {
                    "source_type": "url",
                    "url": "https://example.com/cat.png",
                }
            ]
        }
    )

    result = await wrapper.run_query(payload, server_name="alpha")

    assert result == "MODEL_RESULT"
    assert seen_queries == [None]
    sent_query = wrapper._agent.calls[0]["query"]
    assert hasattr(sent_query, "content")
    assert sent_query.content == [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{PNG_BASE64}"}}
    ]


@pytest.mark.asyncio
async def test_wrapper_run_query_fetches_text_plus_remote_image_before_agent_call(monkeypatch):
    wrapper = _build_wrapper(monkeypatch)
    wrapper._query_image_resolver = QueryImageResolver(
        remote_image_fetcher=_build_remote_image_fetcher(
            lambda _request: httpx.Response(
                status_code=200,
                headers={"content-type": "image/png", "content-length": str(len(PNG_BYTES))},
                content=PNG_BYTES,
            ),
            monkeypatch,
        )
    )

    payload = QueryInputPayload.model_validate(
        {
            "text": "describe this image",
            "images": [{"source_type": "url", "url": "https://example.com/cat.png"}],
        }
    )

    result = await wrapper.run_query(payload, server_name="alpha")

    assert result == "MODEL_RESULT"
    sent_query = wrapper._agent.calls[0]["query"]
    assert hasattr(sent_query, "content")
    assert sent_query.content == [
        {"type": "text", "text": "describe this image"},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{PNG_BASE64}"}},
    ]


@pytest.mark.asyncio
async def test_wrapper_run_query_redacts_only_text_for_multimodal_input(monkeypatch):
    from app.core.runtime.mcp_wrapper import GuardrailContext

    wrapper = _build_wrapper(monkeypatch)

    def _redact_text_guardrail(ctx: GuardrailContext) -> GuardrailContext:
        return replace(ctx, query="[REDACTED_TEXT]")

    wrapper.before_model_guardrails = [_redact_text_guardrail]

    payload = QueryInputPayload.model_validate(
        {
            "text": "secret text",
            "images": [
                {
                    "source_type": "base64",
                    "mime_type": "image/png",
                    "data": "ZmFrZV9pbWFnZQ==",
                }
            ],
        }
    )

    result = await wrapper.run_query(payload)

    assert result == "MODEL_RESULT"
    sent_query = wrapper._agent.calls[0]["query"]
    assert hasattr(sent_query, "content")
    assert sent_query.content == [
        {"type": "text", "text": "[REDACTED_TEXT]"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,ZmFrZV9pbWFnZQ=="}},
    ]


@pytest.mark.asyncio
async def test_wrapper_run_query_redacts_fetched_image_data_from_runtime_errors(monkeypatch):
    from app.core.exceptions import MCPWrapperError

    wrapper = _build_wrapper(monkeypatch)
    wrapper._query_image_resolver = QueryImageResolver(
        remote_image_fetcher=_build_remote_image_fetcher(
            lambda _request: httpx.Response(
                status_code=200,
                headers={"content-type": "image/png", "content-length": str(len(PNG_BYTES))},
                content=PNG_BYTES,
            ),
            monkeypatch,
        )
    )

    async def _failing_run(*, query, max_steps=None, server_name=None):
        raise RuntimeError(f"provider rejected {query.content[0]['image_url']['url']}")

    wrapper._agent.run = _failing_run

    payload = QueryInputPayload.model_validate(
        {"images": [{"source_type": "url", "url": "https://example.com/cat.png"}]}
    )

    with pytest.raises(MCPWrapperError) as exc_info:
        await wrapper.run_query(payload)

    assert "[REDACTED]" in str(exc_info.value)
    assert PNG_BASE64 not in str(exc_info.value)


class _OperationWrapper:
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
    ) -> None:
        self.has_mcp_servers = bool(mcp_servers or {})
        self._steps_used = 0
        self._last_server_used = None
        self.last_query = None

    def set_context(self, *, tenant_id=None, run_id=None, session_id=None):
        return None

    def set_elicitation_handler(self, handler):
        return None

    def set_task_status_handler(self, handler):
        return None

    async def initialize(self):
        return None

    async def close(self):
        return None

    @asynccontextmanager
    async def query_operation_scope(self, **kwargs):
        yield

    async def run_query(self, query, max_steps=None, server_name=None):
        self.last_query = query
        self._steps_used = 3
        self._last_server_used = server_name
        return "ASYNC_MULTIMODAL_RESULT"

    @property
    def steps_used(self) -> int:
        return self._steps_used

    @property
    def last_server_used(self):
        return self._last_server_used


@pytest.mark.asyncio
async def test_session_manager_async_operation_stores_safe_multimodal_summary(monkeypatch):
    from app.core.sessions.manager import SessionManager

    monkeypatch.setattr("app.core.sessions.manager.MCPWrapper", _OperationWrapper)

    manager = SessionManager()
    session_id = await manager.create_session(
        SessionCreateRequest.model_validate(
            {
                "llm_provider": {"provider": "ollama", "model": "dummy", "temperature": 0},
                "mcp_servers": {},
            }
        )
    )

    blob = "ZmFrZV9pbWFnZV9kYXRh"
    created = await manager.create_query_operation(
        session_id=session_id,
        request=QueryOperationCreateRequest.model_validate(
            {
                "input": {
                    "text": "describe this",
                    "images": [
                        {
                            "source_type": "url",
                            "url": "https://example.com/asset.png",
                        },
                        {
                            "source_type": "base64",
                            "mime_type": "image/png",
                            "data": blob,
                        },
                    ],
                }
            }
        ),
    )

    request_snapshot = created.metadata.request.model_dump(exclude_none=True)
    assert request_snapshot == {
        "input": {
            "text_present": True,
            "text_length": 13,
            "image_count": 2,
            "images": [
                {
                    "source_type": "url",
                    "url": "https://example.com/...",
                },
                {
                    "source_type": "base64",
                    "mime_type": "image/png",
                    "data_size_bytes": 15,
                },
            ],
        },
    }
    assert blob not in json.dumps(created.model_dump(mode="json"))

    final = None
    deadline = asyncio.get_running_loop().time() + 1.0
    while asyncio.get_running_loop().time() < deadline:
        final = await manager.get_query_operation(session_id=session_id, operation_id=created.operation_id)
        if final.status.value == "completed":
            break
        await asyncio.sleep(0.01)

    assert final is not None
    assert final.status.value == "completed"
    assert final.result is not None
    assert final.result.result == "ASYNC_MULTIMODAL_RESULT"
    assert blob not in json.dumps(final.model_dump(mode="json"))


def test_serialize_query_operation_error_redacts_data_urls():
    error = serialize_query_operation_error(
        RuntimeError("boom data:image/png;base64,ZmFrZV9pbWFnZV9kYXRh")
    )

    assert "[REDACTED]" in error.message
    assert "ZmFrZV9pbWFnZV9kYXRh" not in error.message
