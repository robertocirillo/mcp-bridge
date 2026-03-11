"""
Refined wrapper for mcp-use with enhanced error handling
"""

import os
import re
from typing import Optional, Dict, Any, List, Callable, Awaitable, Union
from dataclasses import dataclass
from fnmatch import fnmatchcase

from app.core.exceptions import MCPWrapperError, DependencyError, ConfigurationError
from app.utils.logging import get_logger
from app.utils.helpers import retry_async
from app.models.config import SandboxOptions as SandboxOptionsModel  # rinomina per non confonderla con quella di mcp-use
from app.core.bias_detector_client import BiasDetectorClient, BiasDetectorError
from app.core.guardrail_runner import GuardrailExecutionContext, GuardrailRunner
from app.core.mcp_policy_engine import ToolPolicy, ToolInvocationContext, ToolInvocationDecision, ToolPolicyEngine
from app.core.mcp_audit import AuditEvent, InMemoryAuditRecorder, utc_now_iso

logger = get_logger(__name__)

GuardrailContext = GuardrailExecutionContext


# -----------------------------
# Local MVP guardrails
# -----------------------------

# NOTE: Deterministic, dependency-free patterns.
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)

# Phone: generic international-ish pattern with a minimum amount of digits.
_PHONE_RE = re.compile(r"\b(?:\+?\d[\d\s().-]{6,}\d)\b")

# IBAN: 15..34 chars, starts with 2 letters + 2 digits.
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b", re.IGNORECASE)

@dataclass(frozen=True)
class BiasDetectionResult:
    """Result returned by a BiasDetector.

    MVP0 is intentionally small and deterministic.
    """

    detected: bool
    categories: List[str] | None = None
    findings: List[str] | None = None


class BiasDetector:
    """Minimal, pluggable bias detector interface."""

    def detect(self, text: str) -> BiasDetectionResult:  # pragma: no cover
        raise NotImplementedError


class NoOpBiasDetector(BiasDetector):
    """Default detector: never detects bias (fail-open, deterministic)."""

    def detect(self, text: str) -> BiasDetectionResult:
        return BiasDetectionResult(detected=False)




class RuleBasedBiasDetector(BiasDetector):
    """Deterministic, dependency-free bias detector (MVP1).

    Goal: catch *explicit* discriminatory / dehumanizing / exclusionary language
    with conservative heuristics. This is intentionally NOT a subtle bias detector.

    Detection strategy:
    - Strong generalizations about broad human groups with negative descriptors
    - Dehumanization terms when a group is mentioned
    - Exclusion/violence verbs in the same sentence as a group

    Notes:
    - No external services / models; deterministic.
    - Avoids listing slurs.
    """

    # Broad groups in Italian/English. Keep this list short and conservative.
    _GROUP_TERMS = [
        # Italian
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
        # English
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

    # Negative descriptors (generic; intentionally avoids slurs).
    _NEGATIVE_DESCRIPTORS = [
        # Italian
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
        # English
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

    # Dehumanization terms (generic; intentionally avoids slurs).
    _DEHUMANIZATION_TERMS = [
        # Italian / English
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

    # Exclusion / violence verbs (generic; deterministic).
    _EXCLUSION_VIOLENCE_TERMS = [
        # Italian
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
        # English
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

    # Context mitigations: if text is explicitly condemning a discriminatory statement,
    # we reduce the score (fail-open for educational/critical context).
    _CONTEXT_MITIGATIONS = [
        # Italian
        "è sbagliato dire",
        "e' sbagliato dire",
        "non è vero che",
        "non e' vero che",
        "è offensivo dire",
        "e' offensivo dire",
        # English
        "it's wrong to say",
        "it is wrong to say",
        "it's offensive to say",
        "it is offensive to say",
    ]

    def __init__(self, *, threshold: int = 4):
        self.threshold = int(threshold)

        group_alt = "|".join(sorted((re.escape(g) for g in self._GROUP_TERMS), key=len, reverse=True))
        self._group_re = re.compile(rf"\b(?:{group_alt})\b", re.IGNORECASE)

        neg_alt = "|".join(sorted((re.escape(w) for w in self._NEGATIVE_DESCRIPTORS), key=len, reverse=True))
        self._neg_re = re.compile(rf"\b(?:{neg_alt})\b", re.IGNORECASE)

        deh_alt = "|".join(sorted((re.escape(w) for w in self._DEHUMANIZATION_TERMS), key=len, reverse=True))
        self._deh_re = re.compile(rf"\b(?:{deh_alt})\b", re.IGNORECASE)

        excl_alt = "|".join(sorted((re.escape(w) for w in self._EXCLUSION_VIOLENCE_TERMS), key=len, reverse=True))
        self._excl_re = re.compile(rf"(?:{excl_alt})", re.IGNORECASE)

        # Strong generalizations. Conservative: requires explicit quantifier + group + copula.
        self._generalization_re = re.compile(
            rf"\b(?:tutti|tutte|all|every)\b[^.\n\r]{0,80}?\b(?:{group_alt})\b[^.\n\r]{0,80}?\b(?:sono|are|sempre|always)\b",
            re.IGNORECASE,
        )

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join((text or "").replace("\n", " ").split())

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        parts = re.split(r"[.!?]+", text)
        return [p.strip() for p in parts if p.strip()]

    def detect(self, text: str) -> BiasDetectionResult:
        t = self._normalize(text)
        if not t:
            return BiasDetectionResult(detected=False)

        score = 0
        categories: set[str] = set()
        findings: list[str] = []

        lower = t.lower()
        mitigated_context = any(m in lower for m in self._CONTEXT_MITIGATIONS)

        # 1) Exclusion/violence in same sentence as a group mention.
        for s in self._split_sentences(t):
            if self._group_re.search(s) and self._excl_re.search(s):
                score += 6
                categories.add("exclusion_or_violence")
                findings.append("rule:exclusion_or_violence")
                break

        # 2) Dehumanization term + group mention.
        if self._group_re.search(t) and self._deh_re.search(t):
            score += 6
            categories.add("dehumanization")
            findings.append("rule:dehumanization")

        # 3) Strong generalization + negative descriptor.
        if self._generalization_re.search(t) and self._neg_re.search(t):
            score += 4
            categories.add("strong_generalization")
            findings.append("rule:strong_generalization_negative")

        # Mitigation for critical/educational context.
        if mitigated_context:
            score = max(0, score - 6)
            if score == 0:
                categories.clear()
                findings.clear()

        detected = score >= self.threshold
        return BiasDetectionResult(
            detected=detected,
            categories=sorted(categories) if detected else [],
            findings=findings if detected else [],
        )
# Module-level detector instance so tests can monkeypatch it deterministically.
_bias_detector: BiasDetector = NoOpBiasDetector()


def set_bias_detector(detector: BiasDetector) -> None:
    """Override the active bias detector (useful for tests)."""

    global _bias_detector
    _bias_detector = detector


def get_bias_detector() -> BiasDetector:
    return _bias_detector


def initialize_bias_detector_from_env() -> str:
    """Initialize the global bias detector from environment variables.

    This keeps the default behavior (NoOp) unless explicitly enabled.

    Env vars:
      - MCP_BRIDGE_BIAS_DETECTOR: 'noop' (default) | 'rules'
      - MCP_BRIDGE_BIAS_RULES_THRESHOLD: int (default 4)

    Returns the detector name actually configured.
    """

    detector = (os.getenv('MCP_BRIDGE_BIAS_DETECTOR', 'noop') or 'noop').strip().lower()
    if detector != 'rules':
        set_bias_detector(NoOpBiasDetector())
        return 'noop'

    try:
        threshold = int(os.getenv('MCP_BRIDGE_BIAS_RULES_THRESHOLD', '4'))
    except Exception:
        threshold = 4

    set_bias_detector(RuleBasedBiasDetector(threshold=threshold))
    return 'rules'


def make_bias_after_model_guardrail(*, mode: str = "off"):
    """Factory for an after_model guardrail that blocks biased output.

    MVP0 modes:
      - off: no-op
      - block: raise GuardrailViolationError(code=BIAS_DETECTED)
    """

    normalized_mode = (mode or "off").strip().lower()
    if normalized_mode not in {"off", "block"}:
        normalized_mode = "off"

    async def _guardrail(ctx: "GuardrailContext", output: Any) -> Any:
        if normalized_mode == "off":
            return output

        if output is None:
            return output

        text = _extract_final_answer(str(output))
        result = get_bias_detector().detect(text)
        detected = bool(getattr(result, "detected", False))
        if not detected:
            return output

        categories = getattr(result, "categories", None)
        findings = getattr(result, "findings", None)

        # Observability: log the trigger (do not log the full output text).
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


def _extract_final_answer(text: str) -> str:
    """Best-effort extraction of the final answer from agent outputs.

    Many agent frameworks prefix the final answer with markers like:
    - "Final Answer:" (common)
    - "Final:" / "Final Answer -" / etc.

    For now we keep this heuristic conservative:
    - if a known marker is found, we take the substring after the *last* marker
    - otherwise we return the full text.
    """

    if not text:
        return text

    markers = ["Final Answer:", "Final Answer -", "Final:"]
    last_idx = -1
    last_marker = None
    for m in markers:
        idx = text.rfind(m)
        if idx > last_idx:
            last_idx = idx
            last_marker = m

    if last_idx == -1 or last_marker is None:
        return text

    return text[last_idx + len(last_marker) :].strip() or ""


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
    """Factory for an after_model guardrail backed by bias-detector-service.

    Behavior:
      - off: no-op
      - block: call the service and block with BIAS_DETECTED if flagged

    Cascaded checks:
      - If `checks` is omitted (None), mcp-bridge performs a single classification call
        using the session-level defaults.
      - If `checks` is provided, mcp-bridge performs one call per check, merging
        the session-level defaults with per-check overrides.
      - mcp-bridge remains "dumb": it blocks only when upstream responds with flagged=True.
        No local interpretation of labels is performed.

    Fail-closed:
      - If enabled and the service call errors/timeouts, we block by raising
        GuardrailViolationError(code=BIAS_DETECTOR_UNAVAILABLE).
    """

    normalized_mode = (mode or "off").strip().lower()
    if normalized_mode not in {"off", "block"}:
        normalized_mode = "off"

    def _fields_set(obj: Any) -> set[str]:
        if obj is None:
            return set()
        fs = getattr(obj, "model_fields_set", None)
        if isinstance(fs, set):
            return set(fs)
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
        """Merge session-level defaults with per-check overrides.

        A field is considered an override when it is *present* in the check payload,
        even if its value is null.
        """

        fs = _fields_set(check)
        resolved = {
            "name": _get(check, "name") or f"check_{idx + 1}",
            "threshold": _get(check, "threshold") if "threshold" in fs else threshold,
            "top_k": _get(check, "top_k") if "top_k" in fs else top_k,
            "active_categories": _get(check, "active_categories") if "active_categories" in fs else active_categories,
            "unsafe_labels": _get(check, "unsafe_labels") if "unsafe_labels" in fs else unsafe_labels,
            "model_id": _get(check, "model_id") if "model_id" in fs else model_id,
            "revision": _get(check, "revision") if "revision" in fs else revision,
        }
        return resolved

    def _build_flagged_label_scores(resp: Dict[str, Any], effective_threshold: Any) -> List[Dict[str, Any]]:
        flagged_labels = resp.get("flagged_labels", []) or []
        flagged_label_scores: List[Dict[str, Any]] = []
        labels = resp.get("labels") or []
        if isinstance(labels, list):
            for item in labels:
                if not isinstance(item, dict):
                    continue
                lbl = item.get("label")
                score = item.get("score")
                is_flagged = item.get("is_flagged")
                if lbl in flagged_labels and isinstance(score, (int, float)):
                    score_f = float(score)
                    thr = float(effective_threshold) if effective_threshold is not None else None
                    flagged_label_scores.append(
                        {
                            "label": lbl,
                            "score": score_f,
                            "score_pct": round(score_f * 100.0, 2),
                            "threshold": thr,
                            "margin": round(score_f - thr, 6) if thr is not None else None,
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
        """Build a request dict for diagnostics / error payloads.

        Optional flags are only included when enabled to preserve backward
        compatibility with previous payloads and tests.
        """
        req: Dict[str, Any] = {
            "model_id": model_id,
            "revision": revision,
            "threshold": threshold,
            "top_k": top_k,
            "active_categories": active_categories,
            "unsafe_labels": unsafe_labels,
        }
        if return_all_scores:
            req["return_all_scores"] = True
        if return_char_spans:
            req["return_char_spans"] = True
        return req


    async def _guardrail(ctx: "GuardrailContext", output: Any) -> Any:
        if normalized_mode == "off":
            return output

        if output is None:
            return output

        text = _extract_final_answer(str(output))

        # Determine cascaded execution plan.
        if checks is None:
            checks_to_run = [None]
        else:
            # Explicit empty list => no checks.
            if isinstance(checks, list) and len(checks) == 0:
                return output
            checks_to_run = list(checks) if isinstance(checks, list) else [checks]

        results: List[Dict[str, Any]] = []
        first_flagged_result: Optional[Dict[str, Any]] = None

        for idx, chk in enumerate(checks_to_run):
            resolved = _resolve_check(idx, chk)
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
                # Only pass optional flags when enabled to preserve compatibility
                # with older / fake clients used in unit tests.
                if return_all_scores:
                    classify_kwargs["return_all_scores"] = True
                if return_char_spans:
                    classify_kwargs["return_char_spans"] = True

                resp = await client.classify(**classify_kwargs)
            except BiasDetectorError as e:
                # Map known INVALID_REQUEST to 400; everything else is treated as unavailable.
                body = getattr(e, "body", None)
                detail = body.get("detail") if isinstance(body, dict) else None
                code = detail.get("code") if isinstance(detail, dict) else None
                if e.status_code == 400 and code == "INVALID_REQUEST":
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
                            "upstream_status": getattr(e, "status_code", None),
                            "upstream": getattr(e, "body", None),
                        },
                    )
                return output
            except Exception as e:
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
                            "error": type(e).__name__,
                        },
                    )
                return output

            flagged = bool(resp.get("flagged", False))
            flagged_labels = resp.get("flagged_labels", []) or []

            # Determine effective threshold (prefer upstream meta if present).
            meta = resp.get("meta")
            effective_threshold = meta.get("threshold") if isinstance(meta, dict) else None
            if effective_threshold is None:
                effective_threshold = resolved.get("threshold")

            flagged_label_scores = _build_flagged_label_scores(resp, effective_threshold)

            # Prefer upstream echo fields, but fall back to the request values.
            # This makes the payload stable even if upstream omits model_id/revision.
            resp_model_id = resp.get("model_id")
            if resp_model_id is None:
                resp_model_id = resolved.get("model_id")
            resp_revision = resp.get("revision")
            if resp_revision is None:
                resp_revision = resolved.get("revision")

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
                    "model_id": resp_model_id,
                    "revision": resp_revision,
                    "flagged": flagged,
                    "flagged_labels": flagged_labels,
                    "flagged_label_scores": flagged_label_scores,
                    "threshold": effective_threshold,
                    "top_k": resolved.get("top_k"),
                    "labels": resp.get("labels"),
                    "meta": resp.get("meta"),
                },
            }
            results.append(result)

            if flagged and first_flagged_result is None:
                first_flagged_result = result

        # No check flagged => pass
        if first_flagged_result is None:
            return output

        # Backward-compatible top-level detail fields are taken from the first flagged check.
        first_resp = (first_flagged_result.get("response") or {}) if isinstance(first_flagged_result, dict) else {}
        first_req = (first_flagged_result.get("request") or {}) if isinstance(first_flagged_result, dict) else {}
        flagged_labels = first_resp.get("flagged_labels", []) or []

        # Prefer response (upstream echo), fall back to request.
        first_model_id = first_resp.get("model_id")
        if first_model_id is None:
            first_model_id = first_req.get("model_id")
        first_revision = first_resp.get("revision")
        if first_revision is None:
            first_revision = first_req.get("revision")

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
                "categories": (first_req.get("active_categories") or []),
                "findings": [f"label:{lbl}" for lbl in flagged_labels],
                "mode": "block",
                "model_id": first_model_id,
                "revision": first_revision,
                "flagged_labels": flagged_labels,
                "flagged_label_scores": first_resp.get("flagged_label_scores") or [],
                "threshold": first_resp.get("threshold"),
                "top_k": first_resp.get("top_k"),
                # Cascaded results
                "checks_results": results,
            },
        )

    return _guardrail




def _detect_pii(text: str) -> Dict[str, int]:
    """Return detected PII types with counts (email/phone/iban)."""
    return {
        "email": len(_EMAIL_RE.findall(text)),
        "phone": len(_PHONE_RE.findall(text)),
        "iban": len(_IBAN_RE.findall(text)),
    }


def redact_pii(text: str) -> str:
    """Redact email/phone/iban occurrences from text."""
    # Order matters to avoid redacting inside placeholders.
    text = _EMAIL_RE.sub("[MCP_BRIDGE_REDACTED_EMAIL]", text)
    text = _IBAN_RE.sub("[MCP_BRIDGE_REDACTED_IBAN]", text)
    text = _PHONE_RE.sub("[MCP_BRIDGE_REDACTED_PHONE]", text)
    return text


def _redact_pii_in_obj(value: Any) -> Any:
    """Recursively redact PII in tool results.

    MVP behavior:
    - Only redacts inside string values.
    - Preserves the original structure for lists/tuples/dicts.
    - Other types are returned as-is.
    """

    if value is None:
        return value
    if isinstance(value, str):
        return redact_pii(value)
    if isinstance(value, list):
        return [_redact_pii_in_obj(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_redact_pii_in_obj(v) for v in value)
    if isinstance(value, dict):
        return {k: _redact_pii_in_obj(v) for k, v in value.items()}
    return value


def _detect_pii_in_obj(value: Any) -> Dict[str, int]:
    """Recursively detect PII counts in nested objects.

    Used for tool-result blocking when pii_mode == 'block'.
    Mirrors `_redact_pii_in_obj` traversal but aggregates counts.

    NOTE: For determinism and stability, we only scan *string values*.
    """

    counts = {"email": 0, "phone": 0, "iban": 0}

    if value is None:
        return counts

    if isinstance(value, str):
        found = _detect_pii(value)
        for k in counts:
            counts[k] += int(found.get(k, 0) or 0)
        return counts

    if isinstance(value, list):
        for v in value:
            found = _detect_pii_in_obj(v)
            for k in counts:
                counts[k] += int(found.get(k, 0) or 0)
        return counts

    if isinstance(value, tuple):
        for v in value:
            found = _detect_pii_in_obj(v)
            for k in counts:
                counts[k] += int(found.get(k, 0) or 0)
        return counts

    if isinstance(value, dict):
        for v in value.values():
            found = _detect_pii_in_obj(v)
            for k in counts:
                counts[k] += int(found.get(k, 0) or 0)
        return counts

    return counts


def make_pii_after_model_guardrail(*, mode: str = "redact"):
    """Factory for an after_model guardrail that detects/redacts PII.

    Modes:
      - redact (default): return redacted output
      - block: raise GuardrailViolationError(code=PII_DETECTED)
    """

    normalized_mode = (mode or "redact").strip().lower()
    if normalized_mode not in {"off", "redact", "block"}:
        # Keep deterministic behavior; treat unknown modes as redact.
        normalized_mode = "redact"

    async def _guardrail(ctx: "GuardrailContext", output: Any) -> Any:
        # Only operate on text outputs for the MVP.
        if output is None:
            return output
        text = _extract_final_answer(str(output))
        counts = _detect_pii(text)
        present = [k for k, v in counts.items() if v > 0]

        if not present:
            return output

        if normalized_mode == "off":
            return output

        if normalized_mode == "block":
            raise GuardrailViolationError(
                code="PII_DETECTED",
                message="PII detected in model output",
                phase="after_model",
                rule="pii",
                tenant_id=getattr(ctx, "tenant_id", None),
                run_id=getattr(ctx, "run_id", None),
                session_id=getattr(ctx, "session_id", None),
                details={
                    "types": present,
                    "counts": counts,
                    "mode": "block",
                },
            )

        # Default: redact.
        return redact_pii(text)

    return _guardrail


def _extract_final_answer(text: str) -> str:
    """Best-effort extraction of a "final answer" from agent-style outputs.

    Many agent frameworks return a trace that includes a trailing "Final Answer:" block.
    For the bias guardrail we currently evaluate only the final answer portion.

    If no marker is found, returns the original text.
    """

    if not text:
        return text

    # Prefer the last occurrence to handle multi-step traces.
    markers = ["Final Answer:", "Final answer:", "FINAL ANSWER:"]
    for m in markers:
        idx = text.rfind(m)
        if idx != -1:
            return text[idx + len(m) :].strip()

    # Common alternative markers.
    markers2 = ["Final:", "FINAL:"]
    for m in markers2:
        idx = text.rfind(m)
        if idx != -1:
            return text[idx + len(m) :].strip()

    return text


def _extract_user_visible_answer(text: str) -> str:
    """Extract a user-visible answer from agent-style traces.

    mcp-use / LangChain agents may sometimes return ReAct-style traces including
    'Thought:', 'Action:', 'Observation:' and an optional 'Final Answer:' marker.

    This helper is intentionally conservative:
    - If a 'Final Answer:' marker is found (case-insensitive), return the trailing block.
    - Otherwise, only if the text *looks like* a ReAct trace, return the last non-trace line.
    - Else, return the original text (stripped).

    This is used to keep the API response stable and user-friendly, without exposing reasoning.
    """

    if text is None:
        return text
    t = str(text)
    if not t.strip():
        return t

    # Prefer explicit final answer markers (take the last occurrence).
    final_patterns = [
        r"(?is)\bfinal\s+answer\s*:\s*(.+)\s*$",
        r"(?is)\bfinal\s+answer\s*-\s*(.+)\s*$",
        r"(?is)\bfinal\s*:\s*(.+)\s*$",
    ]
    for pat in final_patterns:
        matches = list(re.finditer(pat, t))
        if matches:
            return matches[-1].group(1).strip()

    # Only attempt ReAct stripping if it resembles a trace.
    looks_like_trace = ("Thought:" in t) and ("Action:" in t or "Observation:" in t)
    if not looks_like_trace:
        return t.strip()

    trace_prefixes = (
        "Thought:",
        "Action:",
        "Action Input:",
        "Observation:",
    )

    candidates = []
    for raw in t.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(trace_prefixes):
            continue
        candidates.append(line)

    return (candidates[-1] if candidates else t).strip()


def make_pii_before_model_guardrail(*, mode: str = "block"):
    """Factory for a before_model guardrail that detects/redacts PII in user input.

    Modes:
      - off: no-op
      - redact: replace detected entities in ctx.query with placeholders
      - block (default): raise GuardrailViolationError(code=PII_DETECTED, phase=before_model)
    """

    normalized_mode = (mode or "block").strip().lower()
    if normalized_mode not in {"off", "redact", "block"}:
        normalized_mode = "block"

    def _guardrail(ctx: "GuardrailContext") -> "GuardrailContext":
        # No-op when disabled.
        if normalized_mode == "off":
            return ctx

        query = getattr(ctx, "query", None)
        if not query:
            return ctx

        text = str(query)
        counts = _detect_pii(text)
        present = [k for k, v in counts.items() if v > 0]
        if not present:
            return ctx

        if normalized_mode == "block":
            raise GuardrailViolationError(
                code="PII_DETECTED",
                message="PII detected in user input",
                phase="before_model",
                rule="pii",
                tenant_id=getattr(ctx, "tenant_id", None),
                run_id=getattr(ctx, "run_id", None),
                session_id=getattr(ctx, "session_id", None),
                details={
                    "types": present,
                    "counts": counts,
                    "mode": "block",
                },
            )

        # redact
        redacted = redact_pii(text)
        return GuardrailContext(
            tenant_id=getattr(ctx, "tenant_id", None),
            run_id=getattr(ctx, "run_id", None),
            session_id=getattr(ctx, "session_id", None),
            query=redacted,
            server_name=getattr(ctx, "server_name", None),
        )

    return _guardrail


class MCPToolNotAllowedError(Exception):
    """Raised when a tool call is blocked by session policy."""

    def __init__(
        self,
        tool_name: str,
        *,
        tenant_id: Optional[str] = None,
        run_id: Optional[str] = None,
        session_id: Optional[str] = None,
        reason: str = "blocked by session policy",
    ):
        self.tool_name = tool_name
        self.tenant_id = tenant_id
        self.run_id = run_id
        self.session_id = session_id
        self.reason = reason
        super().__init__(f"Tool '{tool_name}' not allowed: {reason}")


class GuardrailViolationError(Exception):
    """Raised when a guardrail blocks the request or output."""

    def __init__(
        self,
        *,
        http_status: int = 403,
        code: str = "GUARDRAIL_VIOLATION",
        message: str,
        phase: str,
        rule: Optional[str] = None,
        tenant_id: Optional[str] = None,
        run_id: Optional[str] = None,
        session_id: Optional[str] = None,
        tool_name: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        self.code = code
        self.message = message
        self.http_status = int(http_status)
        self.phase = phase  # "before_model" | "after_model" | "tool_result" (etc)
        self.rule = rule
        self.tenant_id = tenant_id
        self.run_id = run_id
        self.session_id = session_id
        self.tool_name = tool_name
        self.details = details or {}
        super().__init__(message)

def _matches_any(patterns: List[str], value: str) -> bool:
    for pat in patterns:
        if fnmatchcase(value, pat):
            return True
    return False


class _GuardedMCPSession:
    """Proxy session that enforces tool policy before calling call_tool()."""

    def __init__(self, session: Any, wrapper: "MCPWrapper"):
        self._session = session
        self._wrapper = wrapper

    async def call_tool(self, name: str, *args: Any, **kwargs: Any) -> Any:
        arguments = self._wrapper._extract_tool_arguments(args, kwargs)
        self._wrapper._enforce_tool_allowed(name, *args, **kwargs)
        result = await self._session.call_tool(name, *args, **kwargs)
        return self._wrapper._wrap_tool_result(name, result, arguments=arguments)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._session, item)


class _GuardedMCPClient:
    """Proxy client that wraps sessions and enforces tool policy."""

    def __init__(self, client: Any, wrapper: "MCPWrapper"):
        self._client = client
        self._wrapper = wrapper

    async def get_session(self, *args: Any, **kwargs: Any) -> Any:
        session = await self._client.get_session(*args, **kwargs)
        return _GuardedMCPSession(session, self._wrapper)

    async def call_tool(self, name: str, *args: Any, **kwargs: Any) -> Any:
        arguments = self._wrapper._extract_tool_arguments(args, kwargs)
        self._wrapper._enforce_tool_allowed(name, *args, **kwargs)
        result = await self._client.call_tool(name, *args, **kwargs)
        return self._wrapper._wrap_tool_result(name, result, arguments=arguments)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._client, item)

# Mapping provider -> LangChain class path
PROVIDER_IMPORTS = {
    "openai": ("langchain_openai", "ChatOpenAI"),
    "anthropic": ("langchain_anthropic", "ChatAnthropic"),
    "ollama": ("langchain_ollama", "ChatOllama"),
}


class MCPWrapper:
    """Enhanced wrapper for mcp-use that fully encapsulates the library"""

    def __init__(
        self,
        llm_provider: str,
        model: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        mcp_servers: Optional[Dict[str, Dict[str, Any]]] = None,
        max_steps: int = 30,
        verbose: bool = False,
        sandbox: bool = False,
        sandbox_options: Optional[Any] = None,  # può essere dict o Pydantic model
        disallowed_tools: Optional[List[str]] = None,
        use_server_manager: bool = False,
    ):
        """
        Initializes the MCP wrapper

        Args:
            llm_provider: Model provider (openai, anthropic, ollama)
            model: Model name
            api_key: API key (optional if set in environment)
            base_url: Base URL for custom providers
            temperature: Model temperature
            max_tokens: Maximum number of tokens
            mcp_servers: MCP servers configuration
            max_steps: Maximum steps for the agent
            verbose: Verbose mode for debugging
            sandbox: Use the E2B sandbox environment
            sandbox_options: Options for the sandbox
            disallowed_tools: Tools not allowed
            use_server_manager: Use server manager for automatic selection
        """
        self.llm_provider = llm_provider.lower()
        self.model = model
        self.api_key = api_key
        self.base_url = base_url or (os.getenv("OLLAMA_BASE_URL") if llm_provider == "ollama" else None)
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.mcp_servers = mcp_servers or {}
        self.has_mcp_servers = bool(self.mcp_servers)
        self.max_steps = max_steps
        self.verbose = verbose
        self.sandbox = sandbox
        self.sandbox_options = self._normalize_sandbox_options(sandbox_options)
        self.disallowed_tools = disallowed_tools
        self.use_server_manager = use_server_manager

        # Tool governance boundary (incremental refactor)
        self.tool_policy_engine = ToolPolicyEngine(deny_patterns=self.disallowed_tools or [])
        self.audit_recorder = InMemoryAuditRecorder()
        self.guardrail_runner = GuardrailRunner(
            audit_recorder=self.audit_recorder,
            violation_error_cls=GuardrailViolationError,
        )

        # Internal state
        self._agent = None
        self._client = None
        self._initialized = False
        self._steps_used = 0
        self._last_server_used = None
        self._active_server_name = None

        # Request/session context (for logs + structured errors)
        self.tenant_id: Optional[str] = None
        self.run_id: Optional[str] = None
        self.session_id: Optional[str] = None

        # Guardrail pipelines (LangChain-inspired hooks)
        # Each callable can be sync or async.
        # - before_model: fn(ctx) -> ctx
        # - after_model: fn(ctx, output) -> output
        self.before_model_guardrails: List[Callable[[GuardrailContext], Union[GuardrailContext, Awaitable[GuardrailContext]]]] = []
        self.after_model_guardrails: List[Callable[[GuardrailContext, Any], Union[Any, Awaitable[Any]]]] = []

        # Global guardrails switch (session-scoped). If disabled, no guardrail will run.
        self.guardrails_enabled: bool = True

        # Optional per-guardrail timeout to avoid hanging requests.
        # When set, a timeout raises GuardrailViolationError(code=MCP_GUARDRAIL_TIMEOUT).
        self.guardrail_timeout_seconds: Optional[float] = None

        # Bias detector service client (session-scoped; optional)
        self._bias_detector_service: Optional[BiasDetectorClient] = None

        # Local MVP guardrails (LangChain before/after pattern): configurable PII handling.
        # Defaults:
        # - input (before_model): block (security default)
        # - output (after_model): redact (backward-compatible)
        self.pii_mode: str = "redact"
        self.pii_input_mode: str = "block"
        self._pii_after_model_guardrail = None
        self._pii_before_model_guardrail = None
        self.set_pii_mode(self.pii_mode)
        self.set_pii_input_mode(self.pii_input_mode)

        # Bias detector guardrail (MVP0: after_model only).
        # Default is off (no-op) to avoid breaking behavior.
        self.bias_mode: str = "off"
        self._bias_after_model_guardrail = None
        self.set_bias_mode(self.bias_mode)

        # Validate and import dependencies
        self._validate_config()
        self._import_dependencies()

    @staticmethod
    def _normalize_sandbox_options(sandbox_options: Optional[Any]) -> Dict[str, Any]:
        """Normalizes sandbox options to a dictionary compatible with mcp-use
           Accepts:
           - my models.config.SandboxOptions (Pydantic model)
           - dict
           - None
        """
        if sandbox_options is None:
            return {}

        # Pydantic v2
        if hasattr(sandbox_options, "model_dump"):
            return sandbox_options.model_dump(exclude_none=True)

        # Pydantic v1 (per sicurezza)
        if hasattr(sandbox_options, "dict"):
            return sandbox_options.dict(exclude_none=True)  # type: ignore[call-arg]

        # Già un dict
        if isinstance(sandbox_options, dict):
            return sandbox_options

        # Fallback generico per oggetti con attributi
        try:
            return {
                "api_key": getattr(sandbox_options, "api_key", None),
                "sandbox_template_id": getattr(sandbox_options, "sandbox_template_id", "base"),
                "supergateway_command": getattr(
                    sandbox_options,
                    "supergateway_command",
                    "npx -y supergateway",
                ),
            }
        except Exception:
            raise ConfigurationError(
                f"Unsupported sandbox_options type: {type(sandbox_options)!r}. "
                "Expected dict or Pydantic BaseModel."
            )

    def set_context(
        self,
        *,
        tenant_id: Optional[str] = None,
        run_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> None:
        """Set request/session context for logging and structured errors."""
        self.tenant_id = tenant_id
        self.run_id = run_id
        self.session_id = session_id

    def set_pii_mode(self, mode: Optional[str]) -> None:
        """Configure session-scoped PII handling for the local after_model guardrail.

        Backward compatible: if mode is missing/invalid, defaults to "redact".
        """

        normalized_mode = (mode or "redact").strip().lower()
        if normalized_mode not in {"off", "redact", "block"}:
            normalized_mode = "redact"

        # Persist the selected mode (useful for debugging and tests).
        self.pii_mode = normalized_mode

        # Disable (uninstall) the guardrail when mode is off.
        if normalized_mode == "off":
            old_gr = getattr(self, "_pii_after_model_guardrail", None)
            if old_gr is not None and getattr(self, "after_model_guardrails", None) is not None:
                try:
                    self.after_model_guardrails = [gr for gr in self.after_model_guardrails if gr is not old_gr]
                except Exception:
                    pass
            self._pii_after_model_guardrail = None
            return

        new_gr = make_pii_after_model_guardrail(mode=normalized_mode)

        # Replace the previously installed PII guardrail (if present).
        old_gr = getattr(self, "_pii_after_model_guardrail", None)
        if not hasattr(self, "after_model_guardrails") or self.after_model_guardrails is None:
            self.after_model_guardrails = []

        replaced = False
        if old_gr is not None:
            for idx, gr in enumerate(self.after_model_guardrails):
                if gr is old_gr:
                    self.after_model_guardrails[idx] = new_gr
                    replaced = True
                    break

        if not replaced:
            self.after_model_guardrails.append(new_gr)

        self._pii_after_model_guardrail = new_gr

    def set_pii_input_mode(self, mode: Optional[str]) -> None:
        """Configure session-scoped PII handling for the local before_model guardrail.

        Modes:
          - off: no-op (uninstall guardrail)
          - redact: rewrite ctx.query by replacing PII with placeholders
          - block: raise GuardrailViolationError(code=PII_DETECTED, phase=before_model)
        """

        normalized_mode = (mode or "block").strip().lower()
        if normalized_mode not in {"off", "redact", "block"}:
            normalized_mode = "block"

        self.pii_input_mode = normalized_mode

        # Disable (uninstall) when off.
        if normalized_mode == "off":
            old_gr = getattr(self, "_pii_before_model_guardrail", None)
            if old_gr is not None and getattr(self, "before_model_guardrails", None) is not None:
                try:
                    self.before_model_guardrails = [gr for gr in self.before_model_guardrails if gr is not old_gr]
                except Exception:
                    pass
            self._pii_before_model_guardrail = None
            return

        new_gr = make_pii_before_model_guardrail(mode=normalized_mode)
        old_gr = getattr(self, "_pii_before_model_guardrail", None)
        if not hasattr(self, "before_model_guardrails") or self.before_model_guardrails is None:
            self.before_model_guardrails = []

        replaced = False
        if old_gr is not None:
            for idx, gr in enumerate(self.before_model_guardrails):
                if gr is old_gr:
                    self.before_model_guardrails[idx] = new_gr
                    replaced = True
                    break

        if not replaced:
            self.before_model_guardrails.append(new_gr)

        self._pii_before_model_guardrail = new_gr

    def set_bias_mode(self, mode: Optional[str]) -> None:
        # Configure session-scoped bias handling for the local after_model guardrail.
        #
        # MVP0 modes:
        #   - off: no-op (uninstall guardrail)
        #   - block: raise GuardrailViolationError(code=BIAS_DETECTED, phase=after_model)

        normalized_mode = (mode or "off").strip().lower()
        if normalized_mode not in {"off", "block"}:
            normalized_mode = "off"

        self.bias_mode = normalized_mode

        # Disable (uninstall) the guardrail when mode is off.
        if normalized_mode == "off":
            old_gr = getattr(self, "_bias_after_model_guardrail", None)
            if old_gr is not None and getattr(self, "after_model_guardrails", None) is not None:
                try:
                    self.after_model_guardrails = [gr for gr in self.after_model_guardrails if gr is not old_gr]
                except Exception:
                    pass
            self._bias_after_model_guardrail = None
            return

        new_gr = make_bias_after_model_guardrail(mode=normalized_mode)

        # Replace the previously installed bias guardrail (if present).
        old_gr = getattr(self, "_bias_after_model_guardrail", None)
        if not hasattr(self, "after_model_guardrails") or self.after_model_guardrails is None:
            self.after_model_guardrails = []

        replaced = False
        if old_gr is not None:
            for idx, gr in enumerate(self.after_model_guardrails):
                if gr is old_gr:
                    self.after_model_guardrails[idx] = new_gr
                    replaced = True
                    break

        if not replaced:
            self.after_model_guardrails.append(new_gr)

        self._bias_after_model_guardrail = new_gr

    def set_bias_settings(
        self,
        *,
        mode: Optional[str],
        base_url: Optional[str] = None,
        timeout_seconds: float = 5.0,
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
    ) -> None:
        """Configure session-scoped bias handling.

        If base_url is provided, mcp-bridge will use bias-detector-service.
        Otherwise it falls back to the built-in detector selected via env vars.
        """

        normalized_mode = (mode or "off").strip().lower()
        if normalized_mode not in {"off", "block"}:
            normalized_mode = "off"

        # Persist selection for visibility/debug.
        self.bias_mode = normalized_mode

        # Uninstall guardrail if off
        if normalized_mode == "off":
            old_gr = getattr(self, "_bias_after_model_guardrail", None)
            if old_gr is not None and getattr(self, "after_model_guardrails", None) is not None:
                try:
                    self.after_model_guardrails = [gr for gr in self.after_model_guardrails if gr is not old_gr]
                except Exception:
                    pass
            self._bias_after_model_guardrail = None
            return

        # If no base_url, use the legacy local detector guardrail.
        if not base_url:
            self.set_bias_mode(normalized_mode)
            return

        # Create/replace the service client for this session.
        try:
            self._bias_detector_service = BiasDetectorClient(
                base_url=base_url,
                timeout_seconds=float(timeout_seconds),
            )
        except Exception as e:
            raise ConfigurationError(f"Invalid bias-detector-service configuration: {e}")

        new_gr = make_bias_after_model_guardrail_service(
            client=self._bias_detector_service,
            mode=normalized_mode,
            threshold=float(threshold),
            top_k=int(top_k),
            active_categories=active_categories,
            unsafe_labels=unsafe_labels,
            model_id=model_id,
            revision=revision,
            return_all_scores=bool(return_all_scores),
            return_char_spans=bool(return_char_spans),
            checks=checks,
            fail_closed=bool(fail_closed),
        )

        old_gr = getattr(self, "_bias_after_model_guardrail", None)
        if not hasattr(self, "after_model_guardrails") or self.after_model_guardrails is None:
            self.after_model_guardrails = []

        replaced = False
        if old_gr is not None:
            for idx, gr in enumerate(self.after_model_guardrails):
                if gr is old_gr:
                    self.after_model_guardrails[idx] = new_gr
                    replaced = True
                    break

        if not replaced:
            self.after_model_guardrails.append(new_gr)

        self._bias_after_model_guardrail = new_gr

    def set_tool_policy_engine(self, engine: ToolPolicyEngine) -> None:
        """Replace the active tool policy engine.

        Keeps MCPWrapper as the public façade while moving policy decisions
        out of the transport/runtime boundary.
        """
        self.tool_policy_engine = engine

    def configure_tool_policies(
        self,
        *,
        allow_patterns: Optional[List[str]] = None,
        deny_patterns: Optional[List[str]] = None,
        policies: Optional[List[ToolPolicy]] = None,
    ) -> None:
        self.tool_policy_engine = ToolPolicyEngine(
            allow_patterns=allow_patterns,
            deny_patterns=deny_patterns if deny_patterns is not None else (self.disallowed_tools or []),
            policies=policies,
        )

    def get_audit_events(self) -> List[AuditEvent]:
        return self.audit_recorder.list_events()

    def _record_audit_event(
        self,
        *,
        event_type: str,
        outcome: str,
        tool_name: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        recorder = getattr(self, "audit_recorder", None)
        if recorder is None:
            recorder = InMemoryAuditRecorder()
            self.audit_recorder = recorder
        try:
            recorder.record(
                AuditEvent(
                    event_type=event_type,
                    timestamp=utc_now_iso(),
                    tenant_id=self.tenant_id,
                    run_id=self.run_id,
                    session_id=self.session_id,
                    tool_name=tool_name,
                    outcome=outcome,
                    details=details or {},
                )
            )
        except Exception:
            logger.debug("Failed to record audit event", exc_info=True)

    def set_guardrails_enabled(self, enabled: bool) -> None:
        """Enable/disable ALL guardrails for this wrapper (session-scoped).

        When disabled, no before_model/after_model guardrail is executed.
        """
        self.guardrails_enabled = bool(enabled)

    def _get_guardrail_runner(self) -> GuardrailRunner:
        runner = getattr(self, "guardrail_runner", None)
        recorder = getattr(self, "audit_recorder", None)
        if recorder is None:
            recorder = InMemoryAuditRecorder()
            self.audit_recorder = recorder
        if runner is None or getattr(runner, "audit_recorder", None) is not recorder:
            runner = GuardrailRunner(
                audit_recorder=recorder,
                violation_error_cls=GuardrailViolationError,
            )
            self.guardrail_runner = runner
        return runner

    def _wrap_tool_result(
        self,
        tool_name: str,
        result: Any,
        *,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Apply guardrails to tool results.

        Behavior is controlled by the same session-scoped switches:
        - guardrails_enabled = False -> no-op
        - pii_mode == 'redact' -> redact strings recursively (PII placeholders)
        - pii_mode == 'block' -> raise GuardrailViolationError(code=PII_DETECTED)
        - pii_mode == 'off' -> no-op

        NOTE: Tool policy enforcement (`disallowed_tools`) is enforced *before*
        this method and must NOT depend on `guardrails_enabled`.
        """
        ctx = GuardrailContext(
            tenant_id=self.tenant_id,
            run_id=self.run_id,
            session_id=self.session_id,
            server_name=getattr(self, "_active_server_name", None),
            tool_name=tool_name,
            arguments=arguments or {},
        )
        outcome = self._get_guardrail_runner().tool_result(
            ctx,
            result,
            enabled=getattr(self, "guardrails_enabled", True),
            pii_mode=getattr(self, "pii_mode", "redact"),
            redact_result=_redact_pii_in_obj,
            detect_result_pii=_detect_pii_in_obj,
        )
        return outcome.value

    async def _run_before_model_guardrails(self, ctx: GuardrailContext) -> GuardrailContext:
        outcome = await self._get_guardrail_runner().before_model(
            ctx,
            getattr(self, "before_model_guardrails", []),
            enabled=getattr(self, "guardrails_enabled", True),
            timeout_seconds=getattr(self, "guardrail_timeout_seconds", None),
        )
        return outcome.value

    async def _run_after_model_guardrails(self, ctx: GuardrailContext, output: Any) -> Any:
        outcome = await self._get_guardrail_runner().after_model(
            ctx,
            output,
            getattr(self, "after_model_guardrails", []),
            enabled=getattr(self, "guardrails_enabled", True),
            timeout_seconds=getattr(self, "guardrail_timeout_seconds", None),
        )
        return outcome.value

    def _extract_tool_arguments(self, args: Any, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        if kwargs:
            return dict(kwargs)
        if not args:
            return {}
        if len(args) == 1 and isinstance(args[0], dict):
            return dict(args[0])
        return {"args": list(args)}

    def _evaluate_tool_invocation_policy(self, tool_name: str, *, arguments: Optional[Dict[str, Any]] = None) -> ToolInvocationDecision:
        engine = getattr(self, "tool_policy_engine", None)
        if engine is None:
            engine = ToolPolicyEngine(deny_patterns=getattr(self, "disallowed_tools", None) or [])
            self.tool_policy_engine = engine
        ctx = ToolInvocationContext(
            tool_name=tool_name,
            arguments=arguments or {},
            tenant_id=self.tenant_id,
            run_id=self.run_id,
            session_id=self.session_id,
            server_name=getattr(self, "_active_server_name", None),
        )
        return engine.evaluate(ctx)

    def _enforce_tool_allowed(self, tool_name: str, *args: Any, **kwargs: Any) -> None:
        """Last-gate enforcement before any MCP tool call."""
        arguments = self._extract_tool_arguments(args, kwargs)
        decision = self._evaluate_tool_invocation_policy(tool_name, arguments=arguments)
        server_name = getattr(self, "_active_server_name", None)
        logger.info(
            "mcp_tool_policy_decision",
            extra={
                "tenant_id": self.tenant_id,
                "run_id": self.run_id,
                "session_id": self.session_id,
                "tool_name": tool_name,
                "allowed": decision.allowed,
                "reason": decision.reason,
                "risk_class": decision.risk_class,
            },
        )
        self._record_audit_event(
            event_type="tool_policy_decision",
            outcome="allowed" if decision.allowed else "blocked",
            tool_name=tool_name,
            details={
                "reason": decision.reason,
                "risk_class": decision.risk_class,
                "validation_errors": list(decision.validation_errors),
                "arguments_present": bool(arguments),
                "matched_policy": getattr(decision.matched_policy, "pattern", None),
                "server_name": server_name,
            },
        )
        if not decision.allowed:
            raise MCPToolNotAllowedError(
                tool_name,
                tenant_id=self.tenant_id,
                run_id=self.run_id,
                session_id=self.session_id,
                reason=decision.reason,
            )


    def _validate_config(self):
        """Validates the initial configuration"""
        if not self.llm_provider:
            raise ConfigurationError("LLM provider not specified")

        if not self.model:
            raise ConfigurationError("Model not specified")

        # if not self.mcp_servers:
        #     raise ConfigurationError("No MCP servers configured")
        if self.has_mcp_servers:
            # Validate MCP servers
            for name, config in self.mcp_servers.items():
                if not config.get("command") and not config.get("url"):
                    raise ConfigurationError(
                        f"Server {name}: must have 'command' or 'url'"
                    )

    def _import_dependencies(self):
        """Imports required dependencies with enhanced error handling"""
        # Import mcp-use
        try:
            from mcp_use import MCPAgent, MCPClient
            from mcp_use.types.sandbox import SandboxOptions

            self.MCPAgent = MCPAgent
            self.MCPClient = MCPClient
            self.SandboxOptions = SandboxOptions
            logger.debug("mcp-use successfully imported")
        except ImportError as e:
            raise DependencyError(f"mcp-use not installed: {e}")

        # Import LangChain provider
        if self.llm_provider not in PROVIDER_IMPORTS:
            raise ConfigurationError(f"Unsupported provider: {self.llm_provider}")

        module_name, class_name = PROVIDER_IMPORTS[self.llm_provider]
        try:
            module = __import__(module_name, fromlist=[class_name])
            self.ChatLLM = getattr(module, class_name)
            logger.debug(f"{module_name} successfully imported")
        except ImportError as e:
            raise DependencyError(f"{module_name} not installed: {e}")

    def _create_llm(self):
        """Creates the LLM model instance with error handling"""
        try:
            # Costruzione centralizzata dei kwargs (base + provider-specific)
            kwargs = self._build_llm_kwargs()

            llm = self.ChatLLM(**kwargs)
            logger.debug(
                f"LLM {self.llm_provider}/{self.model} successfully created "
                f"with kwargs={ {k: v for k, v in kwargs.items() if k != 'api_key'} }"
            )
            return llm

        except Exception as e:
            raise MCPWrapperError(f"Error creating LLM model: {e}")


    def _build_llm_kwargs(self) -> Dict[str, Any]:
        """
        Costruisce i kwargs di base per il modello LLM e delega
        la parte provider-specific a _apply_provider_specific_kwargs.
        """
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "temperature": self.temperature,
        }

        if self.max_tokens:
            kwargs["max_tokens"] = self.max_tokens

        return self._apply_provider_specific_kwargs(kwargs)


    def _apply_provider_specific_kwargs(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Aggiunge ai kwargs le opzioni specifiche del provider
        (API key, base_url, ecc.).
        """
        # OpenAI / Anthropic: gestione API key
        if self.llm_provider in ("openai", "anthropic"):
            env_key = f"{self.llm_provider.upper()}_API_KEY"
            api_key = self.api_key or os.getenv(env_key)

            if not api_key:
                raise ConfigurationError(
                    f"Missing API key for provider '{self.llm_provider}'. "
                    f"Provide it explicitly or set {env_key} env var."
                )

            kwargs["api_key"] = api_key
            return kwargs

        # Ollama: gestione base_url
        if self.llm_provider == "ollama":
            base_url = self.base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
            kwargs["base_url"] = base_url
            return kwargs

        # Provider non supportato
        raise ConfigurationError(f"Unsupported LLM provider: {self.llm_provider}")


    async def initialize(self):
        """Initializes the MCP agent and clients with automatic retry"""
        if self._initialized:
            logger.debug("MCPWrapper already initialized")
            return

        try:
            await retry_async(self._initialize_internal, max_retries=3, delay=1.0)
            self._initialized = True
            logger.info("MCPWrapper successfully initialized")

        except Exception as e:
            logger.error(f"Initialization error after all attempts: {e}")
            raise MCPWrapperError(f"Initialization failed: {e}")

    async def _initialize_internal(self):
        """Internal initialization logic"""
        llm = self._create_llm()

        # Configure MCP client
        client_kwargs = {"config": {"mcpServers": self.mcp_servers}}

        if self.sandbox:
            client_kwargs["sandbox"] = True
            if self.sandbox_options:
                client_kwargs["sandbox_options"] = {
                    "api_key": self.sandbox_options.get("api_key", os.getenv("E2B_API_KEY")),
                    "sandbox_template_id": self.sandbox_options.get("sandbox_template_id", "base"),
                    "supergateway_command": self.sandbox_options.get("supergateway_command", "npx -y supergateway"),
                }

        self._client = self.MCPClient(**client_kwargs)

        # Wrap client/session to enforce tool policy and allow minimal tool-result guardrails.
        # Tool policy enforcement is a no-op when disallowed_tools is None/empty.
        self._client = _GuardedMCPClient(self._client, self)

        # Create the agent
        agent_kwargs = {
            "llm": llm,
            "client": self._client,
            "max_steps": self.max_steps,
            "use_server_manager": self.use_server_manager,
            "verbose": self.verbose,
        }

        if self.disallowed_tools:
            agent_kwargs["disallowed_tools"] = self.disallowed_tools

        self._agent = self.MCPAgent(**agent_kwargs)

    async def run_query(
        self,
        query: str,
        max_steps: Optional[int] = None,
        server_name: Optional[str] = None,
    ) -> str:
        """
        Executes a query using the MCP agent

        Args:
            query: The query to process
            max_steps: Override for maximum steps (optional)
            server_name: Specific server name to use (optional)

        Returns:
            The agent's response as a string
        """
        if not self._initialized:
            await self.initialize()

        # Guardrails: before_model (validation/normalization)
        ctx = GuardrailContext(
            tenant_id=self.tenant_id,
            run_id=self.run_id,
            session_id=self.session_id,
            query=query,
            server_name=server_name,
        )
        ctx = await self._run_before_model_guardrails(ctx)
        query = ctx.query or ""

        if not query.strip():
            raise ValueError("Empty query not allowed")

        try:
            previous_active_server_name = getattr(self, "_active_server_name", None)
            self._active_server_name = server_name
            logger.debug(f"Executing query: {query[:100]}...")

            # Prepare parameters
            run_kwargs: Dict[str, Any] = {"query": query}

            if max_steps is not None:
                run_kwargs["max_steps"] = max_steps

            if server_name:
                if server_name not in self.mcp_servers:
                    raise ConfigurationError(f"Server '{server_name}' not configured")
                run_kwargs["server_name"] = server_name
                self._last_server_used = server_name

            # Define a separate async function for retry
            async def execute_agent_run():
                return await self._agent.run(**run_kwargs)

            # Execute the query with retry
            result = await retry_async(
                execute_agent_run, max_retries=2, delay=0.5
            )

            # Update stats
            self._steps_used = getattr(self._agent, "steps_used", 0)

            if not self._last_server_used and hasattr(self._agent, "last_server_used"):
                self._last_server_used = self._agent.last_server_used

            logger.debug(f"Query completed in {self._steps_used} steps")
            output = str(result)
            output = await self._run_after_model_guardrails(ctx, output)
            output = _extract_user_visible_answer(output)
            effective_server_name = server_name or self._last_server_used
            self._record_audit_event(
                event_type="query_execution",
                outcome="completed",
                details={
                    "max_steps": max_steps if max_steps is not None else self.max_steps,
                    "server_name": effective_server_name,
                    "steps_used": self._steps_used,
                },
            )
            return output

        except MCPToolNotAllowedError:
            self._record_audit_event(
                event_type="query_execution",
                outcome="blocked",
                details={"reason": "tool_policy", "server_name": server_name},
            )
            raise
        except GuardrailViolationError:
            self._record_audit_event(
                event_type="query_execution",
                outcome="blocked",
                details={"reason": "guardrail", "server_name": server_name},
            )
            raise
        except Exception as e:
            logger.error(f"Query execution error: {e}")
            raise MCPWrapperError(f"Query execution failed: {e}")
        finally:
            self._active_server_name = previous_active_server_name

    async def close(self):
        """Closes connections and releases resources"""
        # Close bias-detector-service client (if any)
        if getattr(self, "_bias_detector_service", None) is not None:
            try:
                await self._bias_detector_service.close()
            except Exception:
                pass
            self._bias_detector_service = None

        if self._client:
            try:
                await self._client.close_all_sessions()
                logger.debug("MCP client closed successfully")
            except Exception as e:
                logger.warning(f"Error closing MCP client: {e}")

        self._agent = None
        self._client = None
        self._initialized = False
        logger.debug("MCPWrapper closed")

    @property
    def steps_used(self) -> int:
        """Returns the number of steps used in the last run"""
        return self._steps_used

    @property
    def last_server_used(self) -> Optional[str]:
        """Returns the last server used"""
        return self._last_server_used

    @property
    def is_initialized(self) -> bool:
        """Indicates if the wrapper has been initialized"""
        return self._initialized

    def get_config_summary(self) -> Dict[str, Any]:
        """Returns a summary of the configuration"""
        return {
            "llm_provider": self.llm_provider,
            "model": self.model,
            "max_steps": self.max_steps,
            "sandbox": self.sandbox,
            "servers": list(self.mcp_servers.keys()),
            "use_server_manager": self.use_server_manager,
            "initialized": self._initialized,
        }

    async def test_connection(self) -> Dict[str, bool]:
        """Tests the connection to configured MCP servers"""
        if not self._initialized:
            await self.initialize()

        results: Dict[str, bool] = {}
        for server_name in self.mcp_servers.keys():
            try:
                await self.run_query("ping", max_steps=1, server_name=server_name)
                results[server_name] = True
            except Exception as e:
                logger.warning(f"Connection test failed for {server_name}: {e}")
                results[server_name] = False
