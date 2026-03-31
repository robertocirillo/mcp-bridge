"""
Pydantic models for HTTP requests
"""

from typing import Any, Dict, Literal, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, model_validator

from app.core.multimodal.policy import MAX_BASE64_IMAGE_DATA_LENGTH, SUPPORTED_IMAGE_MIME_TYPES
from app.core.multimodal.validation import normalize_image_mime_type
from app.models.config import SessionConfig


def _normalize_optional_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_base64_data(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = "".join(value.split())
    return normalized or None


class SessionCreateRequest(SessionConfig):
    """Request to create a new session. For now it is the same as SessionConfig."""
    pass


class ImageInput(BaseModel):
    """Structured image input accepted by multimodal queries."""

    source_type: Literal["url", "base64"] = Field(..., description="How the image is provided")
    url: Optional[str] = Field(None, description="Remote image URL for source_type=url")
    data: Optional[str] = Field(
        None,
        description="Base64-encoded image data for source_type=base64",
        repr=False,
    )
    mime_type: Optional[str] = Field(None, description="MIME type for base64 image input")

    @model_validator(mode="after")
    def validate_source(self) -> "ImageInput":
        self.mime_type = normalize_image_mime_type(self.mime_type)

        if self.mime_type is not None and self.mime_type not in SUPPORTED_IMAGE_MIME_TYPES:
            raise ValueError(
                f"Unsupported mime_type '{self.mime_type}'. Supported values: {sorted(SUPPORTED_IMAGE_MIME_TYPES)}"
            )
        self.data = _normalize_base64_data(self.data)

        if self.source_type == "url":
            if not self.url:
                raise ValueError("Field 'url' is required when source_type='url'")
            parsed = urlsplit(self.url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("Field 'url' must be an absolute http/https URL when source_type='url'")
            if self.data is not None:
                raise ValueError("Field 'data' is not allowed when source_type='url'")
            return self

        if not self.data:
            raise ValueError("Field 'data' is required when source_type='base64'")
        if not self.mime_type:
            raise ValueError("Field 'mime_type' is required when source_type='base64'")
        if self.url is not None:
            raise ValueError("Field 'url' is not allowed when source_type='base64'")
        if len(self.data) > MAX_BASE64_IMAGE_DATA_LENGTH:
            raise ValueError(
                "Field 'data' exceeds the maximum supported base64 length "
                f"of {MAX_BASE64_IMAGE_DATA_LENGTH} characters"
            )
        return self

    def as_data_url(self) -> str:
        if self.source_type != "base64":
            raise ValueError("Data URL conversion is only available for source_type='base64'")
        return f"data:{self.mime_type};base64,{self.data}"


class QueryInputPayload(BaseModel):
    """Structured multimodal query input."""

    text: Optional[str] = Field(None, description="Optional textual input associated with the request")
    images: list[ImageInput] = Field(default_factory=list, description="Optional list of image inputs")

    @model_validator(mode="after")
    def validate_not_empty(self) -> "QueryInputPayload":
        self.text = _normalize_optional_text(self.text)

        if self.text is None and not self.images:
            raise ValueError("At least one of 'text' or 'images' must be provided in 'input'")
        return self


class QueryRequest(BaseModel):
    """Request to execute a query"""
    query: Optional[str] = Field(None, min_length=1, description="Legacy text query to execute")
    input: Optional[QueryInputPayload] = Field(None, description="Structured multimodal input payload")
    max_steps: Optional[int] = Field(None, gt=0, le=100, description="Override for maximum number of steps")
    server_name: Optional[str] = Field(None, description="Specific server name to use")

    @model_validator(mode="after")
    def validate_request_shape(self) -> "QueryRequest":
        self.query = _normalize_optional_text(self.query)
        if self.query is None and self.input is None:
            raise ValueError("At least one of 'query' or 'input' must be provided")
        return self


class QueryOperationCreateRequest(BaseModel):
    """Request to create an asynchronous session operation."""

    query: Optional[str] = Field(None, min_length=1, description="Legacy text query to execute")
    input: Optional[QueryInputPayload] = Field(None, description="Structured multimodal input payload")
    max_steps: Optional[int] = Field(None, gt=0, le=100, description="Override for maximum number of steps")
    server_name: Optional[str] = Field(None, description="Specific server name to use")
    tool_name: Optional[str] = Field(None, min_length=1, description="Direct MCP tool name to invoke")
    arguments: Dict[str, Any] = Field(
        default_factory=dict,
        description="Tool arguments forwarded to the MCP server for direct tool invocation.",
    )

    @model_validator(mode="after")
    def validate_operation_shape(self) -> "QueryOperationCreateRequest":
        self.query = _normalize_optional_text(self.query)
        has_query = self.query is not None or self.input is not None
        has_tool_invocation = self.tool_name is not None

        if has_query == has_tool_invocation:
            raise ValueError("Exactly one of query/input or 'tool_name' must be provided")

        if has_query and self.arguments:
            raise ValueError("Field 'arguments' is only supported when 'tool_name' is provided")

        if has_tool_invocation and self.max_steps is not None:
            raise ValueError("Field 'max_steps' is only supported when query/input is provided")

        return self


class QueryOperationResumeRequest(BaseModel):
    """Request to resume a paused asynchronous query operation."""

    action: Literal["accept", "decline", "cancel"] = Field(
        ...,
        description="How the user responded to the pending elicitation request.",
    )
    content: Optional[Any] = Field(
        default=None,
        description="Structured user response payload. Required for accept.",
    )
    interaction_id: Optional[str] = Field(
        default=None,
        description="Optional interaction identifier to guard against stale resumes.",
    )


class PromptRenderRequest(BaseModel):
    """Request to render/get a prompt from an MCP server."""

    server_name: Optional[str] = Field(
        default=None,
        description="Specific server name to use. Optional only when the session has exactly one MCP server.",
    )
    arguments: Dict[str, Any] = Field(
        default_factory=dict,
        description="Prompt arguments forwarded to the MCP server.",
    )


class ResourceReadRequest(BaseModel):
    """Request to read an MCP resource."""

    uri: str = Field(..., min_length=1, description="Resource URI to read")
    server_name: Optional[str] = Field(
        default=None,
        description="Specific server name to use. Optional only when the session has exactly one MCP server.",
    )

class SessionUpdateRequest(BaseModel):
    """Request to update a session"""
    max_steps: Optional[int] = Field(None, gt=0, le=100, description="New maximum number of steps")
    verbose: Optional[bool] = Field(None, description="New verbose mode")

class A2ATaskRequest(BaseModel):
    """Minimal A2A task request representation used by the bridge."""

    goal: str = Field(..., description="High-level goal or instruction for the agent.")
    input: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Structured input payload for the agent."
    )
    task_id: Optional[str] = Field(
        default=None,
        description="Optional client-provided task identifier."
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional metadata for routing, tenant info, etc."
    )




class A2AMessageRequest(BaseModel):
    """
    High-level request to send a message to an A2A agent.

    This is the REST-facing model used by mcp-bridge. Internally it will be
    mapped to A2A messages/tasks via the python-a2a SDK.
    """

    goal: str = Field(
        ...,
        description="High-level goal or instruction for the agent.",
    )
    input: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional structured input payload for the task.",
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional metadata (channel, tags, etc.).",
    )
    blocking: bool = Field(
        default=True,
        description=(
            "If true, the bridge waits for the agent to complete the task and "
            "returns the final result. If false, the bridge creates an A2A "
            "task and returns immediately with its initial status."
        ),
    )
    client_task_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional client-provided identifier for this logical task. "
            "If not provided, the bridge or the A2A agent will generate one."
        ),
    )
