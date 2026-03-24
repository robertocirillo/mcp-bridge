from types import SimpleNamespace

import pytest

from app.core.mcp_wrapper import MCPWrapper


def _build_wrapper(monkeypatch: pytest.MonkeyPatch) -> MCPWrapper:
    monkeypatch.setattr(MCPWrapper, "_import_dependencies", lambda self: None)
    return MCPWrapper(llm_provider="ollama", model="dummy")


def _task_context() -> dict[str, str | None]:
    return {
        "operation_id": "op-1",
        "session_id": "session-1",
        "tenant_id": "tenant-1",
        "run_id": "run-1",
        "server_name": "simple_task_interactive",
        "last_elicitation_action": None,
    }


@pytest.mark.asyncio
async def test_handle_runtime_message_accepts_root_wrapped_task_status(monkeypatch: pytest.MonkeyPatch) -> None:
    wrapper = _build_wrapper(monkeypatch)
    wrapper.set_context(session_id="session-1")
    wrapper._task_operation_contexts["task-1"] = _task_context()

    captured: dict[str, object] = {}

    async def _task_status_handler(*, session_id: str, operation_id: str, payload: dict[str, object]) -> None:
        captured["session_id"] = session_id
        captured["operation_id"] = operation_id
        captured["payload"] = payload

    wrapper.set_task_status_handler(_task_status_handler)

    await wrapper._handle_runtime_message(
        SimpleNamespace(
            root=SimpleNamespace(
                method="notifications/tasks/status",
                params={
                    "taskId": "task-1",
                    "status": "input_required",
                    "statusMessage": "Task requires confirmation",
                },
            )
        )
    )

    assert captured["session_id"] == "session-1"
    assert captured["operation_id"] == "op-1"
    assert captured["payload"] == {
        "task_id": "task-1",
        "status": "input_required",
        "ttl": None,
        "created_at": None,
        "last_updated_at": None,
        "poll_interval": None,
        "status_message": "Task requires confirmation",
        "server_name": "simple_task_interactive",
    }


@pytest.mark.asyncio
async def test_handle_protocol_elicitation_accepts_related_task_key_variants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapper = _build_wrapper(monkeypatch)
    wrapper.set_context(session_id="session-1")
    wrapper._task_operation_contexts["task-1"] = _task_context()

    captured: dict[str, object] = {}

    async def _elicitation_handler(*, session_id: str, operation_id: str, payload: dict[str, object]) -> dict[str, bool]:
        captured["session_id"] = session_id
        captured["operation_id"] = operation_id
        captured["payload"] = payload
        return {"confirmed": True}

    wrapper.set_elicitation_handler(_elicitation_handler)

    result = await wrapper._handle_protocol_elicitation(
        SimpleNamespace(request_id="req-1", meta={"related-task": {"taskId": "task-1"}}),
        SimpleNamespace(
            message="Delete test.txt?",
            requestedSchema={"type": "object"},
            meta={"related-task": {"taskId": "task-1"}},
        ),
    )

    assert captured["session_id"] == "session-1"
    assert captured["operation_id"] == "op-1"
    assert captured["payload"] == {
        "message": "Delete test.txt?",
        "requested_schema": {"type": "object"},
        "request_context": {"request_id": "req-1"},
        "server_name": "simple_task_interactive",
    }
    assert result.action == "accept"
    assert result.content == {"confirmed": True}
