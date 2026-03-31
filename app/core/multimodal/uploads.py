from __future__ import annotations

from app.core.multimodal.image_data import encode_image_bytes_to_base64
from app.core.multimodal.policy import supported_image_mime_types_sorted
from app.core.multimodal.validation import (
    MultimodalInputValidationError,
    normalize_image_mime_type,
    validate_supported_image_mime_type,
)
from app.models.requests import ImageInput

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SIGNATURE = b"\xff\xd8\xff"
_WEBP_RIFF_SIGNATURE = b"RIFF"
_WEBP_FORMAT_SIGNATURE = b"WEBP"
_GENERIC_BINARY_MIME_TYPES = frozenset({"application/octet-stream"})


def detect_image_mime_type(content: bytes) -> str | None:
    if content.startswith(_PNG_SIGNATURE):
        return "image/png"
    if content.startswith(_JPEG_SIGNATURE):
        return "image/jpeg"
    if len(content) >= 12 and content.startswith(_WEBP_RIFF_SIGNATURE) and content[8:12] == _WEBP_FORMAT_SIGNATURE:
        return "image/webp"
    return None


def validate_uploaded_image_payload(
    *,
    content_prefix: bytes,
    declared_mime_type: str | None,
    filename: str | None,
    index: int,
) -> str:
    context = f"images[{index}]"
    normalized_declared_mime_type = normalize_image_mime_type(declared_mime_type)
    if normalized_declared_mime_type in _GENERIC_BINARY_MIME_TYPES:
        normalized_declared_mime_type = None

    if not content_prefix:
        raise MultimodalInputValidationError(f"Uploaded image payload is empty for {context}")

    detected_mime_type = detect_image_mime_type(content_prefix)
    if detected_mime_type is None:
        label = filename or context
        raise MultimodalInputValidationError(
            f"Uploaded file '{label}' is not a supported image. Supported values: {supported_image_mime_types_sorted()}"
        )

    if normalized_declared_mime_type is not None:
        validated_declared_mime_type = validate_supported_image_mime_type(
            normalized_declared_mime_type,
            context=context,
        )
        if validated_declared_mime_type != detected_mime_type:
            raise MultimodalInputValidationError(
                "Uploaded image MIME type mismatch for "
                f"{context}: declared {validated_declared_mime_type}, detected {detected_mime_type}"
            )
        return validated_declared_mime_type

    return detected_mime_type


def build_image_input_from_upload(
    *,
    content: bytes,
    declared_mime_type: str | None,
    filename: str | None,
    index: int,
) -> ImageInput:
    mime_type = validate_uploaded_image_payload(
        content_prefix=content,
        declared_mime_type=declared_mime_type,
        filename=filename,
        index=index,
    )

    return ImageInput(
        source_type="base64",
        mime_type=mime_type,
        data=encode_image_bytes_to_base64(content),
    )
