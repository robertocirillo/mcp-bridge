from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_SESSION_ASSET_KIND = "image"
DEFAULT_SESSION_ASSET_PURPOSE = "input_image"
LEGACY_METADATA_FALLBACK_KIND = "generic"
LEGACY_METADATA_FALLBACK_PURPOSE = "attachment"


@dataclass(frozen=True)
class SessionAsset:
    """Filesystem-backed, session-scoped asset metadata."""

    asset_id: str
    session_id: str
    path: Path
    mime_type: str
    size_bytes: int
    filename: str | None
    created_at: datetime
    kind: str = DEFAULT_SESSION_ASSET_KIND
    purpose: str = DEFAULT_SESSION_ASSET_PURPOSE
    metadata_path: Path | None = None
    declared_content_type: str | None = None
    storage_backend: str = "local_temp"
    last_accessed_at: datetime | None = None
    expires_at: datetime | None = None

    def to_metadata_payload(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "session_id": self.session_id,
            "kind": self.kind,
            "purpose": self.purpose,
            "storage_backend": self.storage_backend,
            "path": self.path.name,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "filename": self.filename,
            "declared_content_type": self.declared_content_type,
            "created_at": self.created_at.isoformat(),
            "last_accessed_at": self.last_accessed_at.isoformat() if self.last_accessed_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }

    @classmethod
    def from_metadata_payload(
        cls,
        *,
        session_dir: Path,
        metadata_path: Path,
        payload: dict[str, Any],
    ) -> "SessionAsset":
        return cls(
            asset_id=str(payload["asset_id"]),
            session_id=str(payload["session_id"]),
            # Missing kind/purpose in on-disk metadata predates the asset layer,
            # so reloads stay neutral instead of inferring image-specific semantics.
            kind=str(payload.get("kind") or LEGACY_METADATA_FALLBACK_KIND),
            purpose=str(payload.get("purpose") or LEGACY_METADATA_FALLBACK_PURPOSE),
            path=session_dir / str(payload["path"]),
            metadata_path=metadata_path,
            mime_type=str(payload["mime_type"]),
            size_bytes=int(payload["size_bytes"]),
            filename=payload.get("filename"),
            declared_content_type=payload.get("declared_content_type"),
            storage_backend=str(payload.get("storage_backend") or "local_temp"),
            created_at=datetime.fromisoformat(str(payload["created_at"])),
            last_accessed_at=(
                datetime.fromisoformat(str(payload["last_accessed_at"]))
                if payload.get("last_accessed_at")
                else None
            ),
            expires_at=(
                datetime.fromisoformat(str(payload["expires_at"]))
                if payload.get("expires_at")
                else None
            ),
        )
