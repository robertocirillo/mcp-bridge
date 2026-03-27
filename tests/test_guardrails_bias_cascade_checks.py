import pytest

from app.core.runtime.mcp_wrapper import GuardrailContext, GuardrailViolationError, make_bias_after_model_guardrail_service


class RecordingBiasClient:
    """Fake bias-detector-service client that records calls and returns deterministic outputs."""

    def __init__(self):
        self.calls = []

    async def classify(self, **kwargs):
        # Record the call for assertions.
        self.calls.append(dict(kwargs))

        model_id = kwargs.get("model_id")
        revision = kwargs.get("revision") or ""
        threshold = kwargs.get("threshold", 0.5)
        unsafe_labels = kwargs.get("unsafe_labels")

        text = (kwargs.get("text") or "").lower()

        # Deterministic behavior:
        # - model_b flags TOXIC
        # - model_a flags HATE only when unsafe_labels explicitly includes HATE
        if model_id == "model_b":
            flagged = True
            flagged_labels = ["TOXIC"]
            labels = [
                {"label": "TOXIC", "score": 0.9, "is_flagged": True},
                {"label": "OK", "score": 0.1, "is_flagged": False},
            ]
            meta = {"threshold": threshold}
            return {
                "model_id": "model_b",
                "revision": revision,
                "flagged": flagged,
                "flagged_labels": flagged_labels,
                "labels": labels,
                "meta": meta,
            }

        # Default: model_a
        if unsafe_labels == ["HATE"] and "hate" in text:
            flagged = True
            flagged_labels = ["HATE"]
            labels = [
                {"label": "HATE", "score": 0.9967, "is_flagged": True},
                {"label": "NOT-HATE", "score": 0.0033, "is_flagged": False},
            ]
        else:
            flagged = False
            flagged_labels = []
            labels = [
                {"label": "HATE", "score": 0.1, "is_flagged": False},
                {"label": "NOT-HATE", "score": 0.9, "is_flagged": False},
            ]

        meta = {"threshold": threshold}
        return {
            "model_id": model_id or "model_a",
            "revision": revision,
            "flagged": flagged,
            "flagged_labels": flagged_labels,
            "labels": labels,
            "meta": meta,
        }


class NullEchoBiasClient(RecordingBiasClient):
    """Fake client that omits model_id/revision in the upstream response.

    This simulates bias-detector-service responses that don't echo model_id/revision
    even when the request includes them.
    """

    async def classify(self, **kwargs):
        resp = await super().classify(**kwargs)
        resp["model_id"] = None
        resp["revision"] = None
        return resp


@pytest.mark.asyncio
async def test_bias_guardrail_cascaded_checks_runs_all_checks_and_reports_results():
    client = RecordingBiasClient()

    guardrail = make_bias_after_model_guardrail_service(
        client=client,
        mode="block",
        threshold=0.5,
        top_k=2,
        active_categories=None,
        unsafe_labels=None,
        model_id="model_a",
        revision="",
        return_all_scores=True,
        return_char_spans=True,
        checks=[
            {"name": "A_hate", "unsafe_labels": ["HATE"]},
            {"name": "A_not_hate_should_not_block", "unsafe_labels": ["NOT-HATE"]},
            {"name": "B_toxic", "model_id": "model_b", "unsafe_labels": ["TOXIC"], "threshold": 0.7},
        ],
        fail_closed=True,
    )

    ctx = GuardrailContext()
    with pytest.raises(GuardrailViolationError) as exc:
        await guardrail(ctx, "Final Answer: I hate you.")

    err = exc.value
    assert err.code == "BIAS_DETECTED"
    assert err.http_status == 403

    # All checks executed even if the first one flags.
    assert len(client.calls) == 3

    # Global request flags forwarded to every classify call.
    assert all(call.get("return_all_scores") is True for call in client.calls)
    assert all(call.get("return_char_spans") is True for call in client.calls)

    details = err.details
    assert "checks_results" in details
    assert len(details["checks_results"]) == 3

    # Backward-compatible top-level fields come from the first flagged check.
    assert details["flagged_labels"] == ["HATE"]
    assert details["flagged_label_scores"][0]["label"] == "HATE"
    assert details["flagged_label_scores"][0]["score_pct"] == pytest.approx(99.67)

    # Per-check results
    r0 = details["checks_results"][0]
    assert r0["request"]["return_all_scores"] is True
    assert r0["request"]["return_char_spans"] is True
    r1 = details["checks_results"][1]
    r2 = details["checks_results"][2]

    assert r0["name"] == "A_hate"
    assert r0["response"]["flagged"] is True
    assert r0["response"]["flagged_labels"] == ["HATE"]
    assert r0["response"]["model_id"] == "model_a"
    assert r0["response"]["revision"] == ""

    assert r1["name"] == "A_not_hate_should_not_block"
    assert r1["response"]["flagged"] is False

    assert r2["name"] == "B_toxic"
    assert r2["response"]["flagged"] is True
    assert r2["response"]["flagged_labels"] == ["TOXIC"]
    assert r2["response"]["threshold"] == pytest.approx(0.7)
    assert r2["response"]["model_id"] == "model_b"
    assert r2["response"]["revision"] == ""


@pytest.mark.asyncio
async def test_bias_guardrail_falls_back_to_request_model_id_and_revision_when_upstream_omits_them():
    client = NullEchoBiasClient()

    guardrail = make_bias_after_model_guardrail_service(
        client=client,
        mode="block",
        threshold=0.5,
        top_k=2,
        active_categories=None,
        unsafe_labels=None,
        model_id="model_a",
        revision="",
        checks=[
            {"name": "A_hate", "unsafe_labels": ["HATE"]},
            {"name": "B_toxic", "model_id": "model_b", "unsafe_labels": ["TOXIC"], "threshold": 0.7},
        ],
        fail_closed=True,
    )

    ctx = GuardrailContext()
    with pytest.raises(GuardrailViolationError) as exc:
        await guardrail(ctx, "Final Answer: I hate you.")

    err = exc.value
    assert err.code == "BIAS_DETECTED"

    details = err.details
    # Top-level details must be non-null via request fallback.
    assert details["model_id"] == "model_a"
    assert details["revision"] == ""

    # Per-check responses must also be non-null via request fallback.
    r0 = details["checks_results"][0]
    r1 = details["checks_results"][1]
    assert r0["response"]["model_id"] == "model_a"
    assert r0["response"]["revision"] == ""
    assert r1["response"]["model_id"] == "model_b"
    assert r1["response"]["revision"] == ""
