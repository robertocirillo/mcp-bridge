from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from fastapi import UploadFile

from app.core.session_assets.local_store import LocalTemporarySessionAssetStore
from app.core.multimodal.uploads import validate_uploaded_image_payload
from app.core.multimodal.validation import (
    MultimodalInputValidationError,
    validate_request_image_count,
    validate_total_image_bytes,
)
from app.models.requests import QueryInputPayload, QueryOperationCreateRequest, QueryRequest


@dataclass(frozen=True)
class PreparedMultipartQuery:
    input_payload: QueryInputPayload
    asset_ids: list[str]


def _filter_upload_files(images: Sequence[UploadFile] | None) -> list[UploadFile]:
    upload_files = [image for image in (images or []) if image is not None]
    if not upload_files:
        raise MultimodalInputValidationError("At least one uploaded image must be provided in field 'images'")

    validate_request_image_count(len(upload_files))
    return upload_files


async def prepare_multipart_image_input(
    *,
    session_id: str,
    text: str | None,
    images: Sequence[UploadFile] | None,
    asset_store: LocalTemporarySessionAssetStore,
) -> PreparedMultipartQuery:
    upload_files = _filter_upload_files(images)

    total_image_bytes = 0
    image_inputs = []
    stored_asset_ids: list[str] = []

    try:
        for index, image in enumerate(upload_files):
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

        return PreparedMultipartQuery(
            input_payload=QueryInputPayload.model_validate({"text": text, "images": image_inputs}),
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
    asset_store: LocalTemporarySessionAssetStore,
) -> tuple[QueryRequest, list[str]]:
    prepared = await prepare_multipart_image_input(
        session_id=session_id,
        text=text,
        images=images,
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
    asset_store: LocalTemporarySessionAssetStore,
) -> tuple[QueryOperationCreateRequest, list[str]]:
    prepared = await prepare_multipart_image_input(
        session_id=session_id,
        text=text,
        images=images,
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
