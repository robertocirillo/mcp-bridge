import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)

# Importa le dependency che il router usa davvero
from app.api.dependencies import get_settings, get_a2a_client


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
    def __init__(self, result: DummyResult):
        self._result = result

    async def send_message(self, *args, **kwargs):
        return self._result


@pytest.fixture
def override_deps():
    """
    Override dipendenze FastAPI: settings + a2a_client.
    Ogni test può impostare il result da tornare con override_deps.set_result(...)
    """
    state = {"result": DummyResult(task_id=None, status="succeeded", message="ok", raw_response={"x": 1})}

    def _get_settings_override():
        # rende sempre valido l'agent_id usato nei test
        agents = {"test-agent": DummyConf(enabled=True)}
        return DummySettings(DummyA2ASettings(enabled=True, agents=agents))

    def _get_a2a_client_override():
        return DummyA2AClient(state["result"])

    app.dependency_overrides[get_settings] = _get_settings_override
    app.dependency_overrides[get_a2a_client] = _get_a2a_client_override

    class Ctl:
        def set_result(self, result: DummyResult):
            state["result"] = result

    yield Ctl()

    app.dependency_overrides.clear()


def test_goal_whitespace_returns_400_or_422(override_deps):
    payload = {"goal": "   ", "blocking": True, "metadata": {}}
    r = client.post("/a2a/agents/test-agent/messages", json=payload)
    assert r.status_code in (400, 422), r.text


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
