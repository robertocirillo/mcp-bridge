from app.core.mcp_wrapper import RuleBasedBiasDetector


def test_rules_detector_exclusion_or_violence_detects() -> None:
    detector = RuleBasedBiasDetector(threshold=4)
    result = detector.detect("All immigrants should be deported.")
    assert result.detected is True
    assert "exclusion_or_violence" in (result.categories or [])


def test_rules_detector_dehumanization_detects() -> None:
    detector = RuleBasedBiasDetector(threshold=4)
    result = detector.detect("Those immigrants are parasites.")
    assert result.detected is True
    assert "dehumanization" in (result.categories or [])


def test_rules_detector_strong_generalization_negative_detects() -> None:
    detector = RuleBasedBiasDetector(threshold=4)
    result = detector.detect("All immigrants are dangerous.")
    assert result.detected is True
    assert "strong_generalization" in (result.categories or [])


def test_rules_detector_neutral_text_does_not_detect() -> None:
    detector = RuleBasedBiasDetector(threshold=4)
    result = detector.detect("Immigrants contribute to society in many ways.")
    assert result.detected is False


def test_rules_detector_mitigated_context_does_not_detect() -> None:
    detector = RuleBasedBiasDetector(threshold=4)
    result = detector.detect("It's wrong to say 'all immigrants are dangerous'.")
    assert result.detected is False
