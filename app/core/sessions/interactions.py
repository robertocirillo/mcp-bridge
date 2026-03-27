"""
Pending interaction and elicitation helpers used by SessionManager.
"""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from app.core.exceptions import (
    QueryOperationElicitationExpiredError,
    QueryOperationElicitationUnavailableError,
    QueryOperationResumeInvalidError,
)
from app.models.requests import QueryOperationResumeRequest
from app.models.responses import (
    QueryOperationInteraction,
    QueryOperationResponse,
    QueryOperationStatus,
)


@dataclass
class PendingElicitation:
    """In-memory continuation for a paused query operation."""

    interaction_id: str
    future: asyncio.Future
    created_at: datetime
    provisional: bool = False


class PendingInteractionStore:
    """In-memory storage for pending elicitations and resume bookkeeping."""

    def __init__(self):
        self.pending_elicitations: Dict[str, Dict[str, PendingElicitation]] = {}

    def get(self, session_id: str, operation_id: str) -> Optional[PendingElicitation]:
        return self.pending_elicitations.get(session_id, {}).get(operation_id)

    def pop_session(self, session_id: str) -> list[PendingElicitation]:
        return list(self.pending_elicitations.pop(session_id, {}).values())

    def clear(
        self,
        *,
        session_id: str,
        operation_id: str,
        interaction_id: Optional[str] = None,
    ) -> None:
        session_pending = self.pending_elicitations.get(session_id)
        if session_pending is None:
            return

        pending = session_pending.get(operation_id)
        if pending is None:
            return
        if interaction_id is not None and pending.interaction_id != interaction_id:
            return

        session_pending.pop(operation_id, None)
        if not session_pending:
            self.pending_elicitations.pop(session_id, None)

    def consume_resume(
        self,
        *,
        session_id: str,
        operation_id: str,
        operation: QueryOperationResponse,
        request: QueryOperationResumeRequest,
    ) -> PendingElicitation:
        pending = self.get(session_id, operation_id)
        interaction = operation.pending_interaction
        if pending is None or interaction is None or not operation.requires_input:
            if operation.status in {
                QueryOperationStatus.completed,
                QueryOperationStatus.failed,
                QueryOperationStatus.cancelled,
            }:
                raise QueryOperationElicitationExpiredError(
                    f"Elicitation is no longer available for query operation {operation_id}"
                )
            raise QueryOperationElicitationUnavailableError(
                f"No pending elicitation is available for query operation {operation_id}"
            )

        validate_resume_request(request=request, interaction_id=interaction.interaction_id)

        preserve_pending = pending.provisional and is_provisional_interaction(interaction)
        if not preserve_pending:
            self.clear(session_id=session_id, operation_id=operation_id)

        operation.requires_input = False
        operation.pending_interaction = None
        operation.metadata.updated_at = datetime.now()

        if request.action in {"accept", "decline"}:
            operation.status = QueryOperationStatus.running

        return pending

    def begin_wrapper_elicitation(
        self,
        *,
        session_id: str,
        operation_id: str,
        operation: QueryOperationResponse,
        payload: Dict[str, Any],
    ) -> tuple[asyncio.Future, QueryOperationInteraction]:
        request_context = payload.get("request_context", {})
        interaction = QueryOperationInteraction(
            interaction_id=str(uuid.uuid4()),
            message=str(payload.get("message") or ""),
            requested_schema=payload.get("requested_schema"),
            requested_at=datetime.now(),
            details={
                "server_name": payload.get("server_name"),
                "request_context": request_context,
            },
        )
        future = asyncio.get_running_loop().create_future()
        pending = PendingElicitation(
            interaction_id=interaction.interaction_id,
            future=future,
            created_at=interaction.requested_at,
        )

        existing = self.pending_elicitations.setdefault(session_id, {}).get(operation_id)
        if existing is not None and existing.provisional:
            current_interaction = operation.pending_interaction
            interaction.interaction_id = existing.interaction_id
            interaction.requested_at = existing.created_at
            if current_interaction is not None:
                interaction.details = {
                    **current_interaction.details,
                    "server_name": payload.get("server_name"),
                    "request_context": request_context,
                    "provisional": False,
                }
            future = existing.future
            pending = PendingElicitation(
                interaction_id=existing.interaction_id,
                future=existing.future,
                created_at=existing.created_at,
                provisional=False,
            )
        elif existing is not None and not existing.future.done():
            existing.future.cancel()

        self.pending_elicitations.setdefault(session_id, {})[operation_id] = pending
        operation.status = QueryOperationStatus.input_required
        operation.requires_input = True
        operation.pending_interaction = interaction
        operation.result = None
        operation.error = None
        operation.metadata.updated_at = datetime.now()
        return future, interaction

    def apply_task_status(
        self,
        *,
        session_id: str,
        operation_id: str,
        operation: QueryOperationResponse,
        payload: Dict[str, Any],
    ) -> None:
        status = str(payload.get("status") or "").strip().lower()
        if not status:
            return
        if operation.status in {
            QueryOperationStatus.completed,
            QueryOperationStatus.failed,
            QueryOperationStatus.cancelled,
        }:
            return

        session_pending = self.pending_elicitations.setdefault(session_id, {})
        pending = session_pending.get(operation_id)
        now = datetime.now()

        task_details = {
            "task_id": payload.get("task_id"),
            "server_name": payload.get("server_name"),
            "poll_interval": payload.get("poll_interval"),
            "ttl": payload.get("ttl"),
            "created_at": payload.get("created_at"),
            "last_updated_at": payload.get("last_updated_at"),
            "source": "task-status-notification",
        }
        task_details = {key: value for key, value in task_details.items() if value is not None}

        if status == "working":
            operation.status = QueryOperationStatus.running
            operation.requires_input = False
            operation.result = None
            operation.error = None
            if pending is not None and pending.provisional:
                session_pending.pop(operation_id, None)
                if not session_pending:
                    self.pending_elicitations.pop(session_id, None)
            operation.pending_interaction = None
            operation.metadata.updated_at = now
            return

        if status != "input_required":
            return

        interaction = operation.pending_interaction
        if pending is None:
            task_id = str(payload.get("task_id") or uuid.uuid4())
            future = asyncio.get_running_loop().create_future()
            pending = PendingElicitation(
                interaction_id=f"task-status:{task_id}",
                future=future,
                created_at=now,
                provisional=True,
            )
            session_pending[operation_id] = pending

        if interaction is None or is_provisional_interaction(interaction):
            interaction = QueryOperationInteraction(
                interaction_id=pending.interaction_id,
                message=str(payload.get("status_message") or "Task requires input"),
                requested_schema=None,
                requested_at=interaction.requested_at if interaction is not None else pending.created_at,
                details={**task_details, "provisional": True},
            )
        else:
            interaction.details = {
                **interaction.details,
                **task_details,
            }

        operation.status = QueryOperationStatus.input_required
        operation.requires_input = True
        operation.result = None
        operation.error = None
        operation.pending_interaction = interaction
        operation.metadata.updated_at = now


def is_provisional_interaction(interaction: Optional[QueryOperationInteraction]) -> bool:
    return bool(interaction and interaction.details.get("provisional") is True)


def validate_resume_request(
    *,
    request: QueryOperationResumeRequest,
    interaction_id: str,
) -> None:
    if request.interaction_id and request.interaction_id != interaction_id:
        raise QueryOperationElicitationExpiredError(
            f"Elicitation {request.interaction_id} is no longer active"
        )

    if request.action == "accept" and request.content is None:
        raise QueryOperationResumeInvalidError(
            "Resume action 'accept' requires a non-null content payload"
        )

    if request.action in {"decline", "cancel"} and request.content is not None:
        raise QueryOperationResumeInvalidError(
            f"Resume action '{request.action}' must not include a content payload"
        )
