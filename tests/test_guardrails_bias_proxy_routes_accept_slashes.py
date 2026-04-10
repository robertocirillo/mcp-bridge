from fastapi.testclient import TestClient

from config import Settings
from main import create_app


def test_bias_proxy_routes_accept_model_ids_with_slashes():
    """HF model ids contain slashes (e.g. org/model).

    The proxy routes must therefore declare `{model_id:path}`; otherwise FastAPI will
    not match the route and will return 404 before reaching the handler.
    """

    # Force the proxy to fail with 503 through the standard settings dependency so we
    # can assert the request actually hits the handler (i.e. route matched) rather than 404.
    client = TestClient(create_app(Settings(BIAS_DETECTOR_SERVICE_BASE_URL="")))

    r = client.get(
        "/v1/guardrails/bias/models/cardiffnlp/twitter-roberta-base-hate-latest/policy"
    )
    assert r.status_code == 503

    r = client.get(
        "/v1/guardrails/bias/models/cardiffnlp/twitter-roberta-base-hate-latest/labels"
    )
    assert r.status_code == 503
