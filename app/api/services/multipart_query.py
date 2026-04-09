from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from fastapi import UploadFile

from app.core.session_assets.local_store import LocalTemporarySessionAssetStore
from app.core.multimodal.uploads import validate_uploaded_image_payload, validate_uploaded_pdf_payload
from app.core.multimodal.validation import (
    MultimodalInputValidationError,
    validate_request_document_count,
    validate_request_image_count,
    validate_total_document_bytes,
    validate_total_image_bytes,
)
from app.models.requests import QueryInputPayload, QueryOperationCreateRequest, QueryRequest


@dataclass(frozen=True)
class PreparedMultipartQuery:
    input_payload: QueryInputPayload
    asset_ids: list[str]


def _filter_upload_files(
    uploads: Sequence[UploadFile] | None,
    *,
    field_name: str,
) -> list[UploadFile]:
    upload_files = [upload for upload in (uploads or []) if upload is not None]
    if field_name == "images":
        validate_request_image_count(len(upload_files))
    elif field_name == "documents":
        validate_request_document_count(len(upload_files))
    else:
        raise ValueError(f"Unsupported multipart upload field '{field_name}'")
    return upload_files


def _validate_multipart_query_payload(
    *,
    text: str | None,
    images: list[UploadFile],
    documents: list[UploadFile],
) -> None:
    if (text or "").strip() or images or documents:
        return
    raise MultimodalInputValidationError(
        "At least one of 'text', 'images', or 'documents' must be provided in multipart input"
    )


async def _persist_document_uploads(
    *,
    session_id: str,
    documents: Sequence[UploadFile],
    asset_store: LocalTemporarySessionAssetStore,
    stored_asset_ids: list[str],
) -> list[dict[str, object]]:
    total_document_bytes = 0
    document_inputs: list[dict[str, object]] = []
    for index, document in enumerate(documents):
        asset = await asset_store.persist_upload(
            session_id=session_id,
            upload=document,
            index=index,
            kind="document",
            purpose="input_document",
            current_total_bytes=total_document_bytes,
            content_validator=validate_uploaded_pdf_payload,
            size_validator=validate_total_document_bytes,
        )
        total_document_bytes += asset.size_bytes
        stored_asset_ids.append(asset.asset_id)
        document_inputs.append(
            {
                "source_type": "upload",
                "asset_id": asset.asset_id,
                "mime_type": asset.mime_type,
                "size_bytes": asset.size_bytes,
                "filename": asset.filename,
            }
        )
    return document_inputs


async def _persist_image_uploads(
    *,
    session_id: str,
    images: Sequence[UploadFile],
    asset_store: LocalTemporarySessionAssetStore,
    stored_asset_ids: list[str],
) -> list[dict[str, object]]:
    total_image_bytes = 0
    image_inputs: list[dict[str, object]] = []
    for index, image in enumerate(images):
        asset = await asset_store.persist_upload(
            session_id=session_id,
            upload=image,
            index=index,
            kind="image",
            purpose="input_image",
            current_total_bytes=total_image_bytes,
            content_validator=validate_uploaded_image_payload,
            size_validator=validate_total_image_bytes,
        )
        total_image_bytes += asset.size_bytes
        stored_asset_ids.append(asset.asset_id)
        image_inputs.append(
            {
                "source_type": "upload",
                "asset_id": asset.asset_id,
                "mime_type": asset.mime_type,
                "size_bytes": asset.size_bytes,
                "filename": asset.filename,
            }
        )
    return image_inputs


async def prepare_multipart_query_input(
    *,
    session_id: str,
    text: str | None,
    images: Sequence[UploadFile] | None,
    documents: Sequence[UploadFile] | None,
    asset_store: LocalTemporarySessionAssetStore,
) -> PreparedMultipartQuery:
    upload_files = _filter_upload_files(images, field_name="images")
    upload_documents = _filter_upload_files(documents, field_name="documents")
    _validate_multipart_query_payload(
        text=text,
        images=upload_files,
        documents=upload_documents,
    )

    stored_asset_ids: list[str] = []

    try:
        image_inputs = await _persist_image_uploads(
            session_id=session_id,
            images=upload_files,
            asset_store=asset_store,
            stored_asset_ids=stored_asset_ids,
        )
        document_inputs = await _persist_document_uploads(
            session_id=session_id,
            documents=upload_documents,
            asset_store=asset_store,
            stored_asset_ids=stored_asset_ids,
        )

        return PreparedMultipartQuery(
            input_payload=QueryInputPayload.model_validate(
                {
                    "text": text,
                    "images": image_inputs,
                    "documents": document_inputs,
                }
            ),
            asset_ids=stored_asset_ids,
        )
    except Exception:
        await asset_store.delete_assets(session_id=session_id, asset_ids=stored_asset_ids)
        raise


async def build_multipart_query_request(
    *,
    session_id: str,
    text: str | None,
    max_steps: int | None,
    server_name: str | None,
    images: Sequence[UploadFile] | None,
    documents: Sequence[UploadFile] | None,
    asset_store: LocalTemporarySessionAssetStore,
) -> tuple[QueryRequest, list[str]]:
    prepared = await prepare_multipart_query_input(
        session_id=session_id,
        text=text,
        images=images,
        documents=documents,
        asset_store=asset_store,
    )
    return (
        QueryRequest(
            input=prepared.input_payload,
            max_steps=max_steps,
            server_name=server_name,
        ),
        prepared.asset_ids,
    )


async def build_multipart_query_operation_request(
    *,
    session_id: str,
    text: str | None,
    max_steps: int | None,
    server_name: str | None,
    images: Sequence[UploadFile] | None,
    documents: Sequence[UploadFile] | None,
    asset_store: LocalTemporarySessionAssetStore,
) -> tuple[QueryOperationCreateRequest, list[str]]:
    prepared = await prepare_multipart_query_input(
        session_id=session_id,
        text=text,
        images=images,
        documents=documents,
        asset_store=asset_store,
    )
    return (
        QueryOperationCreateRequest(
            input=prepared.input_payload,
            max_steps=max_steps,
            server_name=server_name,
        ),
        prepared.asset_ids,
    )
