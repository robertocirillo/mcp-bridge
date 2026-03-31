from __future__ import annotations

import asyncio
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Sequence

from fastapi import UploadFile

from app.core.exceptions import TemporaryUploadError, TemporaryUploadNotFoundError
from app.core.multimodal.uploads import validate_uploaded_image_payload
from app.core.multimodal.validation import validate_total_image_bytes

_COPY_CHUNK_SIZE = 1024 * 1024
_SNIFF_BYTES = 32


@dataclass(frozen=True)
class TemporaryImageUpload:
    asset_id: str
    session_id: str
    path: Path
    mime_type: str
    size_bytes: int
    filename: str | None
    created_at: datetime


class TemporaryImageUploadStore:
    """Local filesystem-backed storage for short-lived multipart image uploads."""

    def __init__(
        self,
        *,
        root_dir: Path | None = None,
        ttl_seconds: int = 3600,
    ) -> None:
        self._root_dir = Path(root_dir or Path(tempfile.gettempdir()) / "mcp-bridge" / "session-assets")
        self._ttl_seconds = ttl_seconds
        self._assets: dict[str, dict[str, TemporaryImageUpload]] = {}
        self._lock = asyncio.Lock()

    @property
    def root_dir(self) -> Path:
        return self._root_dir

    async def persist_image_upload(
        self,
        *,
        session_id: str,
        upload: UploadFile,
        index: int,
        current_total_bytes: int,
    ) -> TemporaryImageUpload:
        asset_id = str(uuid.uuid4())
        session_dir = self._root_dir / session_id
        file_path = session_dir / asset_id

        await asyncio.to_thread(session_dir.mkdir, parents=True, exist_ok=True)

        bytes_written = 0
        content_prefix = bytearray()

        try:
            with file_path.open("wb") as target:
                while True:
                    chunk = await upload.read(_COPY_CHUNK_SIZE)
                    if not chunk:
                        break
                    target.write(chunk)
                    bytes_written += len(chunk)
                    validate_total_image_bytes(current_total_bytes + bytes_written)
                    if len(content_prefix) < _SNIFF_BYTES:
                        remaining = _SNIFF_BYTES - len(content_prefix)
                        content_prefix.extend(chunk[:remaining])

            mime_type = validate_uploaded_image_payload(
                content_prefix=bytes(content_prefix),
                declared_mime_type=upload.content_type,
                filename=upload.filename,
                index=index,
            )
            asset = TemporaryImageUpload(
                asset_id=asset_id,
                session_id=session_id,
                path=file_path,
                mime_type=mime_type,
                size_bytes=bytes_written,
                filename=upload.filename,
                created_at=datetime.now(),
            )
            async with self._lock:
                self._assets.setdefault(session_id, {})[asset_id] = asset
            return asset
        except Exception as exc:
            await asyncio.to_thread(file_path.unlink, missing_ok=True)
            if isinstance(exc, ValueError):
                raise
            if isinstance(exc, TemporaryUploadError):
                raise
            raise TemporaryUploadError(f"Failed to persist multipart upload for images[{index}]") from exc
        finally:
            await upload.close()

    async def read_image_bytes(
        self,
        *,
        session_id: str,
        asset_id: str,
    ) -> bytes:
        asset = await self._get_asset(session_id=session_id, asset_id=asset_id)
        try:
            return await asyncio.to_thread(asset.path.read_bytes)
        except FileNotFoundError as exc:
            raise TemporaryUploadNotFoundError(
                f"Temporary upload asset '{asset_id}' is no longer available for session {session_id}"
            ) from exc
        except OSError as exc:
            raise TemporaryUploadError(
                f"Failed to read temporary upload asset '{asset_id}' for session {session_id}"
            ) from exc

    async def delete_assets(
        self,
        *,
        session_id: str,
        asset_ids: Sequence[str],
    ) -> None:
        if not asset_ids:
            return

        async with self._lock:
            session_assets = self._assets.get(session_id, {})
            assets = [session_assets.pop(asset_id, None) for asset_id in asset_ids]
            if session_assets:
                remaining = True
            else:
                self._assets.pop(session_id, None)
                remaining = False

        for asset in assets:
            if asset is None:
                continue
            await asyncio.to_thread(asset.path.unlink, missing_ok=True)

        if not remaining:
            session_dir = self._root_dir / session_id
            await asyncio.to_thread(self._remove_dir_if_empty, session_dir)

    async def delete_session_assets(self, session_id: str) -> None:
        async with self._lock:
            session_assets = list(self._assets.pop(session_id, {}).values())

        for asset in session_assets:
            await asyncio.to_thread(asset.path.unlink, missing_ok=True)

        session_dir = self._root_dir / session_id
        await asyncio.to_thread(shutil.rmtree, session_dir, True)

    async def sweep_expired(self) -> None:
        cutoff = datetime.now() - timedelta(seconds=self._ttl_seconds)
        tracked_sessions = set(self._assets.keys())

        if not self._root_dir.exists():
            return

        for session_dir in self._root_dir.iterdir():
            if not session_dir.is_dir():
                continue
            if session_dir.name in tracked_sessions:
                continue
            try:
                modified_at = datetime.fromtimestamp(session_dir.stat().st_mtime)
            except OSError:
                continue
            if modified_at <= cutoff:
                await asyncio.to_thread(shutil.rmtree, session_dir, True)

    async def _get_asset(
        self,
        *,
        session_id: str,
        asset_id: str,
    ) -> TemporaryImageUpload:
        async with self._lock:
            asset = self._assets.get(session_id, {}).get(asset_id)
        if asset is None:
            raise TemporaryUploadNotFoundError(
                f"Temporary upload asset '{asset_id}' is not available for session {session_id}"
            )
        return asset

    @staticmethod
    def _remove_dir_if_empty(path: Path) -> None:
        try:
            path.rmdir()
        except OSError:
            return
