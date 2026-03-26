from __future__ import annotations

from typing import Optional

from app.core.multimodal_image_data import (
    ResolvedImageInput,
    ResolvedQueryInputPayload,
    encode_image_bytes_to_base64,
)
from app.core.multimodal_image_fetch import RemoteImageFetcher
from app.models.requests import ImageInput, QueryInputPayload


def _estimate_base64_size(data: str) -> int:
    normalized = "".join(data.split())
    if not normalized:
        return 0
    padding = len(normalized) - len(normalized.rstrip("="))
    return max(0, (len(normalized) * 3) // 4 - padding)


class QueryImageResolver:
    """Normalize structured multimodal image inputs into provider-ready data URLs."""

    def __init__(self, *, remote_image_fetcher: Optional[RemoteImageFetcher] = None) -> None:
        self._remote_image_fetcher = remote_image_fetcher or RemoteImageFetcher()

    async def resolve(self, query_input: str | QueryInputPayload) -> str | ResolvedQueryInputPayload:
        if isinstance(query_input, str):
            return query_input

        images: list[ResolvedImageInput] = []
        for image in query_input.images:
            images.append(await self._resolve_image(image))

        return ResolvedQueryInputPayload(
            text=query_input.text,
            images=images,
        )

    async def _resolve_image(self, image: ImageInput) -> ResolvedImageInput:
        if image.source_type == "base64":
            return ResolvedImageInput(
                source_type="base64",
                mime_type=image.mime_type or "",
                base64_data=image.data or "",
                data_size_bytes=_estimate_base64_size(image.data or ""),
            )

        fetched_image = await self._remote_image_fetcher.fetch(image.url or "")
        return ResolvedImageInput(
            source_type="url",
            mime_type=fetched_image.mime_type,
            base64_data=encode_image_bytes_to_base64(fetched_image.content),
            data_size_bytes=len(fetched_image.content),
            source_url=image.url,
        )
