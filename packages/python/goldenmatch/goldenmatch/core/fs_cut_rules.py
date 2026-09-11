"""Link-cut rules for the Fellegi-Sunter linear score, as evidence cutoffs in bits.

Spec: docs/superpowers/specs/2026-09-11-fs-cut-rule-routing-design.md.

The linear FS score is ``(W - lo) / (hi - lo)``. ``W`` is a pair's summed match weight;
``lo``/``hi`` are the summed per-field minimum and maximum weights plus the negative-evidence
range. Every rule returns a cutoff on ``W`` in bits, and ``bits_to_normalized`` turns that into
the linear cutoff every scorer already applies, native kernel included. All rules are monotone
in ``W``, so no scorer changes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

#: Every rule a matchkey may pin with ``MatchkeyConfig.link_cut_rule``. The schema's ``Literal``
#: repeats these names (schemas must not import core); tests/test_fs_cut_rules.py keeps them equal.
CUT_RULES: tuple[str, ...] = (
    "midpoint", "prior", "prior_mid", "posterior_099",
    "evidence_3", "evidence_5", "evidence_9", "evidence_12", "otsu",
)

_EVIDENCE_BITS = {"evidence_3": 3.0, "evidence_5": 5.0, "evidence_9": 9.0, "evidence_12": 12.0}


@dataclass(frozen=True)
class Envelope:
    """The summed per-field weight extremes the linear score normalizes against."""

    lo: float
    hi: float

    @property
    def midpoint(self) -> float:
        return (self.lo + self.hi) / 2.0


def weight_envelope(mk: Any, em_result: Any) -> Envelope | None:
    """``lo``/``hi`` built exactly as the scorers build them.

    None when there is nothing to place a cut from: no match weights, or a degenerate range.
    """
    from goldenmatch.core.probabilistic import _fs_ne_weight_range

    match_weights = getattr(em_result, "match_weights", None)
    if not match_weights:
        return None
    lo, hi = _fs_ne_weight_range(em_result, mk)
    for f in mk.fields:
        weights = match_weights.get(f.field)
        if weights:
            lo += min(weights)
            hi += max(weights)
    if hi <= lo:
        return None
    return Envelope(lo=float(lo), hi=float(hi))


def prior_bits(proportion_matched: float) -> float:
    """``log2((1 - lambda) / lambda)``: the evidence at which the posterior crosses 0.5."""
    from goldenmatch.core.probabilistic import prior_weight

    return -prior_weight(proportion_matched)


def rule_bits(rule: str, envelope: Envelope, em_result: Any) -> float | None:
    """The cutoff on ``W`` for ``rule``, or None when this model cannot supply it."""
    if rule == "midpoint":
        return envelope.midpoint
    if rule in _EVIDENCE_BITS:
        return _EVIDENCE_BITS[rule]
    if rule == "otsu":
        return None  # needs EMResult.training_score_histogram (Tasks 5-6)
    if rule not in CUT_RULES:
        raise ValueError(f"unknown link cut rule {rule!r}; expected one of {CUT_RULES}")
    lam = getattr(em_result, "proportion_matched", None)
    if lam is None:
        return None
    prior = prior_bits(lam)
    if rule == "prior":
        return max(prior, 0.0)
    if rule == "prior_mid":
        return max(prior, envelope.midpoint, 0.0)
    return math.log2(99.0) + prior  # posterior_099: where sigmoid(prior_weight + W) = 0.99


def bits_to_normalized(bits: float, envelope: Envelope) -> float:
    """The linear-score cutoff equivalent to ``W >= bits``, clamped to [0, 1]."""
    return min(max((bits - envelope.lo) / (envelope.hi - envelope.lo), 0.0), 1.0)
