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
from collections.abc import Callable, Sequence
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
        return otsu_bits(em_result, envelope)
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


def otsu_split(em_result: Any) -> float | None:
    """The Otsu calibrator's split ``t``, exactly as ``_calibrate_link_threshold``
    (GOLDENMATCH_FS_CALIBRATE_THRESHOLD) computes it: clamp and 4-dp rounding included.

    ``t`` is on the normalized [0, 1] scale directly -- it is what the calibrator applies as
    the linear cutoff, with no envelope involved. Callers that need it in bits should place it
    on an envelope with ``otsu_bits``, but the normalized cut for the ``otsu`` rule must be
    this value VERBATIM (not a bits round trip through ``bits_to_normalized``, which can be a
    ulp off -- see ``_fs_resolved_cut``).

    None when the model carries no training histogram, or too little of one.
    """
    import numpy as np

    from goldenmatch.core.probabilistic import (
        _CALIBRATE_MAX,
        _CALIBRATE_MIN,
        _otsu_split_from_counts,
    )

    hist = getattr(em_result, "training_score_histogram", None)
    if not hist:
        return None
    if sum(hist["counts"]) <= 50:
        return None  # same minimum sample as _calibrate_link_threshold
    t = _otsu_split_from_counts(hist["counts"])
    if t is None:
        return None
    return round(float(np.clip(t, _CALIBRATE_MIN, _CALIBRATE_MAX)), 4)


def otsu_bits(em_result: Any, envelope: Envelope) -> float | None:
    """The Otsu calibrator's cut, in bits: ``otsu_split(em_result)`` placed on ``envelope``.

    None when the model carries no training histogram, or too little of one.
    """
    t = otsu_split(em_result)
    return None if t is None else envelope.lo + t * (envelope.hi - envelope.lo)


@dataclass(frozen=True)
class CutDiagnostics:
    """What a link-cut router may read after EM. No labels, no scoring pass."""

    proportion_matched: float
    lo: float
    hi: float
    midpoint_bits: float
    prior_bits: float
    n_fields: int
    has_negative_evidence: bool
    field_weight_spans: dict[str, float]
    #: Share of the training sample each rule would admit; None without a training histogram.
    #: Bin resolution only (1/100 of the regular-field envelope), and the training sample is a
    #: biased stand-in for the scored candidates -- see the spec's risks.
    admitted_fraction: dict[str, float] | None


def cut_diagnostics(mk: Any, em_result: Any) -> CutDiagnostics | None:
    """Router inputs for ``mk`` from its trained model, or None without usable weights."""
    envelope = weight_envelope(mk, em_result)
    lam = getattr(em_result, "proportion_matched", None)
    if envelope is None or lam is None:
        return None
    match_weights = em_result.match_weights
    spans = {
        f.field: float(max(w) - min(w))
        for f in mk.fields
        if (w := match_weights.get(f.field))
    }
    admitted = None
    hist = getattr(em_result, "training_score_histogram", None)
    if hist and sum(hist["counts"]) > 0 and hist["hi"] > hist["lo"]:
        counts = hist["counts"]
        total = float(sum(counts))
        admitted = {}
        for rule in CUT_RULES:
            bits = rule_bits(rule, envelope, em_result)
            if bits is None:
                continue
            # `hist["lo"]`/`hist["hi"]` are the REGULAR-field-only scale the training
            # histogram was built on; `bits` (and the linear score / `otsu`'s cut) are
            # placed on the full `envelope`, which also folds in negative evidence. With
            # NE fields the two scales diverge, so this admitted fraction is approximate.
            t = min(max((bits - hist["lo"]) / (hist["hi"] - hist["lo"]), 0.0), 1.0)
            start = min(math.ceil(t * len(counts)), len(counts))
            admitted[rule] = sum(counts[start:]) / total
    return CutDiagnostics(
        proportion_matched=float(lam),
        lo=envelope.lo,
        hi=envelope.hi,
        midpoint_bits=envelope.midpoint,
        prior_bits=prior_bits(lam),
        n_fields=len(mk.fields),
        has_negative_evidence=bool(getattr(mk, "negative_evidence", None)),
        field_weight_spans=spans,
        admitted_fraction=admitted,
    )


@dataclass(frozen=True)
class CutRow:
    """One routing-table row: when ``when(diagnostics)`` holds, ``rule`` places the cut.

    ``when`` reads only :class:`CutDiagnostics` -- no labels, no scoring pass. ``reason`` is the
    human-readable mechanism, reported in ``cut_reason``.
    """

    name: str
    rule: str
    reason: str
    when: Callable[[CutDiagnostics], bool]

    def __post_init__(self) -> None:
        if self.rule not in CUT_RULES:
            raise ValueError(f"unknown link cut rule {self.rule!r}; expected one of {CUT_RULES}")


#: The shipped routing table, read by ``choose_cut_rule`` when ``GOLDENMATCH_FS_CUT_ROUTER`` is
#: on. The first match wins. No match means the default rule (``GOLDENMATCH_FS_LINEAR_CUT``,
#: default ``prior_mid``), which stands in for the spec's "last row is the shipped default".
#: A row lands here only after passing the design AND held-out gate
#: (``scripts/autoconfig_quality/cut_gate.py``); unshipped candidates live in
#: ``scripts/autoconfig_quality/cut_rows.py``.
ROWS: tuple[CutRow, ...] = (
    # Gated on matrix run 34649964888: design PASS (dblp_acm +0.0918, synth_biblio_d02 +0.0000), held-out PASS.
    CutRow(
        name="sparse_prior_binds",
        rule="posterior_099",
        reason="match rate under 0.004, the prior cutoff above the midpoint, at most 3 fields",
        when=lambda d: (
            d.proportion_matched < 0.004 and d.prior_bits > d.midpoint_bits and d.n_fields <= 3
        ),
    ),
    # Gated on matrix run 34649964888: design PASS (febrl3 +0.0038, febrl4 +0.0002, musicbrainz_20k +0.2673), held-out PASS.
    CutRow(
        name="midpoint_binds_wide",
        rule="evidence_5",
        reason="match rate under 0.1, the midpoint cutoff above the prior, at least 5 fields",
        when=lambda d: (
            d.proportion_matched < 0.1 and d.midpoint_bits > d.prior_bits and d.n_fields >= 5
        ),
    ),
)


def choose_cut_rule(
    diagnostics: CutDiagnostics | None, rows: Sequence[CutRow] | None = None
) -> tuple[str, str] | None:
    """``(rule, reason)`` from the first row of ``rows`` (default: the shipped ``ROWS``) whose
    condition holds, or None for the default rule.

    Also None without diagnostics or with a degenerate match rate (λ outside (0, 1)): every
    failure degrades to the default."""
    table = ROWS if rows is None else rows
    if diagnostics is None or not 0.0 < diagnostics.proportion_matched < 1.0:
        return None
    for row in table:
        if row.when(diagnostics):
            return row.rule, f"routed by {row.name}: {row.reason}"
    return None
