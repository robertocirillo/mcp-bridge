from __future__ import annotations

from app.core.multimodal.capabilities import ensure_image_input_supported, ensure_pdf_input_supported
from app.core.multimodal.model_query import (
    has_query_pdf_input,
    has_query_visual_input,
    resolve_request_query,
)
from app.core.multimodal.validation import validate_multimodal_request_precheck
from app.models.requests import QueryOperationCreateRequest, QueryRequest


def validate_multimodal_query_request(
    *,
    request: QueryRequest | QueryOperationCreateRequest,
    provider: str,
    model: str,
) -> None:
    query_input = resolve_request_query(
        query=request.query,
        input_payload=request.input,
    )
    if isinstance(query_input, str):
        return

    validate_multimodal_request_precheck(
        query_input.images,
        query_input.documents,
    )
    if has_query_visual_input(query_input):
        ensure_image_input_supported(provider=provider, model=model)
    if has_query_pdf_input(query_input):
        ensure_pdf_input_supported(provider=provider, model=model)
