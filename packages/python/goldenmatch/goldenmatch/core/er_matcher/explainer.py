"""Per-decision explainer for the local ER-matcher (1.5B) verdict.

Pairs the model's match/no-match verdict with a human-readable rationale grounded
in the model's OWN learned field-importance — the Layer-2 abstraction of the
causally-validated match direction from the mechanistic-interpretability work
(``docs/design/2026-08-02-15b-decision-geometry-layer1.md``). It is NOT a fresh
just-so story: the per-field importance below is the standardized regression of the
*causally-locked* match direction onto per-field agreement, so the explanation
reflects what actually moves the model's decision — bounded by an honest
faithfulness number.

**Two different questions, two tables.** ``PERSON_FIELD_IMPORTANCE`` says how much a
field's agreement level *tracks* the verdict (what this module scores with).
``PERSON_FIELD_CAUSAL_RANKING`` says which fields the model *needs*, measured by
ablating them on the live model. They disagree — notably on ``dob`` — and each is
right about its own question; see the comments on both. Swapping the ablation result
into the scoring weights was tried and measurably made the explanation *less*
faithful to the model's real verdict, so the two are kept separate on purpose.

Pure + model-free (jaro-winkler agreement + a weight table): given two records and
the model's verdict, it explains. The model itself is only needed to *produce* the
verdict; :meth:`LocalLlamaAdapter.score_and_explain` wires the two together.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

# Person-record field importance for SCORING the explanation: standardized
# coefficients of the causally-validated match direction (layer 14) regressed onto
# per-field jaro-winkler agreement, on historical_50k against HARD (shared-surname)
# negatives. `postcode` aliases `postcode_fake`.
#
# These answer "how much does this field's AGREEMENT LEVEL track the verdict?", which
# is the question `explain_pair` actually asks when it ranks supporting vs opposing
# evidence. Re-deriving them from causal ablation was tried and MEASURABLY REJECTED:
# ablation-magnitude weights scored held-out R^2 0.10 against the model's real P(match)
# versus 0.27 for these (5 seeds, one seed negative). Ablation answers a different
# question -- see PERSON_FIELD_CAUSAL_RANKING below. Do not swap one for the other.
PERSON_FIELD_IMPORTANCE: dict[str, float] = {
    "first_name": 0.42,
    "birth_place": 0.30,
    "occupation": 0.15,
    "postcode_fake": 0.08,
    "postcode": 0.08,
    "surname": 0.04,
    "dob": 0.01,
}

# Which fields the model NEEDS, measured by blanking each field on both records and
# watching the real verdict move (`modal_interp.py::causal_attribution`, 3 hard-negative
# seeds x 400 pairs; a random-negative run agrees). Ordered most- to least-necessary.
#
# This is deliberately SEPARATE from the scoring weights above, because the two
# disagree and both are correct about their own question. Most importantly: `dob` is a
# top-3 field by ablation despite its 0.01 scoring coefficient, so the model does NOT
# ignore date of birth -- never say it does. `occupation` is the reverse (0.15 to score
# with, but last by necessity).
#
# Caveat that keeps this honest: removing any ONE field changes the verdict in only
# ~19% of pairs. The decision is redundant, so this is a ranking of contributions to a
# joint decision, not a list of deciding factors.
PERSON_FIELD_CAUSAL_RANKING: tuple[str, ...] = (
    "birth_place", "first_name", "dob", "postcode_fake", "surname", "occupation",
)

# SUPERSEDED, kept only as the comparison that justifies the sparse table below.
# Dense OLS fit of the causally-validated direction onto all 36 signals. It explains
# more of the DIRECTION (0.888 vs 0.867) yet is worse on the thing that matters --
# frozen output-faithfulness R^2 0.53 +- 0.09 vs 0.64 +- 0.09 -- because OLS spreads
# weight across collinear signals that do not generalize. It also reads backwards:
# summed per field it ranks occupation FIRST and first_name LAST, inverting ablation.
# A tidy illustration that a better in-sample fit can be both less faithful and less
# legible. Do not ship it; `modal_interp.py` measures it as the `fixed_richer` row.
PERSON_SIGNAL_IMPORTANCE_DENSE: dict[str, float] = {
    "occupation__exact": 0.815, "occupation__missing": 0.448,
    "surname__edit_norm": -0.351, "dob__agreement": -0.333,
    "occupation__edit_norm": 0.332, "occupation__len_ratio": -0.292,
    "birth_place__exact": 0.265, "postcode_fake__edit_norm": -0.222,
    "dob__missing": -0.187, "dob__len_ratio": 0.187, "dob__edit_norm": -0.161,
    "surname__exact": 0.139, "surname__agreement": -0.136, "dob__exact": 0.128,
    "surname__len_ratio": -0.127, "postcode_fake__agreement": -0.126,
    "first_name__edit_norm": -0.125, "postcode_fake__len_ratio": 0.123,
    "birth_place__missing": 0.114, "occupation__agreement": -0.114,
    "surname__missing": -0.108, "occupation__conflict": 0.093,
    "birth_place__edit_norm": -0.074, "postcode_fake__exact": 0.056,
    "birth_place__conflict": 0.033, "first_name__missing": -0.028,
    "first_name__exact": 0.028, "surname__conflict": 0.028,
    "first_name__agreement": -0.023, "postcode_fake__missing": -0.02,
    "birth_place__len_ratio": 0.018, "first_name__len_ratio": 0.014,
    "postcode_fake__conflict": -0.014, "birth_place__agreement": -0.011,
    "dob__conflict": -0.008, "first_name__conflict": 0.005,
}

# THE HIGH-FAITHFULNESS BASIS. L1 (alpha=0.05) fit of the causally-validated
# direction onto the 36-signal decomposition; 14 signals survive.
#
# MEASURED, frozen, vs the model's real P(match) (5 seeds, cluster-disjoint, hard
# negatives): R^2 0.64 +- 0.09 on the standard probe -- but only 0.33 +- 0.06 once
# the pairs are CORRUPTION-MATCHED (see below). Most of its apparent advantage was
# the shortcut. The honest number is 0.33, against 0.26 for the 6-field table on the
# same de-confounded pairs: a real but modest edge, not the 2.4x the raw probe
# suggested.
#
# It is also the readable one: summed per field it ranks birth_place FIRST and
# occupation LAST, agreeing with ablation at both ends (rank-correlation vs the
# causal ranking goes -0.77 dense -> +0.43 sparse), and the surviving signals tell a
# coherent story by themselves -- `edit_norm` negative (further apart -> less match),
# `exact` positive. Sparsity bought accuracy AND legibility at once.
#
# Still not the DISPLAY table, and the REASON is now understood rather than guessed.
# Three independent fits (dense OLS, this L1, and a two-stage grouped fit) all rank
# first_name low on the richer basis, so it is not a collinearity artifact. Measured
# cause: `edit_norm` acts as a shared CORRUPTION-LEVEL proxy. Mean edit_norm across
# the six fields correlates -0.90 with the label on the probe set, and the edit_norm
# columns are far more cross-correlated (mean +0.38) than the agreement columns
# (+0.22) -- because probe matches are corrupted copies of one entity while
# non-matches are different entities, so overall string distance separates the
# classes on its own. The fits therefore rank whichever field is the best corruption
# proxy (surname, birth_place) above the field that actually carries identity
# evidence (first_name).
#
# CONFIRMED by re-measuring on corruption-matched pairs (each match paired with a
# non-match at the same corruption level, so that channel carries no label signal):
# this basis falls 0.64 -> 0.33 while the 6-field table barely moves (0.27 -> 0.26).
# The shipped weights were measuring real per-field evidence all along; the richer
# basis was substantially riding the shortcut.
#
# So read this basis as "predicts P(match) well on this probe set", NOT as "these are
# the signals the model reasons with". `PERSON_FIELD_IMPORTANCE` remains the prose,
# and per-field ABLATION (PERSON_FIELD_CAUSAL_RANKING) remains the trustworthy
# importance story -- it perturbs one field at a time on the live model, so a
# cross-field nuisance correlate cannot fool it.
PERSON_SIGNAL_IMPORTANCE: dict[str, float] = {
    "surname__edit_norm": -0.226, "birth_place__edit_norm": -0.19,
    "postcode_fake__edit_norm": -0.147, "birth_place__exact": 0.135,
    "first_name__edit_norm": -0.106, "dob__exact": 0.099,
    "surname__exact": 0.063, "dob__edit_norm": -0.058,
    "surname__missing": 0.053, "postcode_fake__exact": 0.041,
    "occupation__exact": 0.029, "birth_place__conflict": -0.028,
    "first_name__exact": 0.016, "occupation__edit_norm": -0.013,
}

# How much of the model's ACTUAL verdict probability the per-field story explains:
# held-out R^2 of these frozen weights against P(match), cluster-disjoint split,
# hard negatives, 5-seed mean (`modal_interp.py::faithfulness_eval`).
#
# It is low because the decision is REDUNDANT (see above), not because the weights are
# wrong -- refitting them on the same features buys almost nothing. Do not quote a
# higher number: figures near 0.87 measured on a record-disjoint split (which leaks the
# same entity across the split) or against the internal projection rather than the
# verdict. This is the honest one for the look-alike regime the explainer runs in.
PERSON_IMPORTANCE_FAITHFULNESS_R2 = 0.27

# The same measurement for the sparse signal basis, on CORRUPTION-MATCHED pairs --
# the de-confounded number (5-seed mean). The raw-probe figure is 0.64, but that is
# inflated by the corruption shortcut; 0.33 is what survives.
#
# Note how small the remaining edge is: 0.33 vs 0.26 for the far more legible 6-field
# table. The high-faithfulness mode is retained because the edge is real and holds on
# every seed, but it no longer justifies itself on magnitude alone -- if the two-mode
# split ever costs anything, retire the mode.
PERSON_SIGNAL_FAITHFULNESS_R2 = 0.33

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
class Counterfactual:
    """What the model actually does when one field is removed from both records.

    Unlike :class:`FieldSignal`'s ``importance`` (a corpus-level weight), this is
    measured on THIS pair by re-running the model, so it is a direct claim about
    this decision. ``flips_verdict`` is the one a reviewer cares about.
    """

    field: str
    p_without: float  # P(match) with this field blanked on both records
    delta: float  # p_match - p_without; positive = field pushed TOWARD match
    flips_verdict: bool


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
    counterfactuals: list[Counterfactual] | None = None
    signal_contributions: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def build_counterfactuals(
    p_match: float,
    p_without: dict[str, float],
    *,
    threshold: float = 0.5,
) -> list[Counterfactual]:
    """Turn per-field "P(match) with this field removed" into ranked counterfactuals.

    Both ``p_match`` and the values of ``p_without`` must be P(MATCH), not
    confidence-in-the-verdict — otherwise a NO-MATCH pair's baseline is inverted
    relative to the ablated scores and every delta is wrong. :func:`explain_pair`
    does that conversion for you.

    Pure: the caller supplies the re-scored probabilities (that part needs the
    model). Sorted by absolute impact, so the first entry is the field that moved
    this decision most.

    Measured caveat worth repeating to users: on person data only ~19% of pairs
    have ANY single-field flip, because the model integrates redundant evidence.
    An empty flip list is the common case and is not a failure.
    """
    base_match = p_match >= threshold
    out = [
        Counterfactual(
            field=f,
            p_without=float(p),
            delta=float(p_match) - float(p),
            flips_verdict=(float(p) >= threshold) != base_match,
        )
        for f, p in p_without.items()
    ]
    out.sort(key=lambda c: -abs(c.delta))
    return out


def render_counterfactual_note(cfs: list[Counterfactual]) -> str:
    """One-line reviewer-facing summary of the counterfactual sweep."""
    if not cfs:
        return ""
    flips = [c for c in cfs if c.flips_verdict]
    if flips:
        parts = ", ".join(f"{c.field} (P {c.p_without:.2f})" for c in flips[:3])
        return (f"Counterfactual: removing {parts} would REVERSE this verdict.")
    top = cfs[0]
    return (f"Counterfactual: no single field decides this — removing the most "
            f"influential ({top.field}) moves P(match) by {abs(top.delta):.2f} "
            f"without changing the verdict.")


_TOKEN_RE = re.compile(r"[a-z]+|\d+")


def _tokens(s: str) -> list[str]:
    """Lowercase alphanumeric runs, split at letter/digit boundaries.

    The boundary split is what makes product data work: ``"60GB"`` becomes
    ``["60", "gb"]`` so it can align with a ``"60 GB"`` written the other way,
    and ``"PS3"`` becomes ``["ps", "3"]``.
    """
    return _TOKEN_RE.findall(s.lower())


def token_agreement(sa: str, sb: str) -> float:
    """Order-insensitive token-overlap agreement in [0, 1].

    Uses containment (``|A∩B| / min(|A|, |B|)``) when both sides are multi-token,
    which tolerates one side carrying extra words — the normal case for product
    titles. Single-token sides fall back to the stricter Jaccard so a short value
    can't score 1.0 just by appearing inside a longer one.
    """
    ta, tb = set(_tokens(sa)), set(_tokens(sb))
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    if len(ta) >= 2 and len(tb) >= 2:
        return inter / min(len(ta), len(tb))
    return inter / max(len(ta), len(tb))


def field_agreement(va: Any, vb: Any) -> float | None:
    """Agreement of two field values in [0, 1], or None if missing on a side.

    ``max`` of character-level jaro-winkler and :func:`token_agreement`, so it can
    only ever ADD agreement that a pure string metric misses — never remove it.
    That ordering is deliberate: whole-string jaro-winkler is what works on
    corrupted person fields (a token-sort metric collapses there), while token
    overlap is what works on reordered/verbose product text. ``"Sony 60GB PS3"``
    vs ``"PlayStation 3 60 GB Sony"`` scores ~0.2 on jaro-winkler and 0.8 here.

    This is the single source of truth for agreement: the interp measurement
    harness passes this exact function, so the number the explainer shows and the
    number we report faithfulness against cannot drift apart.
    """
    sa = "" if va is None else str(va).strip()
    sb = "" if vb is None else str(vb).strip()
    if not sa or not sb:
        return None
    if sa == sb:
        return 1.0
    import jellyfish

    jw = float(jellyfish.jaro_winkler_similarity(sa, sb))
    return max(jw, token_agreement(sa, sb))


def _agreement(va: Any, vb: Any) -> float | None:
    """Backwards-compatible alias for :func:`field_agreement`."""
    return field_agreement(va, vb)


# The per-field signal decomposition. Named here, in the SHIPPED module, because
# this is the basis both the product and the interp harness must agree on -- the
# faithfulness numbers are only meaningful if what we measure is what we ship.
FIELD_SIGNAL_NAMES: tuple[str, ...] = (
    "agreement", "exact", "missing", "conflict", "len_ratio", "edit_norm",
)


def field_signal_vector(va: Any, vb: Any) -> dict[str, float]:
    """Decompose one field's two values into the six named signals.

    Richer than the single :func:`field_agreement` scalar, and the difference
    matters: *missing* (no evidence) and *conflicting* (negative evidence) both
    sit near agreement 0 but mean opposite things to a reader and to a fit.

    Returns a plain dict of floats — no numpy — so this module stays
    dependency-light. ``conflict`` uses the same ``_CONFLICT_THRESHOLD`` the
    rationale renderer uses, so "the explanation called this a conflict" and "the
    conflict feature fired" can never disagree.
    """
    sa = "" if va is None else str(va).strip()
    sb = "" if vb is None else str(vb).strip()
    out: dict[str, float] = dict.fromkeys(FIELD_SIGNAL_NAMES, 0.0)
    if not sa or not sb:
        out["missing"] = 1.0
        return out
    agr = field_agreement(sa, sb) or 0.0
    out["agreement"] = float(agr)
    out["exact"] = 1.0 if sa == sb else 0.0
    out["conflict"] = 1.0 if agr <= _CONFLICT_THRESHOLD else 0.0
    out["len_ratio"] = min(len(sa), len(sb)) / max(len(sa), len(sb))
    if sa == sb:
        out["edit_norm"] = 0.0
    else:
        import jellyfish

        out["edit_norm"] = jellyfish.levenshtein_distance(sa, sb) / max(len(sa), len(sb))
    return out


def _importance_for(field: str, weights: dict[str, float] | None) -> tuple[float, bool]:
    """(importance, is_learned) for a field. Explicit ``weights`` win; otherwise the
    bundled person profile; otherwise a neutral default (not learned)."""
    if weights is not None and field in weights:
        return float(weights[field]), True
    if field in PERSON_FIELD_IMPORTANCE:
        return PERSON_FIELD_IMPORTANCE[field], True
    return DEFAULT_FIELD_IMPORTANCE, False


def signal_contributions(
    row_a: dict[str, Any],
    row_b: dict[str, Any],
    columns: list[str],
    *,
    weights: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Signed per-signal contributions under the high-faithfulness basis.

    ``weights`` defaults to :data:`PERSON_SIGNAL_IMPORTANCE`. Each entry is
    ``{signal, field, value, weight, contribution}`` with ``contribution =
    weight * value``, sorted by absolute contribution. Signals with no weight are
    dropped rather than defaulted, since this basis is person-specific.

    Use for an audit trail or for ranking evidence when the *number* matters more
    than the prose. Do NOT roll these up into a per-field importance for display —
    summed per field they invert the ablation ranking (see the note on
    :data:`PERSON_SIGNAL_IMPORTANCE`).
    """
    w = PERSON_SIGNAL_IMPORTANCE if weights is None else weights
    out: list[dict[str, Any]] = []
    for f in columns:
        vec = field_signal_vector(row_a.get(f), row_b.get(f))
        for s, val in vec.items():
            key = f"{f}__{s}"
            if key not in w:
                continue
            out.append({
                "signal": key, "field": f, "value": float(val),
                "weight": float(w[key]), "contribution": float(w[key]) * float(val),
            })
    out.sort(key=lambda e: -abs(e["contribution"]))
    return out


def explain_pair(
    row_a: dict[str, Any],
    row_b: dict[str, Any],
    columns: list[str],
    *,
    match: bool,
    confidence: float,
    weights: dict[str, float] | None = None,
    p_without: dict[str, float] | None = None,
    high_faithfulness: bool = False,
) -> PairExplanation:
    """Explain the model's verdict for one record pair.

    ``match``/``confidence`` are the model's own verdict (this function does not
    re-decide). ``weights`` overrides the learned per-field importance; when None,
    the bundled person profile is applied to matching field names (neutral default
    otherwise). Returns a :class:`PairExplanation` with per-field signals, a
    human-readable rationale, and the faithfulness bound.

    ``high_faithfulness=True`` attaches the 36-signal decomposition and reports the
    measured R^2 of THAT basis (0.53 vs 0.27). The per-field prose is unchanged --
    the richer weights score roughly twice as well but rank fields nonsensically
    (first_name last), so they inform the number and the audit trail, never the
    human story. Person schema only; a no-op on other schemas.

    ``p_without`` optionally supplies per-field "P(match) with this field blanked
    on both records", as produced by re-running the model
    (:meth:`LocalLlamaAdapter.score_and_explain` with ``counterfactuals=True``).
    When given, the explanation carries measured :class:`Counterfactual` entries
    and the rationale states whether any single field would reverse the verdict —
    a direct claim about THIS decision rather than a corpus-level weight. It costs
    one extra forward pass per field, so it belongs on a review queue.
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

    # `confidence` is confidence-in-the-verdict; counterfactuals live on the
    # P(match) axis, so a NO-MATCH baseline has to be flipped before comparing.
    p_match_base = float(confidence) if match else 1.0 - float(confidence)
    cfs = build_counterfactuals(p_match_base, p_without) if p_without else None
    sig = (
        signal_contributions(row_a, row_b, columns)
        if high_faithfulness and any_learned else None
    )
    rationale = _render_rationale(
        match, confidence, supports, conflicts, agrees, any_learned,
        PERSON_SIGNAL_FAITHFULNESS_R2 if sig else PERSON_IMPORTANCE_FAITHFULNESS_R2,
    )
    if cfs:
        # measured on THIS pair, so it leads over the corpus-level caveat
        rationale = f"{rationale} {render_counterfactual_note(cfs)}"
    return PairExplanation(
        match=bool(match), confidence=float(confidence), signals=signals,
        rationale=rationale,
        faithfulness_r2=(
            None if not any_learned
            else PERSON_SIGNAL_FAITHFULNESS_R2 if sig
            else PERSON_IMPORTANCE_FAITHFULNESS_R2
        ),
        learned_weights_applied=any_learned,
        field_story_agrees_with_verdict=agrees,
        counterfactuals=cfs,
        signal_contributions=sig,
    )


def _fmt(s: FieldSignal) -> str:
    a = s.agreement
    lvl = "exact" if a is not None and a >= 0.999 else (f"{a:.2f}" if a is not None else "n/a")
    return f"{s.field} ({s.value_a!r} vs {s.value_b!r}, {lvl})"


def _render_rationale(
    match: bool, confidence: float, supports: list[FieldSignal],
    conflicts: list[FieldSignal], agrees: bool, learned: bool,
    r2: float = PERSON_IMPORTANCE_FAITHFULNESS_R2,
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
        parts.append(f"(Field importance is the model's learned weighting. It accounts "
                     f"for ~{int(r2 * 100)}% of the "
                     f"model's verdict: the model combines evidence across fields, so "
                     f"much of the decision is not attributable to any single one.)")
    return " ".join(parts)
