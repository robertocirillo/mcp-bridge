from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_a2a_client, get_settings
from app.api.routes.a2a import router as a2a_router
from app.core.a2a_client import A2AClientError, A2AResult


@dataclass
class DummyAgentConf:
    enabled: bool = True
    name: str = "Echo"
    description: str = "Echo agent"
    endpoint: str = "http://example.invalid"
    card_url: Optional[str] = None


@dataclass
class DummyA2ASettings:
    enabled: bool = True
    agents: Dict[str, DummyAgentConf] = field(default_factory=dict)


@dataclass
class DummySettings:
    a2a: DummyA2ASettings


class StubA2AClient:
    def __init__(
        self,
        *,
        send_message_fn: Optional[Callable[..., Any]] = None,
        get_task_fn: Optional[Callable[..., Any]] = None,
    ) -> None:
        self._send_message_fn = send_message_fn
        self._get_task_fn = get_task_fn

    async def send_message(self, **kwargs: Any) -> A2AResult:
        assert self._send_message_fn is not None, "send_message_fn not set"
        return await self._send_message_fn(**kwargs)

    async def get_task(self, **kwargs: Any) -> A2AResult:
        assert self._get_task_fn is not None, "get_task_fn not set"
        return await self._get_task_fn(**kwargs)


def _settings_with_agent(agent_id: str) -> DummySettings:
    return DummySettings(a2a=DummyA2ASettings(enabled=True, agents={agent_id: DummyAgentConf(enabled=True)}))


def make_client(*, stub_client: StubA2AClient, settings: DummySettings) -> TestClient:
    app = FastAPI()
    app.include_router(a2a_router)

    def _get_settings_override() -> DummySettings:
        return settings

    def _get_a2a_client_override() -> StubA2AClient:
        return stub_client

    app.dependency_overrides[get_settings] = _get_settings_override
    app.dependency_overrides[get_a2a_client] = _get_a2a_client_override
    return TestClient(app)


def test_send_message_schema_error_includes_agent_id_and_operation() -> None:
    async def send_message_fn(**kwargs: Any) -> A2AResult:
        # Should never be called due to schema validation.
        return A2AResult(
            agent_id=kwargs["agent_id"],
            task_id=None,
            status=None,
            output=None,
            message=None,
            raw_response=None,
        )

    client = make_client(
        stub_client=StubA2AClient(send_message_fn=send_message_fn),
        settings=_settings_with_agent("echo"),
    )

    resp = client.post("/a2a/agents/echo/messages", json={"goal": "   "})
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "A2A_SCHEMA_ERROR"
    assert detail["agent_id"] == "echo"
    assert detail["operation"] == "send_message"


def test_message_only_blocking_true_mode_blocking_task_id_null() -> None:
    async def send_message_fn(**kwargs: Any) -> A2AResult:
        return A2AResult(
            agent_id=kwargs["agent_id"],
            task_id=None,
            status=None,
            output={"kind": "message-only"},
            message="ok",
            raw_response={"raw": True},
        )

    client = make_client(
        stub_client=StubA2AClient(send_message_fn=send_message_fn),
        settings=_settings_with_agent("echo"),
    )

    resp = client.post("/a2a/agents/echo/messages", json={"goal": "hi", "blocking": True})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["mode"] == "blocking"
    assert data["agent_id"] == "echo"
    assert data["task_id"] is None
    assert data["message"] == "ok"
    assert data["raw_response"] == {"raw": True}


def test_message_only_blocking_false_mode_blocking_task_id_null() -> None:
    async def send_message_fn(**kwargs: Any) -> A2AResult:
        return A2AResult(
            agent_id=kwargs["agent_id"],
            task_id=None,
            status=None,
            output={"kind": "message-only"},
            message="ok",
            raw_response={"raw": True},
        )

    client = make_client(
        stub_client=StubA2AClient(send_message_fn=send_message_fn),
        settings=_settings_with_agent("echo"),
    )

    resp = client.post("/a2a/agents/echo/messages", json={"goal": "hi", "blocking": False})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["mode"] == "blocking"
    assert data["task_id"] is None


def test_task_based_send_and_poll_success_status_normalized() -> None:
    async def send_message_fn(**kwargs: Any) -> A2AResult:
        return A2AResult(
            agent_id=kwargs["agent_id"],
            task_id="t1",
            status="queued",
            output={"kind": "task"},
            message=None,
            raw_response={"raw": True},
        )

    async def get_task_fn(**kwargs: Any) -> A2AResult:
        return A2AResult(
            agent_id=kwargs["agent_id"],
            task_id=kwargs["task_id"],
            status="completed",
            output={"ok": True},
            message=None,
            raw_response={"raw": True},
        )

    client = make_client(
        stub_client=StubA2AClient(send_message_fn=send_message_fn, get_task_fn=get_task_fn),
        settings=_settings_with_agent("echo"),
    )

    resp = client.post("/a2a/agents/echo/messages", json={"goal": "do work", "blocking": False})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["mode"] == "task"
    assert data["task_id"] == "t1"

    poll = client.get("/a2a/agents/echo/tasks/t1")
    assert poll.status_code == 200, poll.text
    pdata = poll.json()
    assert pdata["status"] == "succeeded"


def test_task_not_found_returns_404_with_structured_detail() -> None:
    async def get_task_fn(**kwargs: Any) -> A2AResult:
        raise A2AClientError("Task not found", status_code=404, code="A2A_TASK_NOT_FOUND", upstream={"why": "missing"})

    client = make_client(
        stub_client=StubA2AClient(get_task_fn=get_task_fn),
        settings=_settings_with_agent("echo"),
    )

    resp = client.get("/a2a/agents/echo/tasks/missing")
    assert resp.status_code == 404, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "A2A_TASK_NOT_FOUND"
    assert detail["agent_id"] == "echo"
    assert detail["task_id"] == "missing"
    assert detail["operation"] == "get_task"


def test_task_polling_not_applicable_returns_409_with_structured_detail() -> None:
    async def get_task_fn(**kwargs: Any) -> A2AResult:
        raise A2AClientError(
            "Method not found",
            status_code=501,
            code="A2A_TASK_NOT_APPLICABLE",
            upstream={"error": {"code": -32601, "message": "Method not found"}},
        )

    client = make_client(
        stub_client=StubA2AClient(get_task_fn=get_task_fn),
        settings=_settings_with_agent("echo"),
    )

    resp = client.get("/a2a/agents/echo/tasks/whatever")
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "A2A_TASK_NOT_APPLICABLE"
    assert detail["agent_id"] == "echo"
    assert detail["task_id"] == "whatever"
    assert detail["operation"] == "get_task"


def test_transport_timeout_returns_504_with_structured_detail() -> None:
    async def get_task_fn(**kwargs: Any) -> A2AResult:
        raise A2AClientError(
            "Timed out contacting agent",
            status_code=504,
            code="A2A_UPSTREAM_ERROR",
            upstream={"exception": "timeout"},
        )

    client = make_client(
        stub_client=StubA2AClient(get_task_fn=get_task_fn),
        settings=_settings_with_agent("echo"),
    )

    resp = client.get("/a2a/agents/echo/tasks/t1")
    assert resp.status_code == 504, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "A2A_UPSTREAM_ERROR"
    assert detail["agent_id"] == "echo"
    assert detail["task_id"] == "t1"
    assert detail["operation"] == "get_task"
