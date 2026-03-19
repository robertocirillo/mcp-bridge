"""
Pydantic models for HTTP requests
"""

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field

from app.models.config import SessionConfig


class SessionCreateRequest(SessionConfig):
    """Request to create a new session. For now it is the same as SessionConfig."""
    pass


class QueryRequest(BaseModel):
    """Request to execute a query"""
    query: str = Field(..., min_length=1, description="Query to execute")
    max_steps: Optional[int] = Field(None, gt=0, le=100, description="Override for maximum number of steps")
    server_name: Optional[str] = Field(None, description="Specific server name to use")


class QueryOperationCreateRequest(QueryRequest):
    """Request to create an asynchronous query operation."""


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
