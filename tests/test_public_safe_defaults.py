from contextlib import asynccontextmanager
import importlib
import json

from fastapi.testclient import TestClient

import config

from config import DEFAULT_CORS_ORIGINS, Settings


def test_settings_default_log_level_is_info():
    assert Settings.model_fields["LOG_LEVEL"].default == "INFO"


def test_settings_default_cors_origins_are_localhost_only():
    assert Settings.model_fields["CORS_ORIGINS"].default == DEFAULT_CORS_ORIGINS
    assert "*" not in DEFAULT_CORS_ORIGINS
    assert "https://localhost" in DEFAULT_CORS_ORIGINS
    assert "https://127.0.0.1" in DEFAULT_CORS_ORIGINS
    assert "https://[::1]" in DEFAULT_CORS_ORIGINS


def _build_app_with_default_cors(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", json.dumps(DEFAULT_CORS_ORIGINS))

    importlib.reload(config)
    main_module = importlib.import_module("main")
    main_module = importlib.reload(main_module)

    @asynccontextmanager
    async def noop_lifespan(_: object):
        yield

    monkeypatch.setattr(main_module.app.router, "lifespan_context", noop_lifespan)
    return main_module.app


def test_app_cors_allows_secure_localhost_origin(monkeypatch):
    app = _build_app_with_default_cors(monkeypatch)

    with TestClient(app) as client:
        response = client.options(
            "/",
            headers={
                "Origin": "https://localhost",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://localhost"


def test_app_cors_blocks_non_default_origin(monkeypatch):
    app = _build_app_with_default_cors(monkeypatch)

    with TestClient(app) as client:
        response = client.options(
            "/",
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
