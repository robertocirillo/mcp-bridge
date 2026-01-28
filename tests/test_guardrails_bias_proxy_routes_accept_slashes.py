from fastapi.testclient import TestClient


def test_bias_proxy_routes_accept_model_ids_with_slashes(monkeypatch):
    """HF model ids contain slashes (e.g. org/model).

    The proxy routes must therefore declare `{model_id:path}`; otherwise FastAPI will
    not match the route and will return 404 before reaching the handler.
    """

    # Import here so monkeypatch can adjust the module-level settings used by the router.
    import main
    from app.api.routes import guardrails_bias

    # Force the proxy to fail with 503 (service not configured) so we can assert
    # the request actually hits the handler (i.e. route matched) rather than 404.
    monkeypatch.setattr(guardrails_bias.settings, "BIAS_DETECTOR_SERVICE_BASE_URL", "")

    client = TestClient(main.app)

    r = client.get(
        "/v1/guardrails/bias/models/cardiffnlp/twitter-roberta-base-hate-latest/policy"
    )
    assert r.status_code == 503

    r = client.get(
        "/v1/guardrails/bias/models/cardiffnlp/twitter-roberta-base-hate-latest/labels"
    )
    assert r.status_code == 503
