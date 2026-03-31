"""Shared multimodal validation helpers used across request, resolver, and fetch paths."""

from __future__ import annotations

from typing import Optional, Sequence

from app.core.multimodal.image_data import ResolvedImageInput
from app.core.multimodal.policy import (
    MAX_REQUEST_IMAGE_COUNT,
    MAX_REQUEST_IMAGE_TOTAL_BYTES,
    SUPPORTED_IMAGE_MIME_TYPES,
    supported_image_mime_types_sorted,
)


class MultimodalInputValidationError(ValueError):
    """Raised when multimodal input violates centralized policy."""


def normalize_image_mime_type(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.split(";", 1)[0].strip().lower()
    return normalized or None


def estimate_base64_size(data: str) -> int:
    normalized = "".join(data.split())
    if not normalized:
        return 0
    padding = len(normalized) - len(normalized.rstrip("="))
    return max(0, (len(normalized) * 3) // 4 - padding)


def validate_supported_image_mime_type(mime_type: Optional[str], *, context: str) -> str:
    normalized = normalize_image_mime_type(mime_type)
    if not normalized:
        raise MultimodalInputValidationError(f"Image MIME type is missing for {context}")
    if normalized not in SUPPORTED_IMAGE_MIME_TYPES:
        raise MultimodalInputValidationError(
            "Image MIME type is not supported for "
            f"{context}: {normalized}. Supported values: {supported_image_mime_types_sorted()}"
        )
    return normalized


def validate_request_image_count(image_count: int) -> None:
    if image_count > MAX_REQUEST_IMAGE_COUNT:
        raise MultimodalInputValidationError(
            "Multimodal input contains "
            f"{image_count} images, exceeding the maximum of {MAX_REQUEST_IMAGE_COUNT} images per request"
        )


def validate_total_image_bytes(total_bytes: int) -> None:
    if total_bytes > MAX_REQUEST_IMAGE_TOTAL_BYTES:
        raise MultimodalInputValidationError(
            "Multimodal input images total "
            f"{total_bytes} bytes, exceeding the maximum of "
            f"{MAX_REQUEST_IMAGE_TOTAL_BYTES} bytes per request"
        )


def calculate_remaining_image_budget(total_bytes: int) -> int:
    return max(0, MAX_REQUEST_IMAGE_TOTAL_BYTES - total_bytes)


def validate_multimodal_request_precheck(images: Sequence[object]) -> None:
    validate_request_image_count(len(images))

    known_total_bytes = 0
    for index, image in enumerate(images):
        if getattr(image, "source_type", None) != "base64":
            continue
        validate_supported_image_mime_type(
            getattr(image, "mime_type", None),
            context=f"input.images[{index}]",
        )
        known_total_bytes += estimate_base64_size(getattr(image, "data", None) or "")

    validate_total_image_bytes(known_total_bytes)


def validate_resolved_image(image: ResolvedImageInput, *, index: int) -> int:
    validate_supported_image_mime_type(image.mime_type, context=f"resolved input.images[{index}]")

    data_size_bytes = image.data_size_bytes
    if data_size_bytes is None or data_size_bytes <= 0:
        raise MultimodalInputValidationError(
            f"Resolved image payload is empty or missing size metadata for input.images[{index}]"
        )

    return data_size_bytes
