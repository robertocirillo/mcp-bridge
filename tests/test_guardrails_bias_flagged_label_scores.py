import pytest

from app.core.mcp_wrapper import (
    GuardrailContext,
    GuardrailViolationError,
    make_bias_after_model_guardrail_service,
)


class FakeBiasClient:
    async def classify(
        self,
        *,
        text: str,
        model_id=None,
        revision=None,
        active_categories=None,
        unsafe_labels=None,
        top_k=5,
        threshold=0.5,
    ):
        return {
            "model_id": "cardiffnlp/twitter-roberta-base-hate-latest",
            "revision": "",
            "flagged": True,
            "flagged_labels": ["HATE"],
            "labels": [
                {"label": "HATE", "score": 0.9967, "is_flagged": True},
                {"label": "NOT-HATE", "score": 0.0033, "is_flagged": False},
            ],
            "meta": {"threshold": threshold},
        }


@pytest.mark.asyncio
async def test_bias_detected_includes_flagged_label_scores():
    guardrail = make_bias_after_model_guardrail_service(
        client=FakeBiasClient(),
        mode="block",
        threshold=0.8,
        top_k=5,
        active_categories=None,
        unsafe_labels=["HATE"],
        model_id="cardiffnlp/twitter-roberta-base-hate-latest",
        revision="",
        fail_closed=True,
    )

    ctx = GuardrailContext()
    with pytest.raises(GuardrailViolationError) as exc:
        await guardrail(ctx, "Final Answer: I hate you all and you should disappear.")

    err = exc.value
    assert err.code == "BIAS_DETECTED"
    assert err.http_status == 403

    details = err.details
    assert details["flagged_labels"] == ["HATE"]
    assert "flagged_label_scores" in details
    assert details["flagged_label_scores"][0]["label"] == "HATE"
    assert details["flagged_label_scores"][0]["score"] == pytest.approx(0.9967)
    assert details["flagged_label_scores"][0]["score_pct"] == pytest.approx(99.67)
    assert details["flagged_label_scores"][0]["threshold"] == pytest.approx(0.8)
    assert details["flagged_label_scores"][0]["margin"] == pytest.approx(0.1967, abs=1e-4)
