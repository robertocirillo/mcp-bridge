from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional

from app.models.responses import (
    PromptArgument,
    PromptInfo,
    PromptRenderMessage,
    ResourceContent,
    ResourceInfo,
)


def model_to_dict(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(exclude_none=True)
        return dumped if isinstance(dumped, dict) else {}
    if hasattr(value, "dict"):
        dumped = value.dict(exclude_none=True)  # type: ignore[call-arg]
        return dumped if isinstance(dumped, dict) else {}
    if hasattr(value, "__dict__"):
        return {
            key: item
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return {}


def extract_items(payload: Any, *keys: str) -> List[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, tuple):
        return list(payload)

    data = model_to_dict(payload)
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, tuple):
            return list(value)

    return []


def pick_value(payload: Any, *keys: str) -> Any:
    if isinstance(payload, dict):
        for key in keys:
            if key in payload and payload[key] is not None:
                return payload[key]

    data = model_to_dict(payload)
    for key in keys:
        value = data.get(key)
        if value is not None:
            return value

    for key in keys:
        value = getattr(payload, key, None)
        if value is not None:
            return value

    return None


def encode_blob(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(value)).decode("ascii")
    return None


def normalize_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {
            str(key): normalize_json_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [normalize_json_value(item) for item in value]
    if isinstance(value, (bytes, bytearray, memoryview)):
        return encode_blob(value)

    normalized = model_to_dict(value)
    if normalized:
        return normalize_json_value(normalized)

    return str(value)


def normalize_message_content(content: Any) -> Dict[str, Any]:
    if content is None:
        return {}
    if isinstance(content, dict):
        return normalize_json_value(content)

    normalized = model_to_dict(content)
    if normalized:
        return normalize_json_value(normalized)

    if isinstance(content, str):
        return {"type": "text", "text": content}

    return {"value": normalize_json_value(content)}


def normalize_prompt_list(raw_result: Any) -> List[PromptInfo]:
    prompts: List[PromptInfo] = []
    for raw_prompt in extract_items(raw_result, "prompts"):
        arguments = []
        for raw_argument in extract_items(raw_prompt, "arguments"):
            name = pick_value(raw_argument, "name")
            if not name:
                continue
            arguments.append(
                PromptArgument(
                    name=str(name),
                    description=pick_value(raw_argument, "description"),
                    required=pick_value(raw_argument, "required"),
                )
            )

        name = pick_value(raw_prompt, "name")
        if not name:
            continue
        prompts.append(
            PromptInfo(
                name=str(name),
                description=pick_value(raw_prompt, "description"),
                arguments=arguments,
            )
        )
    return prompts


def normalize_prompt_render(raw_result: Any) -> tuple[Optional[str], List[PromptRenderMessage]]:
    messages: List[PromptRenderMessage] = []
    for raw_message in extract_items(raw_result, "messages"):
        messages.append(
            PromptRenderMessage(
                role=pick_value(raw_message, "role"),
                content=normalize_message_content(pick_value(raw_message, "content", "message")),
            )
        )
    return pick_value(raw_result, "description"), messages


def normalize_resource_list(raw_result: Any) -> List[ResourceInfo]:
    resources: List[ResourceInfo] = []
    for raw_resource in extract_items(raw_result, "resources"):
        uri = pick_value(raw_resource, "uri")
        if not uri:
            continue
        resources.append(
            ResourceInfo(
                uri=str(uri),
                name=pick_value(raw_resource, "name", "title"),
                description=pick_value(raw_resource, "description"),
                mime_type=pick_value(raw_resource, "mimeType", "mime_type"),
                size=pick_value(raw_resource, "size", "sizeBytes", "size_bytes"),
            )
        )
    return resources


def normalize_resource_read(raw_result: Any) -> List[ResourceContent]:
    contents: List[ResourceContent] = []
    for raw_content in extract_items(raw_result, "contents"):
        data_value = pick_value(raw_content, "data")
        text = pick_value(raw_content, "text")
        if text is None and isinstance(data_value, str):
            text = data_value

        structured = pick_value(
            raw_content,
            "structured",
            "structuredContent",
            "structured_content",
            "json",
        )
        if structured is None and data_value is not None and not isinstance(
            data_value, (str, bytes, bytearray, memoryview)
        ):
            structured = data_value

        blob_value = pick_value(
            raw_content,
            "blob",
            "blobBase64",
            "blob_base64",
            "bytes",
        )
        if blob_value is None and isinstance(data_value, (bytes, bytearray, memoryview)):
            blob_value = data_value

        contents.append(
            ResourceContent(
                uri=pick_value(raw_content, "uri"),
                mime_type=pick_value(raw_content, "mimeType", "mime_type"),
                text=text,
                blob_base64=encode_blob(blob_value),
                structured=normalize_json_value(structured),
            )
        )
    return contents
