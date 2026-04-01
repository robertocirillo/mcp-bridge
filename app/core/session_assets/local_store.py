from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import UploadFile

from app.core.exceptions import TemporaryUploadError, TemporaryUploadNotFoundError
from app.core.multimodal.uploads import validate_uploaded_image_payload
from app.core.multimodal.validation import validate_total_image_bytes

from .cleanup import file_is_stale, remove_dir_if_empty, stale_cutoff
from .models import SessionAsset

_COPY_CHUNK_SIZE = 1024 * 1024
_SNIFF_BYTES = 32

AssetContentValidator = Callable[..., str]
AssetSizeValidator = Callable[[int], None]


class LocalTemporarySessionAssetStore:
    """Local filesystem-backed storage for short-lived session assets."""

    def __init__(
        self,
        *,
        root_dir: Path | None = None,
        ttl_seconds: int = 3600,
    ) -> None:
        self._root_dir = Path(root_dir or Path(tempfile.gettempdir()) / "mcp-bridge" / "session-assets")
        self._ttl_seconds = ttl_seconds
        self._assets: dict[str, dict[str, SessionAsset]] = {}
        self._lock = asyncio.Lock()

    @property
    def root_dir(self) -> Path:
        return self._root_dir

    async def persist_upload(
        self,
        *,
        session_id: str,
        upload: UploadFile,
        index: int,
        kind: str,
        purpose: str,
        current_total_bytes: int = 0,
        content_validator: AssetContentValidator,
        size_validator: AssetSizeValidator | None = None,
    ) -> SessionAsset:
        asset_id = str(uuid.uuid4())
        session_dir = self._root_dir / session_id
        file_path = session_dir / f"{asset_id}.bin"
        metadata_path = session_dir / f"{asset_id}.json"

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
                    if size_validator is not None:
                        size_validator(current_total_bytes + bytes_written)
                    if len(content_prefix) < _SNIFF_BYTES:
                        remaining = _SNIFF_BYTES - len(content_prefix)
                        content_prefix.extend(chunk[:remaining])

            detected_mime_type = content_validator(
                content_prefix=bytes(content_prefix),
                declared_mime_type=upload.content_type,
                filename=upload.filename,
                index=index,
            )
            now = datetime.now()
            asset = SessionAsset(
                asset_id=asset_id,
                session_id=session_id,
                kind=kind,
                purpose=purpose,
                path=file_path,
                metadata_path=metadata_path,
                mime_type=detected_mime_type,
                size_bytes=bytes_written,
                filename=upload.filename,
                declared_content_type=upload.content_type,
                created_at=now,
                last_accessed_at=now,
                expires_at=now + timedelta(seconds=self._ttl_seconds),
            )
            await asyncio.to_thread(self._write_metadata, asset)
            async with self._lock:
                self._assets.setdefault(session_id, {})[asset_id] = asset
            return asset
        except Exception as exc:
            await asyncio.to_thread(file_path.unlink, missing_ok=True)
            await asyncio.to_thread(metadata_path.unlink, missing_ok=True)
            if isinstance(exc, ValueError):
                raise
            if isinstance(exc, TemporaryUploadError):
                raise
            raise TemporaryUploadError(
                f"Failed to persist multipart asset at index {index} "
                f"(kind={kind}, purpose={purpose})"
            ) from exc
        finally:
            await upload.close()

    async def persist_image_upload(
        self,
        *,
        session_id: str,
        upload: UploadFile,
        index: int,
        current_total_bytes: int,
    ) -> SessionAsset:
        return await self.persist_upload(
            session_id=session_id,
            upload=upload,
            index=index,
            kind="image",
            purpose="input_image",
            current_total_bytes=current_total_bytes,
            content_validator=validate_uploaded_image_payload,
            size_validator=validate_total_image_bytes,
        )

    async def read_bytes(
        self,
        *,
        session_id: str,
        asset_id: str,
    ) -> bytes:
        asset = await self._get_asset(session_id=session_id, asset_id=asset_id)
        try:
            content = await asyncio.to_thread(asset.path.read_bytes)
        except FileNotFoundError as exc:
            await self.delete_assets(session_id=session_id, asset_ids=[asset_id])
            raise TemporaryUploadNotFoundError(
                f"Temporary upload asset '{asset_id}' is no longer available for session {session_id}"
            ) from exc
        except OSError as exc:
            raise TemporaryUploadError(
                f"Failed to read temporary upload asset '{asset_id}' for session {session_id}"
            ) from exc

        await self._touch_asset(asset)
        return content

    async def read_image_bytes(
        self,
        *,
        session_id: str,
        asset_id: str,
    ) -> bytes:
        return await self.read_bytes(session_id=session_id, asset_id=asset_id)

    async def delete_assets(
        self,
        *,
        session_id: str,
        asset_ids: Sequence[str],
    ) -> None:
        if not asset_ids:
            return

        assets: list[SessionAsset] = []
        async with self._lock:
            session_assets = self._assets.get(session_id, {})
            for asset_id in asset_ids:
                asset = session_assets.pop(asset_id, None)
                if asset is not None:
                    assets.append(asset)
            if session_assets:
                remaining = True
            else:
                self._assets.pop(session_id, None)
                remaining = False

        unresolved_asset_ids = [asset_id for asset_id in asset_ids if asset_id not in {asset.asset_id for asset in assets}]
        for asset_id in unresolved_asset_ids:
            asset = await self._load_asset_from_disk(session_id=session_id, asset_id=asset_id)
            if asset is not None:
                assets.append(asset)

        for asset in assets:
            await asyncio.to_thread(asset.path.unlink, missing_ok=True)
            if asset.metadata_path is not None:
                await asyncio.to_thread(asset.metadata_path.unlink, missing_ok=True)

        if not remaining:
            session_dir = self._root_dir / session_id
            await asyncio.to_thread(remove_dir_if_empty, session_dir)

    async def delete_session_assets(self, session_id: str) -> None:
        async with self._lock:
            self._assets.pop(session_id, None)

        session_dir = self._root_dir / session_id
        await asyncio.to_thread(shutil.rmtree, session_dir, True)

    async def sweep_expired(self) -> None:
        if not self._root_dir.exists():
            return

        cutoff = stale_cutoff(ttl_seconds=self._ttl_seconds)
        for session_dir in self._root_dir.iterdir():
            if not session_dir.is_dir():
                continue

            for metadata_path in session_dir.glob("*.json"):
                asset = await asyncio.to_thread(self._load_asset_from_metadata_path, metadata_path)
                if asset is None:
                    if file_is_stale(path=metadata_path, cutoff=cutoff):
                        await asyncio.to_thread(metadata_path.unlink, missing_ok=True)
                    continue
                if asset.expires_at is not None and asset.expires_at > datetime.now():
                    continue
                await self.delete_assets(session_id=asset.session_id, asset_ids=[asset.asset_id])

            if not session_dir.exists():
                continue

            for file_path in session_dir.iterdir():
                if file_path.suffix == ".json":
                    continue
                metadata_path = session_dir / f"{file_path.stem}.json"
                if metadata_path.exists():
                    continue
                if file_is_stale(path=file_path, cutoff=cutoff):
                    await asyncio.to_thread(file_path.unlink, missing_ok=True)

            await asyncio.to_thread(remove_dir_if_empty, session_dir)

    async def _get_asset(
        self,
        *,
        session_id: str,
        asset_id: str,
    ) -> SessionAsset:
        async with self._lock:
            asset = self._assets.get(session_id, {}).get(asset_id)
        if asset is not None:
            return asset

        asset = await self._load_asset_from_disk(session_id=session_id, asset_id=asset_id)
        if asset is None:
            raise TemporaryUploadNotFoundError(
                f"Temporary upload asset '{asset_id}' is not available for session {session_id}"
            )

        async with self._lock:
            self._assets.setdefault(session_id, {})[asset_id] = asset
        return asset

    async def _load_asset_from_disk(self, *, session_id: str, asset_id: str) -> SessionAsset | None:
        metadata_path = self._root_dir / session_id / f"{asset_id}.json"
        return await asyncio.to_thread(self._load_asset_from_metadata_path, metadata_path)

    def _load_asset_from_metadata_path(self, metadata_path: Path) -> SessionAsset | None:
        if not metadata_path.exists():
            return None
        try:
            payload = json.loads(metadata_path.read_text())
            return SessionAsset.from_metadata_payload(
                session_dir=metadata_path.parent,
                metadata_path=metadata_path,
                payload=payload,
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None

    async def _touch_asset(self, asset: SessionAsset) -> None:
        now = datetime.now()
        updated_asset = SessionAsset(
            asset_id=asset.asset_id,
            session_id=asset.session_id,
            kind=asset.kind,
            purpose=asset.purpose,
            path=asset.path,
            metadata_path=asset.metadata_path,
            mime_type=asset.mime_type,
            size_bytes=asset.size_bytes,
            filename=asset.filename,
            declared_content_type=asset.declared_content_type,
            storage_backend=asset.storage_backend,
            created_at=asset.created_at,
            last_accessed_at=now,
            expires_at=now + timedelta(seconds=self._ttl_seconds),
        )
        if updated_asset.metadata_path is not None:
            await asyncio.to_thread(self._write_metadata, updated_asset)
        async with self._lock:
            self._assets.setdefault(updated_asset.session_id, {})[updated_asset.asset_id] = updated_asset

    @staticmethod
    def _write_metadata(asset: SessionAsset) -> None:
        if asset.metadata_path is None:
            return
        payload = json.dumps(asset.to_metadata_payload(), sort_keys=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=asset.metadata_path.parent,
                prefix=f"{asset.metadata_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                temp_file.write(payload)
                temp_path = Path(temp_file.name)
            temp_path.replace(asset.metadata_path)
        except Exception:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            raise
