from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.core.clients.bias_detector_client import BiasDetectorClient, BiasDetectorError
from app.core.runtime.errors import GuardrailViolationError
from app.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class BiasDetectionResult:
    detected: bool
    categories: List[str] | None = None
    findings: List[str] | None = None


class BiasDetector:
    """Minimal, pluggable bias detector interface."""

    def detect(self, text: str) -> BiasDetectionResult:  # pragma: no cover
        # Force concrete detectors to implement the bias classification contract.
        raise NotImplementedError


class NoOpBiasDetector(BiasDetector):
    """Default detector: never detects bias."""

    def detect(self, text: str) -> BiasDetectionResult:
        # Always return a negative result when bias detection is intentionally disabled.
        return BiasDetectionResult(detected=False)


class RuleBasedBiasDetector(BiasDetector):
    """Deterministic, dependency-free bias detector."""

    _GROUP_TERMS = [
        "immigrati",
        "migranti",
        "stranieri",
        "donne",
        "uomini",
        "ragazze",
        "ragazzi",
        "musulmani",
        "ebrei",
        "cristiani",
        "neri",
        "bianchi",
        "gay",
        "lesbiche",
        "trans",
        "disabili",
        "immigrants",
        "migrants",
        "foreigners",
        "women",
        "men",
        "girls",
        "boys",
        "muslims",
        "jews",
        "christians",
        "black people",
        "white people",
        "gay people",
        "lesbians",
        "trans people",
        "disabled people",
    ]

    _NEGATIVE_DESCRIPTORS = [
        "inferiori",
        "stupidi",
        "pericolosi",
        "sporchi",
        "malvagi",
        "cattivi",
        "incapaci",
        "pigri",
        "ignoranti",
        "violenti",
        "inferior",
        "stupid",
        "dangerous",
        "dirty",
        "evil",
        "bad",
        "incapable",
        "lazy",
        "ignorant",
        "violent",
    ]

    _DEHUMANIZATION_TERMS = [
        "parassiti",
        "vermi",
        "scarafaggi",
        "spazzatura",
        "parasites",
        "worms",
        "cockroaches",
        "trash",
        "vermin",
    ]

    _EXCLUSION_VIOLENCE_TERMS = [
        "uccidere",
        "eliminare",
        "sterminare",
        "deportare",
        "espellere",
        "bandire",
        "cacciare",
        "mandare via",
        "togliere i diritti",
        "vietare",
        "non dovrebbero esistere",
        "kill",
        "eliminate",
        "exterminate",
        "deport",
        "expel",
        "ban",
        "kick out",
        "remove rights",
        "shouldn't exist",
        "should not exist",
    ]

    _CONTEXT_MITIGATIONS = [
        "e sbagliato dire",
        "e' sbagliato dire",
        "non e vero che",
        "non e' vero che",
        "e offensivo dire",
        "e' offensivo dire",
        "it's wrong to say",
        "it is wrong to say",
        "it's offensive to say",
        "it is offensive to say",
    ]

    def __init__(self, *, threshold: int = 4) -> None:
        # Store the minimum score required before the text is considered biased.
        self.threshold = int(threshold)

        # Precompile the group matcher once so repeated detections stay inexpensive.
        group_alt = "|".join(sorted((re.escape(g) for g in self._GROUP_TERMS), key=len, reverse=True))
        self._group_re = re.compile(rf"\b(?:{group_alt})\b", re.IGNORECASE)

        # Precompile the negative descriptor matcher used for generalization checks.
        neg_alt = "|".join(sorted((re.escape(w) for w in self._NEGATIVE_DESCRIPTORS), key=len, reverse=True))
        self._neg_re = re.compile(rf"\b(?:{neg_alt})\b", re.IGNORECASE)

        # Precompile dehumanizing language patterns so they can be reused across inputs.
        deh_alt = "|".join(sorted((re.escape(w) for w in self._DEHUMANIZATION_TERMS), key=len, reverse=True))
        self._deh_re = re.compile(rf"\b(?:{deh_alt})\b", re.IGNORECASE)

        # Precompile exclusion and violence expressions that should trigger a strong signal.
        excl_alt = "|".join(sorted((re.escape(w) for w in self._EXCLUSION_VIOLENCE_TERMS), key=len, reverse=True))
        self._excl_re = re.compile(rf"(?:{excl_alt})", re.IGNORECASE)

        # Match blanket statements that combine group references with broad negative claims.
        self._generalization_re = re.compile(
            rf"\b(?:tutti|tutte|all|every)\b[^.\n\r]{{0,80}}?\b(?:{group_alt})\b[^.\n\r]{{0,80}}?\b(?:sono|are|sempre|always)\b",
            re.IGNORECASE,
        )

    @staticmethod
    def _normalize(text: str) -> str:
        # Collapse whitespace and line breaks so rule matching works on a stable text shape.
        stripped = (text or "").replace("\n", " ").split()
        return " ".join(stripped)

    @staticmethod
    def _strip_accents(text: str) -> str:
        # Normalize accented characters to improve matching for Italian text variants.
        table = str.maketrans({
            "à": "a",
            "á": "a",
            "è": "e",
            "é": "e",
            "ì": "i",
            "í": "i",
            "ò": "o",
            "ó": "o",
            "ù": "u",
            "ú": "u",
        })
        return (text or "").lower().translate(table)

    @staticmethod
    def _split_sentences(text: str) -> List[str]:
        # Split the text into simple sentence-like chunks for localized rule checks.
        parts = re.split(r"[.!?]+", text)
        return [p.strip() for p in parts if p.strip()]

    def detect(self, text: str) -> BiasDetectionResult:
        # Normalize the input before applying any rule-based heuristics.
        normalized_text = self._normalize(text)
        if not normalized_text:
            return BiasDetectionResult(detected=False)

        # Accumulate a score and evidence so multiple weak signals can combine into a block.
        score = 0
        categories: set[str] = set()
        findings: List[str] = []

        # Check whether the text is likely quoting or criticizing biased content instead of endorsing it.
        lower = self._strip_accents(normalized_text)
        mitigated_context = any(marker in lower for marker in self._CONTEXT_MITIGATIONS)

        # Look for explicit exclusion or violence directed at a protected or social group.
        for sentence in self._split_sentences(normalized_text):
            if self._group_re.search(sentence) and self._excl_re.search(sentence):
                score += 6
                categories.add("exclusion_or_violence")
                findings.append("rule:exclusion_or_violence")
                break

        # Flag dehumanizing language when it appears alongside a recognized group reference.
        if self._group_re.search(normalized_text) and self._deh_re.search(normalized_text):
            score += 6
            categories.add("dehumanization")
            findings.append("rule:dehumanization")

        # Flag strong negative generalizations that apply harmful traits to an entire group.
        if self._generalization_re.search(normalized_text) and self._neg_re.search(normalized_text):
            score += 4
            categories.add("strong_generalization")
            findings.append("rule:strong_generalization_negative")

        # Reduce the score when the surrounding wording indicates condemnation of biased speech.
        if mitigated_context:
            score = max(0, score - 6)
            if score == 0:
                categories.clear()
                findings.clear()

        # Return structured evidence only when the score crosses the configured threshold.
        detected = score >= self.threshold
        return BiasDetectionResult(
            detected=detected,
            categories=sorted(categories) if detected else [],
            findings=findings if detected else [],
        )


_bias_detector: BiasDetector = NoOpBiasDetector()


def set_bias_detector(detector: BiasDetector) -> None:
    global _bias_detector
    _bias_detector = detector


def get_bias_detector() -> BiasDetector:
    return _bias_detector


def initialize_bias_detector_from_env() -> str:
    detector = (os.getenv("MCP_BRIDGE_BIAS_DETECTOR", "noop") or "noop").strip().lower()
    if detector != "rules":
        set_bias_detector(NoOpBiasDetector())
        return "noop"

    try:
        threshold = int(os.getenv("MCP_BRIDGE_BIAS_RULES_THRESHOLD", "4"))
    except Exception:
        threshold = 4

    set_bias_detector(RuleBasedBiasDetector(threshold=threshold))
    return "rules"


def _extract_final_answer(text: str) -> str:
    if not text:
        return text

    final_patterns = [
        r"(?is)\bfinal\s+answer\s*:\s*(.+)\s*$",
        r"(?is)\bfinal\s+answer\s*-\s*(.+)\s*$",
        r"(?is)\bfinal\s*:\s*(.+)\s*$",
    ]
    for pattern in final_patterns:
        matches = list(re.finditer(pattern, text))
        if matches:
            return matches[-1].group(1).strip()

    return text


def _extract_user_visible_answer(text: str) -> str:
    if text is None:
        return text

    raw = str(text)
    if not raw.strip():
        return raw

    final = _extract_final_answer(raw)
    if final != raw:
        return final

    looks_like_trace = ("Thought:" in raw) and ("Action:" in raw or "Observation:" in raw)
    if not looks_like_trace:
        return raw.strip()

    trace_prefixes = (
        "Thought:",
        "Action:",
        "Action Input:",
        "Observation:",
    )
    candidates = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(trace_prefixes):
            continue
        candidates.append(stripped)

    return (candidates[-1] if candidates else raw).strip()


def make_bias_after_model_guardrail(*, mode: str = "off"):
    normalized_mode = (mode or "off").strip().lower()
    if normalized_mode not in {"off", "block"}:
        normalized_mode = "off"

    async def _guardrail(ctx: Any, output: Any) -> Any:
        if normalized_mode == "off" or output is None:
            return output

        text = _extract_final_answer(str(output))
        result = get_bias_detector().detect(text)
        if not bool(getattr(result, "detected", False)):
            return output

        categories = getattr(result, "categories", None)
        findings = getattr(result, "findings", None)

        logger.info(
            "Guardrail triggered: bias detected in model output",
            extra={
                "guardrail": "bias",
                "phase": "after_model",
                "tenant_id": getattr(ctx, "tenant_id", None),
                "run_id": getattr(ctx, "run_id", None),
                "session_id": getattr(ctx, "session_id", None),
                "categories": categories or [],
            },
        )

        raise GuardrailViolationError(
            code="BIAS_DETECTED",
            message="Bias detected in model output",
            phase="after_model",
            rule="bias",
            tenant_id=getattr(ctx, "tenant_id", None),
            run_id=getattr(ctx, "run_id", None),
            session_id=getattr(ctx, "session_id", None),
            details={
                "categories": categories or [],
                "findings": findings or [],
                "mode": "block",
            },
        )

    return _guardrail


def make_bias_after_model_guardrail_service(
    *,
    client: BiasDetectorClient,
    mode: str = "off",
    threshold: float = 0.5,
    top_k: int = 5,
    active_categories: Optional[List[str]] = None,
    unsafe_labels: Optional[List[str]] = None,
    model_id: Optional[str] = None,
    revision: Optional[str] = None,
    return_all_scores: bool = False,
    return_char_spans: bool = False,
    checks: Optional[List[Any]] = None,
    fail_closed: bool = True,
):
    normalized_mode = (mode or "off").strip().lower()
    if normalized_mode not in {"off", "block"}:
        normalized_mode = "off"

    def _fields_set(obj: Any) -> set[str]:
        if obj is None:
            return set()
        model_fields_set = getattr(obj, "model_fields_set", None)
        if isinstance(model_fields_set, set):
            return set(model_fields_set)
        if isinstance(obj, dict):
            return set(obj.keys())
        return set()

    def _get(obj: Any, key: str) -> Any:
        if obj is None:
            return None
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    def _resolve_check(idx: int, check: Any) -> Dict[str, Any]:
        fields_set = _fields_set(check)
        return {
            "name": _get(check, "name") or f"check_{idx + 1}",
            "threshold": _get(check, "threshold") if "threshold" in fields_set else threshold,
            "top_k": _get(check, "top_k") if "top_k" in fields_set else top_k,
            "active_categories": _get(check, "active_categories") if "active_categories" in fields_set else active_categories,
            "unsafe_labels": _get(check, "unsafe_labels") if "unsafe_labels" in fields_set else unsafe_labels,
            "model_id": _get(check, "model_id") if "model_id" in fields_set else model_id,
            "revision": _get(check, "revision") if "revision" in fields_set else revision,
        }

    def _build_flagged_label_scores(resp: Dict[str, Any], effective_threshold: Any) -> List[Dict[str, Any]]:
        flagged_labels = resp.get("flagged_labels", []) or []
        flagged_label_scores: List[Dict[str, Any]] = []
        labels = resp.get("labels") or []
        if not isinstance(labels, list):
            return flagged_label_scores

        for item in labels:
            if not isinstance(item, dict):
                continue
            label = item.get("label")
            score = item.get("score")
            is_flagged = item.get("is_flagged")
            if label not in flagged_labels or not isinstance(score, (int, float)):
                continue
            score_float = float(score)
            threshold_float = float(effective_threshold) if effective_threshold is not None else None
            flagged_label_scores.append(
                {
                    "label": label,
                    "score": score_float,
                    "score_pct": round(score_float * 100.0, 2),
                    "threshold": threshold_float,
                    "margin": round(score_float - threshold_float, 6) if threshold_float is not None else None,
                    "is_flagged": bool(is_flagged) if is_flagged is not None else None,
                }
            )
        return flagged_label_scores

    def _build_request_dict(
        *,
        model_id: Any,
        revision: Any,
        threshold: Any,
        top_k: Any,
        active_categories: Any,
        unsafe_labels: Any,
        return_all_scores: bool,
        return_char_spans: bool,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model_id": model_id,
            "revision": revision,
            "threshold": threshold,
            "top_k": top_k,
            "active_categories": active_categories,
            "unsafe_labels": unsafe_labels,
        }
        if return_all_scores:
            payload["return_all_scores"] = True
        if return_char_spans:
            payload["return_char_spans"] = True
        return payload

    async def _guardrail(ctx: Any, output: Any) -> Any:
        if normalized_mode == "off" or output is None:
            return output

        text = _extract_final_answer(str(output))
        if checks is None:
            checks_to_run = [None]
        elif isinstance(checks, list) and len(checks) == 0:
            return output
        else:
            checks_to_run = list(checks) if isinstance(checks, list) else [checks]

        results: List[Dict[str, Any]] = []
        first_flagged_result: Optional[Dict[str, Any]] = None

        for idx, check in enumerate(checks_to_run):
            resolved = _resolve_check(idx, check)
            if return_all_scores:
                resolved["return_all_scores"] = True
            if return_char_spans:
                resolved["return_char_spans"] = True

            try:
                classify_kwargs = {
                    "text": text,
                    "model_id": resolved.get("model_id"),
                    "revision": resolved.get("revision"),
                    "active_categories": resolved.get("active_categories"),
                    "unsafe_labels": resolved.get("unsafe_labels"),
                    "top_k": resolved.get("top_k"),
                    "threshold": resolved.get("threshold"),
                }
                if return_all_scores:
                    classify_kwargs["return_all_scores"] = True
                if return_char_spans:
                    classify_kwargs["return_char_spans"] = True
                resp = await client.classify(**classify_kwargs)
            except BiasDetectorError as exc:
                body = getattr(exc, "body", None)
                detail = body.get("detail") if isinstance(body, dict) else None
                code = detail.get("code") if isinstance(detail, dict) else None
                if exc.status_code == 400 and code == "INVALID_REQUEST":
                    raise GuardrailViolationError(
                        code="BIAS_DETECTOR_INVALID_REQUEST",
                        message="Bias detector rejected the request",
                        phase="after_model",
                        rule="bias",
                        tenant_id=getattr(ctx, "tenant_id", None),
                        run_id=getattr(ctx, "run_id", None),
                        session_id=getattr(ctx, "session_id", None),
                        http_status=400,
                        details={
                            "check": {
                                "index": idx,
                                "name": resolved.get("name"),
                                "request": resolved,
                            },
                            "upstream": body,
                        },
                    )
                if fail_closed:
                    raise GuardrailViolationError(
                        code="BIAS_DETECTOR_UNAVAILABLE",
                        message="Bias detector service unavailable",
                        phase="after_model",
                        rule="bias",
                        tenant_id=getattr(ctx, "tenant_id", None),
                        run_id=getattr(ctx, "run_id", None),
                        session_id=getattr(ctx, "session_id", None),
                        http_status=503,
                        details={
                            "check": {
                                "index": idx,
                                "name": resolved.get("name"),
                                "request": resolved,
                            },
                            "upstream_status": getattr(exc, "status_code", None),
                            "upstream": getattr(exc, "body", None),
                        },
                    )
                return output
            except Exception as exc:
                if fail_closed:
                    raise GuardrailViolationError(
                        code="BIAS_DETECTOR_UNAVAILABLE",
                        message="Bias detector service unavailable",
                        phase="after_model",
                        rule="bias",
                        tenant_id=getattr(ctx, "tenant_id", None),
                        run_id=getattr(ctx, "run_id", None),
                        session_id=getattr(ctx, "session_id", None),
                        http_status=503,
                        details={
                            "check": {
                                "index": idx,
                                "name": resolved.get("name"),
                                "request": resolved,
                            },
                            "error": type(exc).__name__,
                        },
                    )
                return output

            flagged = bool(resp.get("flagged", False))
            flagged_labels = resp.get("flagged_labels", []) or []
            meta = resp.get("meta")
            effective_threshold = meta.get("threshold") if isinstance(meta, dict) else None
            if effective_threshold is None:
                effective_threshold = resolved.get("threshold")

            response_model_id = resp.get("model_id")
            if response_model_id is None:
                response_model_id = resolved.get("model_id")

            response_revision = resp.get("revision")
            if response_revision is None:
                response_revision = resolved.get("revision")

            result = {
                "name": resolved.get("name"),
                "request": _build_request_dict(
                    model_id=resolved.get("model_id"),
                    revision=resolved.get("revision"),
                    threshold=resolved.get("threshold"),
                    top_k=resolved.get("top_k"),
                    active_categories=resolved.get("active_categories"),
                    unsafe_labels=resolved.get("unsafe_labels"),
                    return_all_scores=bool(return_all_scores),
                    return_char_spans=bool(return_char_spans),
                ),
                "response": {
                    "model_id": response_model_id,
                    "revision": response_revision,
                    "flagged": flagged,
                    "flagged_labels": flagged_labels,
                    "flagged_label_scores": _build_flagged_label_scores(resp, effective_threshold),
                    "threshold": effective_threshold,
                    "top_k": resolved.get("top_k"),
                    "labels": resp.get("labels"),
                    "meta": resp.get("meta"),
                },
            }
            results.append(result)
            if flagged and first_flagged_result is None:
                first_flagged_result = result

        if first_flagged_result is None:
            return output

        first_response = first_flagged_result.get("response") or {}
        first_request = first_flagged_result.get("request") or {}
        flagged_labels = first_response.get("flagged_labels", []) or []

        first_model_id = first_response.get("model_id")
        if first_model_id is None:
            first_model_id = first_request.get("model_id")

        first_revision = first_response.get("revision")
        if first_revision is None:
            first_revision = first_request.get("revision")

        logger.info(
            "Guardrail triggered: bias detected via bias-detector-service",
            extra={
                "guardrail": "bias",
                "phase": "after_model",
                "tenant_id": getattr(ctx, "tenant_id", None),
                "run_id": getattr(ctx, "run_id", None),
                "session_id": getattr(ctx, "session_id", None),
                "flagged_labels": flagged_labels,
                "checks_executed": len(results),
            },
        )

        raise GuardrailViolationError(
            code="BIAS_DETECTED",
            message="Bias detected in model output",
            phase="after_model",
            rule="bias",
            tenant_id=getattr(ctx, "tenant_id", None),
            run_id=getattr(ctx, "run_id", None),
            session_id=getattr(ctx, "session_id", None),
            http_status=403,
            details={
                "categories": first_request.get("active_categories") or [],
                "findings": [f"label:{label}" for label in flagged_labels],
                "mode": "block",
                "model_id": first_model_id,
                "revision": first_revision,
                "flagged_labels": flagged_labels,
                "flagged_label_scores": first_response.get("flagged_label_scores") or [],
                "threshold": first_response.get("threshold"),
                "top_k": first_response.get("top_k"),
                "checks_results": results,
            },
        )

    return _guardrail
