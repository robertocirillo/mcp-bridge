from __future__ import annotations

from collections.abc import Sequence

from fastapi import UploadFile

from app.core.multimodal.temp_uploads import TemporaryImageUploadStore
from app.core.multimodal.uploads import build_image_input_from_upload
from app.core.multimodal.validation import (
    MultimodalInputValidationError,
    validate_request_image_count,
    validate_total_image_bytes,
)
from app.models.requests import QueryInputPayload, QueryOperationCreateRequest, QueryRequest


async def build_multipart_query_request(
    *,
    text: str | None,
    max_steps: int | None,
    server_name: str | None,
    images: Sequence[UploadFile] | None,
) -> QueryRequest:
    upload_files = [image for image in (images or []) if image is not None]
    if not upload_files:
        raise MultimodalInputValidationError("At least one uploaded image must be provided in field 'images'")

    validate_request_image_count(len(upload_files))

    total_image_bytes = 0
    image_inputs = []
    for index, image in enumerate(upload_files):
        try:
            content = await image.read()
        finally:
            await image.close()

        total_image_bytes += len(content)
        validate_total_image_bytes(total_image_bytes)
        image_inputs.append(
            build_image_input_from_upload(
                content=content,
                declared_mime_type=image.content_type,
                filename=image.filename,
                index=index,
            )
        )

    return QueryRequest(
        input=QueryInputPayload(text=text, images=image_inputs),
        max_steps=max_steps,
        server_name=server_name,
    )


async def build_multipart_query_operation_request(
    *,
    session_id: str,
    text: str | None,
    max_steps: int | None,
    server_name: str | None,
    images: Sequence[UploadFile] | None,
    upload_store: TemporaryImageUploadStore,
) -> QueryOperationCreateRequest:
    upload_files = [image for image in (images or []) if image is not None]
    if not upload_files:
        raise MultimodalInputValidationError("At least one uploaded image must be provided in field 'images'")

    validate_request_image_count(len(upload_files))

    total_image_bytes = 0
    image_inputs = []
    stored_asset_ids: list[str] = []

    try:
        for index, image in enumerate(upload_files):
            asset = await upload_store.persist_image_upload(
                session_id=session_id,
                upload=image,
                index=index,
                current_total_bytes=total_image_bytes,
            )
            total_image_bytes += asset.size_bytes
            validate_total_image_bytes(total_image_bytes)
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

        return QueryOperationCreateRequest(
            input=QueryInputPayload.model_validate({"text": text, "images": image_inputs}),
            max_steps=max_steps,
            server_name=server_name,
        )
    except Exception:
        await upload_store.delete_assets(session_id=session_id, asset_ids=stored_asset_ids)
        raise
