from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urlsplit

from app.core.multimodal.image_data import ResolvedQueryInputPayload
from app.core.multimodal.validation import estimate_base64_size
from app.models.requests import QueryInputPayload
from app.models.responses import QueryInputImageSummary, QueryInputPayloadSummary

try:
    from langchain_core.messages import HumanMessage as LangChainHumanMessage
except ImportError:
    class LangChainHumanMessage:  # pragma: no cover - fallback for partial test envs without langchain-core
        def __init__(self, content: Any):
            self.content = content

        def __repr__(self) -> str:
            block_count = len(self.content) if isinstance(self.content, list) else 1
            return f"HumanMessage(content=[{block_count} blocks])"

ModelQueryInput = str | QueryInputPayload
PreparedModelQueryInput = str | ResolvedQueryInputPayload
BuiltModelQuery = str | LangChainHumanMessage

_DATA_URL_RE = re.compile(
    r"data:(image/(?:png|jpeg|webp));base64,[A-Za-z0-9+/=]+",
    re.IGNORECASE,
)


def _has_text_content(text: Optional[str]) -> bool:
    return text is not None and bool(text.strip())


def resolve_request_query(
    *,
    query: Optional[str],
    input_payload: Optional[QueryInputPayload],
) -> ModelQueryInput:
    if input_payload is not None:
        return input_payload
    return query or ""


def extract_query_text(query_input: ModelQueryInput | PreparedModelQueryInput) -> Optional[str]:
    if isinstance(query_input, str):
        return query_input
    return query_input.text if _has_text_content(query_input.text) else None


def replace_query_text(
    query_input: ModelQueryInput | PreparedModelQueryInput,
    *,
    text: Optional[str],
) -> ModelQueryInput | PreparedModelQueryInput:
    if isinstance(query_input, str):
        return text or ""
    if isinstance(query_input, ResolvedQueryInputPayload):
        return ResolvedQueryInputPayload(text=text, images=list(query_input.images))
    return query_input.model_copy(update={"text": text})


def has_query_visual_input(query_input: ModelQueryInput | PreparedModelQueryInput) -> bool:
    return not isinstance(query_input, str) and bool(query_input.images)


def build_model_query(query_input: PreparedModelQueryInput) -> BuiltModelQuery:
    if isinstance(query_input, str):
        return query_input

    blocks: list[dict[str, Any]] = []
    if _has_text_content(query_input.text):
        blocks.append({"type": "text", "text": query_input.text})

    for image in query_input.images:
        blocks.append(
            {
                "type": "image_url",
                "image_url": {"url": image.as_data_url()},
            }
        )

    return LangChainHumanMessage(content=blocks)


def summarize_query_input(input_payload: QueryInputPayload) -> QueryInputPayloadSummary:
    images = [
        QueryInputImageSummary(
            source_type=image.source_type,
            mime_type=image.mime_type,
            url=redact_image_url(image.url) if image.url else None,
            data_size_bytes=estimate_base64_size(image.data) if image.data else getattr(image, "size_bytes", None),
        )
        for image in input_payload.images
    ]
    return QueryInputPayloadSummary(
        text_present=_has_text_content(input_payload.text),
        text_length=len(input_payload.text) if _has_text_content(input_payload.text) else None,
        image_count=len(images),
        images=images,
    )


def describe_query_input(query_input: ModelQueryInput | PreparedModelQueryInput) -> str:
    if isinstance(query_input, str):
        return f"text:{query_input[:100]}"

    input_payload = query_input
    text_length = len(input_payload.text) if _has_text_content(input_payload.text) else 0
    return (
        f"structured:text_present={_has_text_content(input_payload.text)} "
        f"text_length={text_length} image_count={len(input_payload.images)}"
    )
def sanitize_multimodal_error(value: Any) -> str:
    return _DATA_URL_RE.sub(r"data:\1;base64,[REDACTED]", str(value))


def redact_image_url(url: str) -> str:
    parsed = urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        return "[redacted-url]"
    return f"{parsed.scheme}://{parsed.netloc}/..."
def is_langchain_human_message(value: Any) -> bool:
    return isinstance(value, LangChainHumanMessage)
