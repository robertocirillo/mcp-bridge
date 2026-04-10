from fastapi.testclient import TestClient

from config import Settings
from main import create_app


def test_a2a_routes_are_not_mounted_by_default() -> None:
    client = TestClient(create_app(Settings()))

    response = client.get("/a2a/agents")

    assert response.status_code == 404
    openapi = client.get("/openapi.json")
    assert openapi.status_code == 200
    assert "/a2a/agents" not in openapi.json()["paths"]


def test_a2a_routes_mount_when_enabled() -> None:
    client = TestClient(create_app(Settings(A2A_ENABLED=True)))

    response = client.get("/a2a/agents")

    assert response.status_code == 200
