"""
Pydantic models for HTTP responses
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Literal
from datetime import datetime


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
    status: Literal["pending", "running", "completed", "failed", "unknown"] = "unknown"
    output: Optional[Dict[str, Any]] = None
    message: Optional[str] = None
    raw_response: Dict[str, Any] = Field(
        default_factory=dict,
        description="Raw JSON response from the remote A2A agent."
    )



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
    status: Optional[str] = Field(
        default=None,
        description=(
            "High-level status of the task (e.g. pending, running, completed, "
            "failed, cancelled). Exact values depend on the A2A task status."
        ),
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
    status: str = Field(
        ...,
        description=(
            "Current status of the task (e.g. pending, running, completed, "
            "failed, cancelled). Exact values depend on the A2A backend."
        ),
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
