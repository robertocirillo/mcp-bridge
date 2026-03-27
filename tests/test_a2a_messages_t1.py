import pytest
from fastapi.testclient import TestClient

from main import app
from app.api.dependencies import get_settings, get_a2a_client
from app.core.clients.a2a_client import A2AClientError

client = TestClient(app)


class DummyResult:
    def __init__(self, task_id=None, status=None, output=None, message=None, raw_response=None):
        self.task_id = task_id
        self.status = status
        self.output = output
        self.message = message
        self.raw_response = raw_response


class DummyConf:
    def __init__(self, enabled=True, label=None, description=None, card_url=None):
        self.enabled = enabled
        self.label = label
        self.description = description
        self.card_url = card_url


class DummyA2ASettings:
    def __init__(self, enabled=True, agents=None):
        self.enabled = enabled
        self.agents = agents or {}


class DummySettings:
    def __init__(self, a2a_settings):
        self.a2a = a2a_settings


class DummyA2AClient:
    """Test double for A2AClient.

    It can either:
    - return a configured DummyResult, or
    - raise a configured exception for send_message / get_task.
    """

    def __init__(self, state: dict):
        self._state = state

    async def send_message(self, *args, **kwargs):
        exc = self._state.get("send_exc")
        if exc is not None:
            raise exc
        return self._state["result"]

    async def get_task(self, *args, **kwargs):
        exc = self._state.get("task_exc")
        if exc is not None:
            raise exc
        return self._state["task_result"]


@pytest.fixture
def override_deps():
    """Override FastAPI dependencies: settings + a2a_client.

    Each test can control:
    - A2A enabled / agent presence
    - send_message / get_task result
    - exceptions raised by send_message / get_task
    """
    state = {
        "a2a_enabled": True,
        "agent_present": True,
        "agent_enabled": True,
        "result": DummyResult(task_id=None, status="succeeded", message="ok", raw_response={"x": 1}),
        "task_result": DummyResult(task_id="t1", status="queued", output={"task": "t1"}, raw_response={"task": "t1"}),
        "send_exc": None,
        "task_exc": None,
    }

    def _get_settings_override():
        agents = {}
        if state["agent_present"]:
            agents["test-agent"] = DummyConf(enabled=state["agent_enabled"], card_url="http://example.test/card.json")
        return DummySettings(DummyA2ASettings(enabled=state["a2a_enabled"], agents=agents))

    def _get_a2a_client_override():
        return DummyA2AClient(state)

    app.dependency_overrides[get_settings] = _get_settings_override
    app.dependency_overrides[get_a2a_client] = _get_a2a_client_override

    class Ctl:
        def set_result(self, result: DummyResult):
            state["result"] = result
            state["send_exc"] = None

        def set_task_result(self, result: DummyResult):
            state["task_result"] = result
            state["task_exc"] = None

        def set_send_exc(self, exc: Exception):
            state["send_exc"] = exc

        def set_task_exc(self, exc: Exception):
            state["task_exc"] = exc

        def set_a2a_enabled(self, enabled: bool):
            state["a2a_enabled"] = enabled

        def set_agent_present(self, present: bool):
            state["agent_present"] = present

        def set_agent_enabled(self, enabled: bool):
            state["agent_enabled"] = enabled

    yield Ctl()

    app.dependency_overrides.clear()


# -------------------------
# T1 behaviour tests
# -------------------------

def test_goal_whitespace_returns_schema_error(override_deps):
    payload = {"goal": "   ", "blocking": True, "metadata": {}}
    r = client.post("/a2a/agents/test-agent/messages", json=payload)
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["code"] == "A2A_SCHEMA_ERROR"
    assert "goal" in detail.get("message", "")


def test_message_only_maps_to_blocking_mode(override_deps):
    override_deps.set_result(DummyResult(task_id=None, status="succeeded", message="ok", raw_response={"x": 1}))
    payload = {"goal": "hello", "blocking": False, "metadata": {"k": "v"}}

    r = client.post("/a2a/agents/test-agent/messages", json=payload)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["task_id"] is None
    assert data["mode"] == "blocking"


def test_task_id_maps_to_task_mode(override_deps):
    override_deps.set_result(DummyResult(task_id="t1", status="queued", raw_response={"task": "t1"}))
    payload = {"goal": "hello", "blocking": False, "metadata": {}}

    r = client.post("/a2a/agents/test-agent/messages", json=payload)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["task_id"] == "t1"
    assert data["mode"] == "task"


def test_empty_task_id_string_normalizes_to_none_and_blocking(override_deps):
    override_deps.set_result(DummyResult(task_id="   ", status="queued", raw_response={"task": ""}))
    payload = {"goal": "hello", "blocking": False, "metadata": {}}

    r = client.post("/a2a/agents/test-agent/messages", json=payload)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["task_id"] is None
    assert data["mode"] == "blocking"


# -------------------------
# T2 minimal error visibility tests
# -------------------------

def test_a2a_disabled_returns_structured_error(override_deps):
    override_deps.set_a2a_enabled(False)
    payload = {"goal": "hello", "blocking": True, "metadata": {}}

    r = client.post("/a2a/agents/test-agent/messages", json=payload)
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "A2A_DISABLED"
    assert "disabled" in detail["message"].lower()


def test_unknown_agent_returns_structured_error(override_deps):
    override_deps.set_agent_present(False)
    payload = {"goal": "hello", "blocking": True, "metadata": {}}

    r = client.post("/a2a/agents/test-agent/messages", json=payload)
    assert r.status_code == 404, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "A2A_AGENT_NOT_FOUND"
    assert detail.get("agent_id") == "test-agent"


def test_a2a_client_error_is_mapped_to_status_code_and_detail(override_deps):
    override_deps.set_send_exc(
        A2AClientError(
            "upstream boom",
            status_code=502,
            code="A2A_CONNECT_ERROR",
            upstream={"reason": "boom"},
        )
    )
    payload = {"goal": "hello", "blocking": True, "metadata": {}}

    r = client.post("/a2a/agents/test-agent/messages", json=payload)
    assert r.status_code == 502, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "A2A_CONNECT_ERROR"
    assert detail["message"] == "upstream boom"
    assert detail.get("agent_id") == "test-agent"
    assert detail.get("upstream") == {"reason": "boom"}


def test_a2a_task_client_error_is_mapped_and_includes_task_id(override_deps):
    override_deps.set_task_exc(
        A2AClientError(
            "task not found upstream",
            status_code=502,
            code="A2A_UPSTREAM_ERROR",
            upstream={"http_status": 404},
        )
    )

    r = client.get("/a2a/agents/test-agent/tasks/t123")
    assert r.status_code == 502, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "A2A_UPSTREAM_ERROR"
    assert detail["message"] == "task not found upstream"
    assert detail.get("agent_id") == "test-agent"
    assert detail.get("task_id") == "t123"
    assert detail.get("upstream") == {"http_status": 404}
