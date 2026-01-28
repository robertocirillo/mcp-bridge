import json

import pytest
import httpx

from app.core.bias_detector_client import BiasDetectorClient


@pytest.mark.asyncio
async def test_bias_detector_client_includes_unsafe_labels_in_payload() -> None:
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/bias/classify"
        body = json.loads(request.content.decode("utf-8"))
        captured["body"] = body
        return httpx.Response(status_code=200, json={"flagged": False, "labels": [], "flagged_labels": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as async_client:
        client = BiasDetectorClient(base_url="http://test", client=async_client)
        await client.classify(
            text="All immigrants are dangerous",
            unsafe_labels=["HATE"],
            top_k=5,
            threshold=0.8,
        )

    assert captured["body"]["unsafe_labels"] == ["HATE"]
