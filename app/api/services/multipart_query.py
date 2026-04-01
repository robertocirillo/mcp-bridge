from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from fastapi import UploadFile

from app.core.session_assets.local_store import LocalTemporarySessionAssetStore
from app.core.multimodal.tool_documents import BRIDGE_UPLOADED_DOCUMENTS_ARGUMENT
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


def _parse_tool_arguments(arguments: str | None) -> dict:
    if arguments is None or not arguments.strip():
        return {}
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError as exc:
        raise MultimodalInputValidationError("Field 'arguments' must be a valid JSON object") from exc
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise MultimodalInputValidationError("Field 'arguments' must decode to a JSON object")
    return parsed


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


def _build_tool_request_from_uploaded_documents(
    *,
    tool_name: str,
    server_name: str | None,
    arguments: str | None,
    document_inputs: list[dict[str, object]],
) -> QueryOperationCreateRequest:
    parsed_arguments = _parse_tool_arguments(arguments)
    if parsed_arguments.get(BRIDGE_UPLOADED_DOCUMENTS_ARGUMENT) is not None:
        raise MultimodalInputValidationError(
            f"Field 'arguments.{BRIDGE_UPLOADED_DOCUMENTS_ARGUMENT}' is reserved by the bridge"
        )
    parsed_arguments[BRIDGE_UPLOADED_DOCUMENTS_ARGUMENT] = [
        {
            "asset_id": str(document["asset_id"]),
            "asset_kind": "document",
            "mime_type": document.get("mime_type"),
            "filename": document.get("filename"),
            "size_bytes": document.get("size_bytes"),
        }
        for document in document_inputs
    ]
    return QueryOperationCreateRequest(
        tool_name=tool_name,
        server_name=server_name,
        arguments=parsed_arguments,
    )


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
    tool_name: str | None,
    arguments: str | None,
    images: Sequence[UploadFile] | None,
    documents: Sequence[UploadFile] | None,
    asset_store: LocalTemporarySessionAssetStore,
) -> tuple[QueryOperationCreateRequest, list[str]]:
    if tool_name is not None:
        if (text or "").strip():
            raise MultimodalInputValidationError("Field 'text' is not allowed when 'tool_name' is provided")
        if max_steps is not None:
            raise MultimodalInputValidationError("Field 'max_steps' is not allowed when 'tool_name' is provided")
        if images:
            raise MultimodalInputValidationError(
                "Field 'images' is not supported for multipart direct tool invocation"
            )
        upload_documents = _filter_upload_files(documents, field_name="documents")
        if not upload_documents:
            raise MultimodalInputValidationError("Field 'documents' is required when 'tool_name' is provided")
        stored_asset_ids: list[str] = []
        try:
            document_inputs = await _persist_document_uploads(
                session_id=session_id,
                documents=upload_documents,
                asset_store=asset_store,
                stored_asset_ids=stored_asset_ids,
            )
            return (
                _build_tool_request_from_uploaded_documents(
                    tool_name=tool_name,
                    server_name=server_name,
                    arguments=arguments,
                    document_inputs=document_inputs,
                ),
                stored_asset_ids,
            )
        except Exception:
            await asset_store.delete_assets(session_id=session_id, asset_ids=stored_asset_ids)
            raise

    if arguments is not None and arguments.strip():
        raise MultimodalInputValidationError("Field 'arguments' is only supported when 'tool_name' is provided")

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
