from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.core.exceptions import TemporaryUploadError
from app.core.multimodal.image_data import encode_bytes_to_base64
from app.core.session_assets.local_store import LocalTemporarySessionAssetStore
from app.core.session_assets.models import SessionAsset

BRIDGE_UPLOADED_DOCUMENTS_ARGUMENT = "_bridge_uploaded_documents"


def build_uploaded_document_asset_refs(assets: Sequence[SessionAsset]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for asset in assets:
        refs.append(
            {
                "asset_id": asset.asset_id,
                "asset_kind": asset.kind,
                "mime_type": asset.mime_type,
                "filename": asset.filename,
                "size_bytes": asset.size_bytes,
            }
        )
    return refs


def extract_uploaded_document_asset_refs(arguments: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not arguments:
        return []

    raw_refs = arguments.get(BRIDGE_UPLOADED_DOCUMENTS_ARGUMENT)
    if not isinstance(raw_refs, list):
        return []

    refs: list[dict[str, Any]] = []
    for raw_ref in raw_refs:
        if not isinstance(raw_ref, Mapping):
            continue
        asset_id = raw_ref.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id.strip():
            continue
        refs.append(
            {
                "asset_id": asset_id,
                "asset_kind": raw_ref.get("asset_kind"),
                "mime_type": raw_ref.get("mime_type"),
                "filename": raw_ref.get("filename"),
                "size_bytes": raw_ref.get("size_bytes"),
            }
        )
    return refs


def strip_uploaded_document_asset_refs(arguments: Mapping[str, Any] | None) -> dict[str, Any]:
    sanitized_arguments = dict(arguments or {})
    sanitized_arguments.pop(BRIDGE_UPLOADED_DOCUMENTS_ARGUMENT, None)
    return sanitized_arguments


async def resolve_uploaded_document_arguments(
    *,
    session_id: str,
    arguments: Mapping[str, Any] | None,
    asset_store: LocalTemporarySessionAssetStore,
) -> dict[str, Any]:
    resolved_arguments = strip_uploaded_document_asset_refs(arguments)
    refs = extract_uploaded_document_asset_refs(arguments)
    if not refs:
        return resolved_arguments

    resolved_documents: list[dict[str, Any]] = []
    for ref in refs:
        asset_id = str(ref["asset_id"])
        asset = await asset_store.get_asset(session_id=session_id, asset_id=asset_id)
        if asset.kind != "document" or asset.mime_type != "application/pdf":
            raise TemporaryUploadError(
                f"Temporary upload asset '{asset_id}' is not a PDF document for session {session_id}"
            )
        content = await asset_store.read_document_bytes(session_id=session_id, asset_id=asset_id)
        resolved_documents.append(
            {
                "asset_id": asset.asset_id,
                "asset_kind": asset.kind,
                "mime_type": asset.mime_type,
                "filename": asset.filename,
                "size_bytes": asset.size_bytes,
                "encoding": "base64",
                "data_base64": encode_bytes_to_base64(content),
            }
        )

    resolved_arguments[BRIDGE_UPLOADED_DOCUMENTS_ARGUMENT] = resolved_documents
    return resolved_arguments
