"""``GOLDENMATCH_FS_LINEAR_CUT``: place the linear FS link cutoff by evidence bits.

The linear score is ``(W - lo) / (hi - lo)`` over the summed per-field weight extremes, so
the fixed 0.50 cutoff links at the midpoint of those extremes. ``prior`` links at
``W >= max(log2((1 - lambda) / lambda), 0)``; ``prior_mid`` takes the larger of that and
the midpoint. The rule returns the equivalent normalized cutoff.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.probabilistic import (
    _fs_linear_rule_link_threshold,
    _fs_link_threshold,
    resolve_thresholds,
)


def _mk() -> MatchkeyConfig:
    return MatchkeyConfig(
        name="fs",
        type="probabilistic",
        fields=[
            MatchkeyField(field="name", scorer="jaro_winkler", levels=3, partial_threshold=0.8),
            MatchkeyField(field="zip", scorer="exact", levels=2),
        ],
    )


def _em(lam: float):
    # lo = -6 + -4 = -10, hi = 10 + 4 = 14; midpoint = 2 bits.
    return SimpleNamespace(
        match_weights={"name": [-6.0, 1.0, 10.0], "zip": [-4.0, 4.0]},
        proportion_matched=lam,
        calibrated_link_threshold=None,
    )


def _norm(bits: float) -> float:
    return (bits - -10.0) / (14.0 - -10.0)


def test_prior_mid_is_the_default(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_FS_LINEAR_CUT", raising=False)
    got = _fs_linear_rule_link_threshold(_mk(), _em(0.01), calibrated=False)
    assert math.isclose(got, _norm(math.log2(99.0)), rel_tol=1e-9)


def test_off_restores_the_midpoint_cut(monkeypatch):
    for off in ("off", "0", "midpoint"):
        monkeypatch.setenv("GOLDENMATCH_FS_LINEAR_CUT", off)
        assert _fs_linear_rule_link_threshold(_mk(), _em(0.01), calibrated=False) is None


def test_unknown_value_keeps_the_default(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_LINEAR_CUT", "median")
    got = _fs_linear_rule_link_threshold(_mk(), _em(0.01), calibrated=False)
    assert math.isclose(got, _norm(math.log2(99.0)), rel_tol=1e-9)


def test_posterior_mode_is_untouched(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_LINEAR_CUT", "prior")
    assert _fs_linear_rule_link_threshold(_mk(), _em(0.01), calibrated=True) is None


def test_prior_cut_lands_on_the_prior_bits(monkeypatch):
    """KNOWN-POSITIVE: lambda 0.01 -> log2(99) = 6.63 bits, far above the 2-bit midpoint
    the fixed 0.50 cutoff links at."""
    monkeypatch.setenv("GOLDENMATCH_FS_LINEAR_CUT", "prior")
    got = _fs_linear_rule_link_threshold(_mk(), _em(0.01), calibrated=False)
    assert math.isclose(got, _norm(math.log2(99.0)), rel_tol=1e-9)
    assert got > 0.5


def test_prior_cut_never_goes_below_zero_evidence(monkeypatch):
    """A high match rate makes the prior negative; the cut stays at W >= 0."""
    monkeypatch.setenv("GOLDENMATCH_FS_LINEAR_CUT", "prior")
    got = _fs_linear_rule_link_threshold(_mk(), _em(0.9), calibrated=False)
    assert math.isclose(got, _norm(0.0), rel_tol=1e-9)


def test_prior_mid_takes_the_larger_of_prior_and_midpoint(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_LINEAR_CUT", "prior_mid")
    low_prior = _fs_linear_rule_link_threshold(_mk(), _em(0.4), calibrated=False)  # 0.58 bits
    assert math.isclose(low_prior, _norm(2.0), rel_tol=1e-9)
    high_prior = _fs_linear_rule_link_threshold(_mk(), _em(0.01), calibrated=False)
    assert math.isclose(high_prior, _norm(math.log2(99.0)), rel_tol=1e-9)


def test_both_resolvers_agree(monkeypatch):
    """resolve_thresholds (pipeline split) and _fs_link_threshold (scorers) must not drift."""
    monkeypatch.setenv("GOLDENMATCH_FS_LINEAR_CUT", "prior")
    monkeypatch.delenv("GOLDENMATCH_FS_CALIBRATED", raising=False)
    monkeypatch.delenv("GOLDENMATCH_FS_EVIDENCE_CUT", raising=False)
    mk, em = _mk(), _em(0.01)
    link, review = resolve_thresholds(mk, em)
    assert math.isclose(link, _fs_link_threshold(mk, em, calibrated=False), rel_tol=1e-12)
    assert review <= link


def test_explicit_link_threshold_still_wins(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_LINEAR_CUT", "prior")
    mk = _mk().model_copy(update={"link_threshold": 0.42})
    assert _fs_link_threshold(mk, _em(0.01), calibrated=False) == 0.42
