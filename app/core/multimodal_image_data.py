from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Literal, Optional


@dataclass(frozen=True)
class ResolvedImageInput:
    """Internal normalized image payload always ready for provider consumption."""

    source_type: Literal["url", "base64"]
    mime_type: str
    base64_data: str
    data_size_bytes: Optional[int] = None
    source_url: Optional[str] = None

    def as_data_url(self) -> str:
        return build_image_data_url(mime_type=self.mime_type, base64_data=self.base64_data)


@dataclass(frozen=True)
class ResolvedQueryInputPayload:
    """Internal normalized multimodal input passed to the provider builder."""

    text: Optional[str]
    images: list[ResolvedImageInput] = field(default_factory=list)


def encode_image_bytes_to_base64(image_bytes: bytes) -> str:
    return base64.b64encode(image_bytes).decode("ascii")


def build_image_data_url(*, mime_type: str, base64_data: str) -> str:
    return f"data:{mime_type};base64,{base64_data}"
