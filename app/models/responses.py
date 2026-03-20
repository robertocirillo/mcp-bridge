"""
Pydantic models for HTTP responses
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class A2ATaskState(str, Enum):
    """Standard A2A task state values (A2A TaskState)."""

    submitted = "submitted"
    working = "working"
    input_required = "input-required"
    completed = "completed"
    canceled = "canceled"
    failed = "failed"
    unknown = "unknown"


A2A_TERMINAL_TASK_STATES = {A2ATaskState.completed, A2ATaskState.canceled, A2ATaskState.failed}



class SessionResponse(BaseModel):
    """Response for session creation"""
    session_id: str
    status: str = Field(..., description="Session status")
    message: str = Field(..., description="Informative message")
    servers: List[str] = Field(..., description="List of configured MCP servers")

class QueryResponse(BaseModel):
    """Response for query execution"""
    session_id: str
    result: str = Field(..., description="Execution result")
    execution_time: float = Field(..., description="Execution time in seconds")
    steps_used: int = Field(..., description="Number of steps used")
    timestamp: datetime = Field(..., description="Execution timestamp")
    server_used: Optional[str] = Field(None, description="Server used for execution")
    has_mcp_servers: Optional[bool] = Field(None, description="True if session configured with one or more mcp servers")


class QueryOperationStatus(str, Enum):
    """Lifecycle states for asynchronous query operations."""

    queued = "queued"
    running = "running"
    input_required = "input-required"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class QueryOperationInput(BaseModel):
    """Snapshot of the request payload associated with an operation."""

    query: str = Field(..., description="Query to execute")
    max_steps: Optional[int] = Field(None, description="Optional max steps override")
    server_name: Optional[str] = Field(None, description="Specific server name to use")


class QueryOperationToolInput(BaseModel):
    """Snapshot of a direct MCP tool invocation associated with an operation."""

    server_name: Optional[str] = Field(None, description="Specific server name to use")
    tool_name: str = Field(..., description="Direct MCP tool name to invoke")
    arguments: Dict[str, Any] = Field(default_factory=dict, description="Tool arguments")


class QueryOperationMetadata(BaseModel):
    """Stable metadata for a query operation."""

    created_at: datetime = Field(..., description="Operation creation timestamp")
    updated_at: datetime = Field(..., description="Last operation update timestamp")
    request: QueryOperationInput | QueryOperationToolInput = Field(
        ...,
        description="Original request snapshot.",
    )


class QueryOperationResult(BaseModel):
    """Terminal result payload for a completed operation."""

    result: Any = Field(..., description="Execution result")
    execution_time: float = Field(..., description="Execution time in seconds")
    steps_used: int = Field(..., description="Number of steps used")
    timestamp: datetime = Field(..., description="Execution timestamp")
    server_used: Optional[str] = Field(None, description="Server used for execution")
    has_mcp_servers: Optional[bool] = Field(None, description="True if session configured with one or more MCP servers")


class QueryOperationError(BaseModel):
    """Serialized failure details for a failed operation."""

    code: str = Field(..., description="Stable error code")
    message: str = Field(..., description="Human-readable error message")
    details: Dict[str, Any] = Field(default_factory=dict, description="Optional structured error details")


class QueryOperationInteraction(BaseModel):
    """Serialized pending interaction payload exposed by query operations."""

    interaction_id: str = Field(..., description="Stable interaction identifier")
    kind: Literal["elicitation"] = Field("elicitation", description="Pending interaction kind")
    message: str = Field(..., description="User-facing elicitation message")
    requested_schema: Optional[Dict[str, Any]] = Field(
        None,
        description="Optional JSON schema describing the expected structured input.",
    )
    requested_at: datetime = Field(..., description="Timestamp when the elicitation was raised")
    actions: List[str] = Field(
        default_factory=lambda: ["accept", "decline", "cancel"],
        description="Allowed resume actions for this interaction.",
    )
    details: Dict[str, Any] = Field(default_factory=dict, description="Additional interaction metadata")


class QueryOperationResponse(BaseModel):
    """Response for asynchronous query operations."""

    operation_id: str = Field(..., description="Operation identifier")
    session_id: str = Field(..., description="Session identifier")
    status: QueryOperationStatus = Field(..., description="Current operation status")
    metadata: QueryOperationMetadata = Field(..., description="Operation metadata")
    result: Optional[QueryOperationResult] = Field(None, description="Completed operation result")
    error: Optional[QueryOperationError] = Field(None, description="Failure details for failed operations")
    requires_input: bool = Field(False, description="True when the operation is waiting for user input")
    pending_interaction: Optional[QueryOperationInteraction] = Field(
        None,
        description="Serialized pending elicitation payload, when the operation is paused for input.",
    )


class SessionInfo(BaseModel):
    """Detailed information about a session"""
    session_id: str
    status: str = Field(..., description="Session status")
    created_at: datetime = Field(..., description="Creation date/time")
    last_used: datetime = Field(..., description="Last used date/time")
    query_count: int = Field(..., description="Number of queries executed")
    servers: List[str] = Field(..., description="Configured MCP servers")
    llm_provider: str = Field(..., description="LLM provider used")
    llm_model: str = Field(..., description="LLM model used")


class PromptArgument(BaseModel):
    """Prompt argument metadata exposed by the MCP server."""

    name: str = Field(..., description="Argument name")
    description: Optional[str] = Field(None, description="Optional argument description")
    required: Optional[bool] = Field(None, description="Whether the argument is required")


class PromptInfo(BaseModel):
    """Prompt metadata exposed by the MCP server."""

    name: str = Field(..., description="Prompt name")
    description: Optional[str] = Field(None, description="Optional prompt description")
    arguments: List[PromptArgument] = Field(default_factory=list, description="Prompt arguments metadata")


class PromptListResponse(BaseModel):
    """List of prompts available for a session/server."""

    session_id: str = Field(..., description="Session identifier")
    server_name: str = Field(..., description="Resolved MCP server name")
    prompts: List[PromptInfo] = Field(default_factory=list, description="Available prompts")


class PromptRenderMessage(BaseModel):
    """Single message returned by MCP prompt rendering."""

    role: Optional[str] = Field(None, description="Message role, if provided by the server")
    content: Dict[str, Any] = Field(default_factory=dict, description="Normalized MCP message content")


class PromptRenderResponse(BaseModel):
    """Rendered prompt payload."""

    session_id: str = Field(..., description="Session identifier")
    server_name: str = Field(..., description="Resolved MCP server name")
    prompt_name: str = Field(..., description="Prompt name")
    description: Optional[str] = Field(None, description="Optional rendered prompt description")
    messages: List[PromptRenderMessage] = Field(default_factory=list, description="Rendered prompt messages")


class ResourceInfo(BaseModel):
    """Resource metadata exposed by the MCP server."""

    uri: str = Field(..., description="Resource URI")
    name: Optional[str] = Field(None, description="Human-readable resource name")
    description: Optional[str] = Field(None, description="Optional resource description")
    mime_type: Optional[str] = Field(None, description="Declared MIME type")
    size: Optional[int] = Field(None, description="Optional size in bytes")


class ResourceListResponse(BaseModel):
    """List of resources available for a session/server."""

    session_id: str = Field(..., description="Session identifier")
    server_name: str = Field(..., description="Resolved MCP server name")
    resources: List[ResourceInfo] = Field(default_factory=list, description="Available resources")


class ResourceContent(BaseModel):
    """Explicit representation of resource content."""

    uri: Optional[str] = Field(None, description="Content URI, when provided by the server")
    mime_type: Optional[str] = Field(None, description="Content MIME type")
    text: Optional[str] = Field(None, description="Decoded textual content")
    blob_base64: Optional[str] = Field(None, description="Base64-encoded binary content")
    structured: Optional[Any] = Field(None, description="Structured/JSON-like payload")


class ResourceReadResponse(BaseModel):
    """Read result for an MCP resource."""

    session_id: str = Field(..., description="Session identifier")
    server_name: str = Field(..., description="Resolved MCP server name")
    uri: str = Field(..., description="Requested resource URI")
    contents: List[ResourceContent] = Field(default_factory=list, description="Explicit resource contents")

class HealthResponse(BaseModel):
    """Response for health check"""
    status: str = Field(..., description="Service status")
    timestamp: datetime = Field(..., description="Check timestamp")
    active_sessions: int = Field(..., description="Number of active sessions")
    supported_providers: List[str] = Field(..., description="Supported LLM providers")
    features: Dict[str, Any] = Field(..., description="Available features")

class ErrorResponse(BaseModel):
    """Response for errors"""
    error: str = Field(..., description="Error type")
    message: str = Field(..., description="Error message")
    details: Optional[Dict[str, Any]] = Field(None, description="Additional details")
    timestamp: datetime = Field(default_factory=datetime.now, description="Error timestamp")

class SessionStatsResponse(BaseModel):
    """Session statistics"""
    total_sessions: int = Field(..., description="Total sessions created")
    active_sessions: int = Field(..., description="Active sessions")
    total_queries: int = Field(..., description="Total queries executed")
    avg_execution_time: float = Field(..., description="Average execution time")
    providers_usage: Dict[str, int] = Field(..., description="Usage per provider")

class A2AAgentInfo(BaseModel):
    """Information about a configured remote A2A agent."""

    agent_id: str
    name: str
    description: Optional[str] = None
    base_url: str
    capabilities: Optional[List[str]] = None


class A2ATaskResponse(BaseModel):
    """Minimal wrapper around a remote A2A task response."""

    task_id: str = Field(..., description="Task identifier (from remote or local).")
    status: A2ATaskState = Field(
        default=A2ATaskState.unknown,
        description=(
            "A2A task state as defined by the A2A standard: "
            "submitted|working|input-required|completed|canceled|failed|unknown."
        ),
    )
    upstream_state: Optional[str] = Field(
        default=None,
        description="Raw upstream task state (may include non-standard values).",
    )
    is_terminal: Optional[bool] = Field(
        default=None,
        description="True if the task is in a terminal A2A state (completed|failed|canceled).",
    )
    output: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional simplified representation of the task output.",
    )
    message: Optional[str] = Field(
        default=None,
        description="Optional human-readable message describing the current state.",
    )
    raw_response: Dict[str, Any] = Field(
        default_factory=dict,
        description="Raw JSON response from the remote A2A agent.",
    )

    @model_validator(mode="after")
    def _populate_hybrid_fields(self):
        if self.upstream_state is None:
            self.upstream_state = self.status.value if isinstance(self.status, A2ATaskState) else str(self.status)
        if self.is_terminal is None:
            try:
                st = self.status if isinstance(self.status, A2ATaskState) else A2ATaskState(str(self.status))
                self.is_terminal = st in A2A_TERMINAL_TASK_STATES
            except Exception:
                self.is_terminal = False
        return self


class A2AAgentSummary(BaseModel):
    """
    Summary information about an A2A agent, returned by the REST API.

    This is derived from the agent's A2A Agent Card plus local configuration.
    """

    agent_id: str = Field(
        ...,
        description="Logical identifier of the agent inside mcp-bridge.",
    )
    name: str = Field(
        ...,
        description="Human-readable agent name (from the Agent Card or config label).",
    )
    description: Optional[str] = Field(
        default=None,
        description="Optional human-readable description of the agent.",
    )
    card_url: Optional[str] = Field(
        default=None,
        description="URL of the agent's A2A Agent Card.",
    )
    skills: List[str] = Field(
        default_factory=list,
        description="Optional list of skill names exposed by the agent.",
    )
    labels: List[str] = Field(
        default_factory=list,
        description="Optional labels/tags for UI grouping or filtering.",
    )


class A2AMessageResponse(BaseModel):
    """
    Response for POST /a2a/agents/{agent_id}/messages.

    - mode = 'blocking': the agent has completed the task and output is final.
    - mode = 'task': a long-running task has been created; use the task_id
      with GET /a2a/agents/{agent_id}/tasks/{task_id} to check status.
    """

    mode: Literal["blocking", "task"] = Field(
        ...,
        description="Indicates whether the call was handled in blocking or task mode.",
    )
    agent_id: str = Field(
        ...,
        description="Logical identifier of the agent that handled the message.",
    )
    task_id: Optional[str] = Field(
        default=None,
        description=(
            "Identifier of the underlying A2A task, if available. "
            "Always present in 'task' mode; may also be present in 'blocking' mode."
        ),
    )
    status: Optional[A2ATaskState] = Field(
        default=None,
        description=(
            "A2A task state as defined by the A2A standard: "
            "submitted|working|input-required|completed|canceled|failed|unknown. "
            "May be null when the upstream response does not include task status."
        ),
    )
    upstream_state: Optional[str] = Field(
        default=None,
        description="Raw upstream task state (may include non-standard values).",
    )
    is_terminal: Optional[bool] = Field(
        default=None,
        description="True if the task is in a terminal A2A state (completed|failed|canceled).",
    )
    output: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Simplified, UI-friendly representation of the agent output "
            "(e.g. main text, structured payload)."
        ),
    )
    message: Optional[str] = Field(
        default=None,
        description="Optional human-readable message describing the result.",
    )
    raw_response: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Raw A2A response object (Task, Message, etc.) serialized as a dict. "
            "Useful for debugging or advanced clients."
        ),
    )

    @model_validator(mode="after")
    def _populate_hybrid_fields(self):
        if self.status is None:
            return self
        if self.upstream_state is None:
            self.upstream_state = self.status.value if isinstance(self.status, A2ATaskState) else str(self.status)
        if self.is_terminal is None:
            try:
                st = self.status if isinstance(self.status, A2ATaskState) else A2ATaskState(str(self.status))
                self.is_terminal = st in A2A_TERMINAL_TASK_STATES
            except Exception:
                self.is_terminal = False
        return self


class A2ATaskStatusResponse(BaseModel):
    """
    Response for GET /a2a/agents/{agent_id}/tasks/{task_id}.

    Provides the current status of an A2A task and, if available, its output.
    """

    agent_id: str = Field(
        ...,
        description="Logical identifier of the agent that owns the task.",
    )
    task_id: str = Field(
        ...,
        description="Identifier of the A2A task.",
    )
    status: A2ATaskState = Field(
        ...,
        description=(
            "A2A task state as defined by the A2A standard: "
            "submitted|working|input-required|completed|canceled|failed|unknown."
        ),
    )
    upstream_state: Optional[str] = Field(
        default=None,
        description="Raw upstream task state (may include non-standard values).",
    )
    is_terminal: Optional[bool] = Field(
        default=None,
        description="True if the task is in a terminal A2A state (completed|failed|canceled).",
    )
    output: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Simplified representation of the task's output, if the task "
            "has produced a result."
        ),
    )
    message: Optional[str] = Field(
        default=None,
        description="Optional human-readable message describing the current state.",
    )
    raw_response: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Raw A2A task object (Task, TaskStatus, etc.) serialized as a dict. "
            "Useful for debugging or advanced clients."
        ),
    )

    @model_validator(mode="after")
    def _populate_hybrid_fields(self):
        if self.upstream_state is None:
            self.upstream_state = self.status.value if isinstance(self.status, A2ATaskState) else str(self.status)
        if self.is_terminal is None:
            try:
                st = self.status if isinstance(self.status, A2ATaskState) else A2ATaskState(str(self.status))
                self.is_terminal = st in A2A_TERMINAL_TASK_STATES
            except Exception:
                self.is_terminal = False
        return self
