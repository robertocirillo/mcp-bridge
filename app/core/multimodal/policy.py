"""Centralized policy values for multimodal image and PDF input handling."""

from __future__ import annotations

SUPPORTED_IMAGE_MIME_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/webp",
    }
)

SUPPORTED_DOCUMENT_MIME_TYPES = frozenset(
    {
        "application/pdf",
    }
)

MAX_BASE64_IMAGE_DATA_LENGTH = 5_000_000
MAX_REMOTE_IMAGE_BYTES = 5_000_000

MAX_REQUEST_IMAGE_COUNT = 4
MAX_REQUEST_IMAGE_TOTAL_BYTES = 15_000_000
MAX_REQUEST_DOCUMENT_COUNT = 4
MAX_REQUEST_DOCUMENT_TOTAL_BYTES = 32_000_000


def supported_image_mime_types_sorted() -> list[str]:
    return sorted(SUPPORTED_IMAGE_MIME_TYPES)


def supported_document_mime_types_sorted() -> list[str]:
    return sorted(SUPPORTED_DOCUMENT_MIME_TYPES)
