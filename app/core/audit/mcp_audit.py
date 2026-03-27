from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class AuditEvent:
    event_type: str
    timestamp: str
    tenant_id: Optional[str] = None
    run_id: Optional[str] = None
    session_id: Optional[str] = None
    tool_name: Optional[str] = None
    outcome: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


class InMemoryAuditRecorder:
    """Small in-memory recorder to enable observability without external deps."""

    def __init__(self) -> None:
        self._events: List[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self._events.append(event)

    def list_events(self) -> List[AuditEvent]:
        return list(self._events)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
