"""
Pydantic models for HTTP requests
"""

from pydantic import BaseModel, Field
from typing import Optional, Dict, List, Any
from ..core.config import SessionConfig


class SessionCreateRequest(SessionConfig):
    """Request to create a new session. For now it is the same as SessionConfig."""
    pass


class QueryRequest(BaseModel):
    """Request to execute a query"""
    query: str = Field(..., min_length=1, description="Query to execute")
    max_steps: Optional[int] = Field(None, gt=0, le=100, description="Override for maximum number of steps")
    server_name: Optional[str] = Field(None, description="Specific server name to use")

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