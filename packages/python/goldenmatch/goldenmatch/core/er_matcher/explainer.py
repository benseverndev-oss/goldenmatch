"""Per-decision explainer for the local ER-matcher (1.5B) verdict.

Pairs the model's match/no-match verdict with a human-readable rationale grounded
in the model's OWN learned field-importance — the Layer-2 abstraction of the
causally-validated match direction from the mechanistic-interpretability work
(``docs/design/2026-08-02-15b-decision-geometry-layer1.md``). It is NOT a fresh
just-so story: the per-field importance below is the standardized regression of the
*causally-locked* match direction onto per-field agreement, so the explanation
reflects what actually moves the model's decision — bounded by an honest
faithfulness number (the fields explain ~half the decision geometry; the rest is
context/interactions the per-field story cannot capture).

Pure + model-free (jaro-winkler agreement + a weight table): given two records and
the model's verdict, it explains. The model itself is only needed to *produce* the
verdict; :meth:`LocalLlamaAdapter.score_and_explain` wires the two together.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

# Learned person-record field importance (Layer 2). Standardized coefficients of
# the causally-validated match direction (layer 14) regressed onto per-field
# jaro-winkler agreement, on historical_50k against HARD (shared-surname) negatives.
# Reading: against surname look-alikes the model keys on first_name + birth_place,
# and has LEARNED to down-weight surname (uninformative among look-alikes) and dob
# (corrupted/unreliable in that data). `postcode` aliases `postcode_fake`.
PERSON_FIELD_IMPORTANCE: dict[str, float] = {
    "first_name": 0.42,
    "birth_place": 0.30,
    "occupation": 0.15,
    "postcode_fake": 0.08,
    "postcode": 0.08,
    "surname": 0.04,
    "dob": 0.01,
}

# How much of the model's decision geometry the per-field story explains (R^2 of the
# Layer-2 regression). The explanation is faithful to THIS degree, not perfectly.
PERSON_IMPORTANCE_FAITHFULNESS_R2 = 0.51

# neutral weight for a field with no learned importance (unknown schema)
DEFAULT_FIELD_IMPORTANCE = 0.10

_AGREE_THRESHOLD = 0.85   # >= this: the fields agree (supporting evidence)
_CONFLICT_THRESHOLD = 0.60  # <= this: the fields conflict (opposing evidence)


@dataclass
class FieldSignal:
    """One field's contribution to the explanation."""

    field: str
    value_a: str
    value_b: str
    agreement: float | None  # jaro-winkler in [0,1]; None if missing on a side
    importance: float
    kind: str  # "support" | "conflict" | "partial" | "missing"


@dataclass
class PairExplanation:
    """The model's verdict + a field-grounded rationale."""

    match: bool
    confidence: float
    signals: list[FieldSignal]
    rationale: str
    faithfulness_r2: float | None
    learned_weights_applied: bool
    field_story_agrees_with_verdict: bool

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def _agreement(va: Any, vb: Any) -> float | None:
    """Jaro-winkler agreement of two field values, or None if missing on a side."""
    sa = "" if va is None else str(va).strip()
    sb = "" if vb is None else str(vb).strip()
    if not sa or not sb:
        return None
    if sa == sb:
        return 1.0
    import jellyfish

    return float(jellyfish.jaro_winkler_similarity(sa, sb))


def _importance_for(field: str, weights: dict[str, float] | None) -> tuple[float, bool]:
    """(importance, is_learned) for a field. Explicit ``weights`` win; otherwise the
    bundled person profile; otherwise a neutral default (not learned)."""
    if weights is not None and field in weights:
        return float(weights[field]), True
    if field in PERSON_FIELD_IMPORTANCE:
        return PERSON_FIELD_IMPORTANCE[field], True
    return DEFAULT_FIELD_IMPORTANCE, False


def explain_pair(
    row_a: dict[str, Any],
    row_b: dict[str, Any],
    columns: list[str],
    *,
    match: bool,
    confidence: float,
    weights: dict[str, float] | None = None,
) -> PairExplanation:
    """Explain the model's verdict for one record pair.

    ``match``/``confidence`` are the model's own verdict (this function does not
    re-decide). ``weights`` overrides the learned per-field importance; when None,
    the bundled person profile is applied to matching field names (neutral default
    otherwise). Returns a :class:`PairExplanation` with per-field signals, a
    human-readable rationale, and the faithfulness bound.
    """
    signals: list[FieldSignal] = []
    any_learned = False
    for f in columns:
        agr = _agreement(row_a.get(f), row_b.get(f))
        imp, learned = _importance_for(f, weights)
        any_learned = any_learned or learned
        if agr is None:
            kind = "missing"
        elif agr >= _AGREE_THRESHOLD:
            kind = "support"
        elif agr <= _CONFLICT_THRESHOLD:
            kind = "conflict"
        else:
            kind = "partial"
        signals.append(FieldSignal(
            field=f,
            value_a="" if row_a.get(f) is None else str(row_a.get(f)),
            value_b="" if row_b.get(f) is None else str(row_b.get(f)),
            agreement=agr, importance=imp, kind=kind,
        ))

    # rank supporting / opposing evidence by learned importance
    supports = sorted([s for s in signals if s.kind == "support"],
                      key=lambda s: -s.importance)
    conflicts = sorted([s for s in signals if s.kind == "conflict"],
                       key=lambda s: -s.importance)

    # does the field story point the same way as the model's verdict?
    support_mass = sum(s.importance for s in supports)
    conflict_mass = sum(s.importance for s in conflicts)
    story_says_match = support_mass >= conflict_mass
    agrees = story_says_match == bool(match)

    rationale = _render_rationale(
        match, confidence, supports, conflicts, agrees, any_learned
    )
    return PairExplanation(
        match=bool(match), confidence=float(confidence), signals=signals,
        rationale=rationale,
        faithfulness_r2=(PERSON_IMPORTANCE_FAITHFULNESS_R2 if any_learned else None),
        learned_weights_applied=any_learned,
        field_story_agrees_with_verdict=agrees,
    )


def _fmt(s: FieldSignal) -> str:
    a = s.agreement
    lvl = "exact" if a is not None and a >= 0.999 else (f"{a:.2f}" if a is not None else "n/a")
    return f"{s.field} ({s.value_a!r} vs {s.value_b!r}, {lvl})"


def _render_rationale(
    match: bool, confidence: float, supports: list[FieldSignal],
    conflicts: list[FieldSignal], agrees: bool, learned: bool,
) -> str:
    verdict = "MATCH" if match else "NO MATCH"
    parts = [f"{verdict} (confidence {confidence:.2f})."]
    if supports:
        parts.append("Supporting: " + ", ".join(_fmt(s) for s in supports[:3]) + ".")
    if conflicts:
        parts.append("Opposing: " + ", ".join(_fmt(s) for s in conflicts[:3]) + ".")
    if not supports and not conflicts:
        parts.append("Fields are mostly missing or partial — the verdict rests on "
                     "context the per-field view can't summarize.")
    if not agrees:
        parts.append("Note: the field-agreement view points the other way, so this "
                     "explanation is low-confidence for this pair.")
    if learned:
        parts.append(f"(Field importance is the model's learned weighting; it "
                     f"accounts for ~{int(PERSON_IMPORTANCE_FAITHFULNESS_R2 * 100)}% "
                     f"of the model's decision geometry.)")
    return " ".join(parts)
