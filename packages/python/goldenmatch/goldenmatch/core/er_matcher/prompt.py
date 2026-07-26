"""Prompt / serializer / verdict-parser for the OSS ER-matcher LLM boost.

Single source of truth shared by three consumers (spec 2026-07-26):
  1. training-data generation (scripts/er_matcher/gen_pairs.py renders the same
     chat examples the model is fine-tuned on),
  2. inference (the Path-A local server + the future in-process adapter),
  3. the eval harness.

Versioned on purpose: ``SERIALIZER_VERSION`` is part of the model card. A change
to the serialization or rubric is a NEW model revision (the model was trained on
the old rendering), so bump the version and retrain -- never silently alter these
in a way that skews inference away from how the model was trained.

Pure stdlib (no third-party imports) so it is edge-safe and cheap to import.
"""
from __future__ import annotations

import json
import re
from typing import Any

SERIALIZER_VERSION = "v1"

# The task rubric. Kept terse + deterministic; the model is trained against this
# exact text. `missing != conflict` is load-bearing (ER records are sparse).
SYSTEM_RUBRIC = (
    "You are an entity-resolution adjudicator. You are given two records, A and B. "
    "Decide whether they refer to the SAME real-world entity.\n"
    "Rules:\n"
    "- Weigh agreements and conflicts across fields. Strong identifiers (email, "
    "phone, national id) agreeing is strong evidence of a match; conflicting on "
    "them is strong evidence against.\n"
    "- A field missing on one side is NOT a conflict -- ignore it, do not penalize.\n"
    "- Minor formatting/spelling differences, nicknames, and abbreviations of the "
    "SAME value still count as agreement.\n"
    "- Different values in the same field (e.g. two different emails) are a conflict.\n"
    'Respond with ONE line of compact JSON and nothing else: '
    '{"match": true|false, "confidence": 0.0-1.0, "reason": "<short>"}'
)


def _norm_cell(value: Any) -> str:
    """Render a single field value for the prompt. None / empty -> the explicit
    ``(missing)`` sentinel so the model sees absence (rubric: missing != conflict)
    rather than an ambiguous blank."""
    if value is None:
        return "(missing)"
    s = str(value).strip()
    return s if s else "(missing)"


def serialize_pair_v1(a: dict[str, Any], b: dict[str, Any]) -> str:
    """Deterministically render a record pair as the user-turn text.

    Field order is the SORTED union of both records' keys, so the rendering is
    stable regardless of dict insertion order (reproducibility: the training data
    and inference must serialize identically). Every field is shown for BOTH
    records (missing -> ``(missing)``) so the model can see one-sided absence.
    """
    fields = sorted(set(a) | set(b))
    a_lines = "\n".join(f"  {k}: {_norm_cell(a.get(k))}" for k in fields)
    b_lines = "\n".join(f"  {k}: {_norm_cell(b.get(k))}" for k in fields)
    return f"Record A:\n{a_lines}\nRecord B:\n{b_lines}"


def build_chat(a: dict[str, Any], b: dict[str, Any]) -> list[dict[str, str]]:
    """The chat-messages form used for both SFT examples and inference requests."""
    return [
        {"role": "system", "content": SYSTEM_RUBRIC},
        {"role": "user", "content": serialize_pair_v1(a, b)},
    ]


def render_target(match: bool, confidence: float, reason: str = "") -> str:
    """The assistant-turn (training label): the exact JSON the model must emit."""
    return json.dumps(
        {"match": bool(match), "confidence": round(float(confidence), 3), "reason": reason},
        separators=(",", ":"),
        ensure_ascii=False,
    )


_JSON_OBJ = re.compile(r"\{.*?\}", re.DOTALL)


def parse_verdict(text: str) -> dict[str, Any] | None:
    """Lenient parse of a model response into ``{match, confidence, reason}``.

    Tolerates prose/markdown around the JSON (```json fences, leading commentary)
    by extracting the first balanced-looking object. Coerces ``match`` to bool and
    clamps ``confidence`` to [0, 1]. Returns ``None`` on ANY failure -- the caller
    treats None as ABSTAIN (the boost contributes nothing), never a crash, exactly
    like the current hosted-LLM fallback contract.
    """
    if not text:
        return None
    candidates: list[str] = []
    stripped = text.strip()
    candidates.append(stripped)
    m = _JSON_OBJ.search(text)
    if m:
        candidates.append(m.group(0))
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict) or "match" not in obj:
            continue
        match = obj["match"]
        if isinstance(match, str):
            low = match.strip().lower()
            if low in ("true", "yes", "match", "1"):
                match = True
            elif low in ("false", "no", "no_match", "0"):
                match = False
            else:
                continue
        elif not isinstance(match, bool):
            continue
        conf = obj.get("confidence", 1.0 if match else 0.0)
        try:
            conf = float(conf)
        except (TypeError, ValueError):
            conf = 1.0 if match else 0.0
        conf = max(0.0, min(1.0, conf))
        reason = obj.get("reason", "")
        if not isinstance(reason, str):
            reason = str(reason)
        return {"match": bool(match), "confidence": conf, "reason": reason}
    return None
