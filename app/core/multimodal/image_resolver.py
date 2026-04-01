from __future__ import annotations

from typing import Optional

from app.core.multimodal.image_data import (
    ResolvedDocumentInput,
    ResolvedImageInput,
    ResolvedQueryInputPayload,
    encode_bytes_to_base64,
    encode_image_bytes_to_base64,
)
from app.core.multimodal.image_fetch import RemoteImageFetcher
from app.core.session_assets.local_store import LocalTemporarySessionAssetStore
from app.core.multimodal.validation import (
    validate_resolved_document,
    calculate_remaining_image_budget,
    estimate_base64_size,
    validate_multimodal_request_precheck,
    validate_resolved_image,
    validate_total_document_bytes,
    validate_total_image_bytes,
)
from app.models.requests import DocumentInput, ImageInput, QueryInputPayload


class QueryAssetResolver:
    """Normalize structured multimodal image and PDF inputs into provider-ready payloads."""

    def __init__(
        self,
        *,
        remote_image_fetcher: Optional[RemoteImageFetcher] = None,
        upload_store: Optional[LocalTemporarySessionAssetStore] = None,
    ) -> None:
        self._remote_image_fetcher = remote_image_fetcher or RemoteImageFetcher()
        self._upload_store = upload_store

    async def resolve(
        self,
        query_input: str | QueryInputPayload,
        *,
        session_id: str | None = None,
    ) -> str | ResolvedQueryInputPayload:
        if isinstance(query_input, str):
            return query_input

        validate_multimodal_request_precheck(
            query_input.images,
            query_input.documents,
        )

        images: list[ResolvedImageInput] = []
        total_image_bytes = 0
        for index, image in enumerate(query_input.images):
            images.append(
                await self._resolve_image(
                    image,
                    remaining_request_image_bytes=calculate_remaining_image_budget(total_image_bytes),
                    session_id=session_id,
                )
            )
            total_image_bytes += validate_resolved_image(images[-1], index=index)
            validate_total_image_bytes(total_image_bytes)

        documents: list[ResolvedDocumentInput] = []
        total_document_bytes = 0
        for index, document in enumerate(query_input.documents):
            documents.append(
                await self._resolve_document(
                    document,
                    session_id=session_id,
                )
            )
            total_document_bytes += validate_resolved_document(documents[-1], index=index)
            validate_total_document_bytes(total_document_bytes)

        return ResolvedQueryInputPayload(
            text=query_input.text,
            images=images,
            documents=documents,
        )

    async def _resolve_image(
        self,
        image: ImageInput,
        *,
        remaining_request_image_bytes: int,
        session_id: str | None,
    ) -> ResolvedImageInput:
        if image.source_type == "base64":
            return ResolvedImageInput(
                source_type="base64",
                mime_type=image.mime_type or "",
                base64_data=image.data or "",
                data_size_bytes=estimate_base64_size(image.data or ""),
            )

        if image.source_type == "upload":
            if self._upload_store is None:
                raise ValueError("Temporary upload store is not configured")
            if session_id is None:
                raise ValueError("Session context is required to resolve uploaded images")
            image_bytes = await self._upload_store.read_image_bytes(
                session_id=session_id,
                asset_id=image.asset_id or "",
            )
            return ResolvedImageInput(
                source_type="base64",
                mime_type=image.mime_type or "",
                base64_data=encode_image_bytes_to_base64(image_bytes),
                data_size_bytes=len(image_bytes),
            )

        fetched_image = await self._remote_image_fetcher.fetch(
            image.url or "",
            max_bytes=remaining_request_image_bytes,
            max_bytes_scope="request_budget",
        )
        return ResolvedImageInput(
            source_type="url",
            mime_type=fetched_image.mime_type,
            base64_data=encode_image_bytes_to_base64(fetched_image.content),
            data_size_bytes=len(fetched_image.content),
            source_url=image.url,
        )

    async def _resolve_document(
        self,
        document: DocumentInput,
        *,
        session_id: str | None,
    ) -> ResolvedDocumentInput:
        if self._upload_store is None:
            raise ValueError("Temporary upload store is not configured")
        if session_id is None:
            raise ValueError("Session context is required to resolve uploaded PDF documents")

        document_bytes = await self._upload_store.read_document_bytes(
            session_id=session_id,
            asset_id=document.asset_id or "",
        )
        return ResolvedDocumentInput(
            source_type="upload",
            mime_type=document.mime_type or "",
            base64_data=encode_bytes_to_base64(document_bytes),
            data_size_bytes=len(document_bytes),
            filename=document.filename,
        )


QueryImageResolver = QueryAssetResolver
