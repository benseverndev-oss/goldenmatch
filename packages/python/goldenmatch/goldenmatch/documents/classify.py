"""Doctype classification kernel: the fixed classify prompt + the response parser.
Native (documents-core) is the source of truth; the pure-Python path below is the
lossy fallback + the thing the parity corpus guards. No VLM call here -- just the
deterministic prompt string + response parsing.
"""
from __future__ import annotations

import json
import math

from goldenmatch.documents.types import ClassifyResult

_CLASSIFY_PROMPT = (
    'You are shown a document. Classify it as exactly one of these types: '
    'invoice, po, statement, receipt. If it is none of these, answer "generic". '
    'Return ONLY JSON: {"doctype": "<one of: invoice|po|statement|receipt|generic>", '
    '"confidence": <0..1>}. No prose.'
)

_DOCTYPES = frozenset({"invoice", "po", "statement", "receipt", "generic"})


def _strip_fence(text: str) -> str:
    """Drop a leading ```json (or ```) fence -- mirrors `_openai.parse_message_text`
    (rsplit on the LAST ```), NOT a trailing-only strip."""
    t = text.strip()
    if t.startswith("```"):
        nl = t.find("\n")
        if nl != -1:
            t = t[nl + 1:]
            t = t.rsplit("```", 1)[0]
        # no newline -> leave as-is (Python edge case)
    return t.strip()


def _pure_prompt() -> str:
    """Pure-Python constant -- NEVER dispatches to native. Used by the parity harness."""
    return _CLASSIFY_PROMPT


def _pure_parse(text: str) -> ClassifyResult:
    """Pure-Python parser -- NEVER dispatches to native. Used by the parity harness."""
    obj = json.loads(_strip_fence(text))  # raises ValueError (JSONDecodeError) on bad JSON
    if not isinstance(obj, dict):
        raise ValueError("classify response is not a JSON object")
    doctype = obj.get("doctype")
    if not isinstance(doctype, str) or doctype not in _DOCTYPES:
        raise ValueError(f"unknown doctype: {doctype!r}")
    if "confidence" not in obj:
        raise ValueError("classify response missing 'confidence'")
    conf = obj["confidence"]
    if isinstance(conf, bool) or not isinstance(conf, (int, float)):
        raise ValueError("confidence is not a number")
    conf = float(conf)
    # Python json.loads accepts bare NaN/Infinity/-Infinity; Rust serde_json is
    # strict-JSON and REJECTS those tokens (-> Err). Match Rust: reject non-finite
    # BEFORE the clamp (else NaN->1.0 / Infinity->1.0 / -Infinity->0.0 diverges).
    if not math.isfinite(conf):
        raise ValueError("confidence is not finite")
    conf = max(0.0, min(1.0, conf))
    return ClassifyResult(doctype=doctype, confidence=conf)


def classify_prompt() -> str:
    from goldenmatch.core._native_loader import native_enabled, native_module

    if native_enabled("documents") and (nm := native_module()) is not None and hasattr(
        nm, "documents_classify_prompt"
    ):
        return nm.documents_classify_prompt()
    return _pure_prompt()


def parse_classify(text: str) -> ClassifyResult:
    from goldenmatch.core._native_loader import native_enabled, native_module

    if native_enabled("documents") and (nm := native_module()) is not None and hasattr(
        nm, "documents_parse_classify"
    ):
        out = json.loads(nm.documents_parse_classify(text))  # raises ValueError on bad input
        return ClassifyResult(doctype=out["doctype"], confidence=float(out["confidence"]))
    return _pure_parse(text)
