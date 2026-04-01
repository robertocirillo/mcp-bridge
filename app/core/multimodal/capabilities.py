from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.core.exceptions import ImageInputNotSupportedError, PDFInputNotSupportedError

CapabilityReason = Literal["vision_capable", "text_only", "unknown"]

_VISION_MODEL_MARKERS: dict[str, tuple[str, ...]] = {
    "openai": ("gpt-4.1", "gpt-4o", "gpt-4.5", "gpt-5", "o1", "o3"),
    "anthropic": ("claude-3", "claude-4"),
    "ollama": (
        "llava",
        "bakllava",
        "moondream",
        "qwen2.5-vl",
        "qwen2.5vl",
        "qwen3-vl:8b",
        "minicpm-v",
        "llama4",
        "gemma3",
        "mistral-small3.1",
    ),
}

_TEXT_ONLY_MODEL_MARKERS: dict[str, tuple[str, ...]] = {
    "openai": ("gpt-3.5",),
    "ollama": ("llama3", "mistral", "phi4", "deepseek-r1"),
}

_PDF_MODEL_MARKERS: dict[str, tuple[str, ...]] = {
    "openai": ("gpt-4.1", "gpt-4o", "gpt-4.5", "gpt-5", "o1", "o3"),
    "anthropic": ("claude-3-5", "claude-3.5", "claude-3-7", "claude-3.7", "claude-4"),
}


@dataclass(frozen=True)
class ImageInputCapability:
    provider: str
    model: str
    supports_images: bool
    reason: CapabilityReason


@dataclass(frozen=True)
class PDFInputCapability:
    provider: str
    model: str
    supports_pdfs: bool
    reason: CapabilityReason


def _normalize_name(value: str) -> str:
    return value.strip().lower()


def _contains_any(value: str, markers: tuple[str, ...]) -> bool:
    return any(marker in value for marker in markers)


def resolve_image_input_capability(*, provider: str, model: str) -> ImageInputCapability:
    normalized_provider = _normalize_name(provider)
    normalized_model = _normalize_name(model)

    if _contains_any(normalized_model, _VISION_MODEL_MARKERS.get(normalized_provider, ())):
        return ImageInputCapability(
            provider=normalized_provider,
            model=model,
            supports_images=True,
            reason="vision_capable",
        )

    if _contains_any(normalized_model, _TEXT_ONLY_MODEL_MARKERS.get(normalized_provider, ())):
        return ImageInputCapability(
            provider=normalized_provider,
            model=model,
            supports_images=False,
            reason="text_only",
        )

    return ImageInputCapability(
        provider=normalized_provider,
        model=model,
        supports_images=False,
        reason="unknown",
    )


def ensure_image_input_supported(*, provider: str, model: str) -> None:
    capability = resolve_image_input_capability(provider=provider, model=model)
    if capability.supports_images:
        return

    raise ImageInputNotSupportedError(
        provider=provider,
        model=model,
        reason="text_only" if capability.reason == "text_only" else "unknown",
    )


def resolve_pdf_input_capability(*, provider: str, model: str) -> PDFInputCapability:
    normalized_provider = _normalize_name(provider)
    normalized_model = _normalize_name(model)

    if _contains_any(normalized_model, _PDF_MODEL_MARKERS.get(normalized_provider, ())):
        return PDFInputCapability(
            provider=normalized_provider,
            model=model,
            supports_pdfs=True,
            reason="vision_capable",
        )

    if _contains_any(normalized_model, _TEXT_ONLY_MODEL_MARKERS.get(normalized_provider, ())):
        return PDFInputCapability(
            provider=normalized_provider,
            model=model,
            supports_pdfs=False,
            reason="text_only",
        )

    return PDFInputCapability(
        provider=normalized_provider,
        model=model,
        supports_pdfs=False,
        reason="unknown",
    )


def ensure_pdf_input_supported(*, provider: str, model: str) -> None:
    capability = resolve_pdf_input_capability(provider=provider, model=model)
    if capability.supports_pdfs:
        return

    raise PDFInputNotSupportedError(
        provider=provider,
        model=model,
        reason="text_only" if capability.reason == "text_only" else "unknown",
    )
