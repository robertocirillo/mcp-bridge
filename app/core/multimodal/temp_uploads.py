from __future__ import annotations

"""Backward-compatible imports for temporary image upload handling."""

from app.core.session_assets.local_store import LocalTemporarySessionAssetStore
from app.core.session_assets.models import SessionAsset

TemporaryImageUpload = SessionAsset
TemporaryImageUploadStore = LocalTemporarySessionAssetStore

__all__ = [
    "TemporaryImageUpload",
    "TemporaryImageUploadStore",
]
