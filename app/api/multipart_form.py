"""Shared multipart form normalization for query endpoints."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request, UploadFile
from starlette.datastructures import FormData, UploadFile as StarletteUploadFile


def multipart_query_request_body_openapi() -> dict[str, object]:
    return {
        "requestBody": {
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "text": {
                                "type": "string",
                                "title": "Text",
                            },
                            "max_steps": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 100,
                                "title": "Max Steps",
                            },
                            "server_name": {
                                "type": "string",
                                "title": "Server Name",
                            },
                            "images": {
                                "type": "array",
                                "items": {"type": "string", "format": "binary"},
                                "title": "Images",
                            },
                            "documents": {
                                "type": "array",
                                "items": {"type": "string", "format": "binary"},
                                "title": "Documents",
                            },
                        },
                    }
                }
            }
        }
    }


@dataclass(frozen=True)
class NormalizedMultipartQueryForm:
    text: str | None
    max_steps: int | None
    server_name: str | None
    images: list[UploadFile]
    documents: list[UploadFile]
    raw_tool_name_values: list[object]
    raw_arguments_values: list[object]


def _normalize_optional_text(value: object | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Field '{field_name}' must be a string")
    if not value.strip():
        return None
    return value


def _normalize_optional_int(value: object | None, *, field_name: str) -> int | None:
    normalized = _normalize_optional_text(value, field_name=field_name)
    if normalized is None:
        return None
    try:
        return int(normalized)
    except ValueError as exc:
        raise ValueError(f"Field '{field_name}' must be a valid integer") from exc


def _normalize_uploads(form: FormData, *, field_name: str) -> list[UploadFile]:
    normalized: list[UploadFile] = []
    for value in form.getlist(field_name):
        if isinstance(value, StarletteUploadFile) and value.filename:
            normalized.append(value)
    return normalized


async def normalize_multipart_query_form(request: Request) -> NormalizedMultipartQueryForm:
    form = await request.form()
    return NormalizedMultipartQueryForm(
        text=_normalize_optional_text(form.get("text"), field_name="text"),
        max_steps=_normalize_optional_int(form.get("max_steps"), field_name="max_steps"),
        server_name=_normalize_optional_text(form.get("server_name"), field_name="server_name"),
        images=_normalize_uploads(form, field_name="images"),
        documents=_normalize_uploads(form, field_name="documents"),
        raw_tool_name_values=list(form.getlist("tool_name")),
        raw_arguments_values=list(form.getlist("arguments")),
    )
