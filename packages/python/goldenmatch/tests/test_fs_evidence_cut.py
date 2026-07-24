"""Prior-aware posterior link cut (GOLDENMATCH_FS_EVIDENCE_CUT).

The posterior score is sigma(prior_w + W). A FIXED posterior cut (0.99) equals
W >= 6.63 - prior_w, so blocking's lambda inflation LOWERS the evidence bar and
weak pairs clear it. Setting an evidence cut c (bits) makes the threshold
sigma(c + prior_w), i.e. exactly W >= c -- a prior- AND endpoint-invariant bar,
so a weight lever can no longer silently relocate the operating point.

Off (unset) keeps the fixed 0.99 posterior default and does not force posterior
scoring, so the default path is byte-identical.
"""
from __future__ import annotations

import math

import pytest
from goldenmatch.core.probabilistic import (
    EMResult,
    _fs_calibration_mode,
    _fs_evidence_cut,
    compute_thresholds,
    posterior_from_weight,
    prior_weight,
)

CUT = "GOLDENMATCH_FS_EVIDENCE_CUT"
CAL = "GOLDENMATCH_FS_CALIBRATED"


@pytest.fixture(autouse=True)
def _clear(monkeypatch):
    monkeypatch.delenv(CUT, raising=False)
    monkeypatch.delenv(CAL, raising=False)


def _em(prop=0.2):
    return EMResult(m_probs={}, u_probs={}, match_weights={}, converged=True,
                    iterations=1, proportion_matched=prop)


# ── flag parsing / default-off ────────────────────────────────────────────────
def test_default_off_is_linear_and_099():
    assert _fs_evidence_cut() is None
    assert _fs_calibration_mode() == "linear"
    assert compute_thresholds(_em(), calibrated=True) == (0.99, 0.50)


def test_cut_forces_posterior(monkeypatch):
    monkeypatch.setenv(CUT, "4")
    assert _fs_evidence_cut() == 4.0
    assert _fs_calibration_mode() == "posterior"


def test_garbage_value_is_off(monkeypatch):
    monkeypatch.setenv(CUT, "banana")
    assert _fs_evidence_cut() is None
    assert _fs_calibration_mode() == "linear"


def test_explicit_linear_overrides_cut(monkeypatch):
    monkeypatch.setenv(CUT, "4")
    monkeypatch.setenv(CAL, "linear")
    assert _fs_calibration_mode() == "linear"


def test_negative_cut_is_honored(monkeypatch):
    # A negative bar is legal (accepts net-negative evidence); don't silently drop it.
    monkeypatch.setenv(CUT, "-2")
    assert _fs_evidence_cut() == -2.0


# ── the core invariance property ──────────────────────────────────────────────
@pytest.mark.parametrize("prop", [0.002, 0.05, 0.2, 0.5, 0.92])
def test_cut_threshold_is_exactly_W_ge_c(monkeypatch, prop):
    monkeypatch.setenv(CUT, "4")
    link, review = compute_thresholds(_em(prop), calibrated=True)
    prior_w = prior_weight(prop)
    assert posterior_from_weight(4.0, prior_w) == pytest.approx(link)
    assert posterior_from_weight(3.99, prior_w) < link
    assert posterior_from_weight(4.01, prior_w) >= link
    # review band sits 3 bits of evidence below the link cut
    assert posterior_from_weight(1.0, prior_w) == pytest.approx(review)


@pytest.mark.parametrize("prop", [0.01, 0.4, 0.92])
def test_prior_invariance_of_the_bar(monkeypatch, prop):
    """Same c under different priors => the implied W-bar is identical.

    This is the whole point: lambda inflation from blocking cannot move the bar.
    """
    monkeypatch.setenv(CUT, "5")
    link, _ = compute_thresholds(_em(prop), calibrated=True)
    w_at_link = math.log2(link / (1 - link)) - prior_weight(prop)
    assert w_at_link == pytest.approx(5.0, abs=1e-6)


def test_higher_cut_is_stricter(monkeypatch):
    monkeypatch.setenv(CUT, "2")
    low, _ = compute_thresholds(_em(0.3), calibrated=True)
    monkeypatch.setenv(CUT, "6")
    high, _ = compute_thresholds(_em(0.3), calibrated=True)
    assert high > low


def test_review_never_exceeds_link(monkeypatch):
    monkeypatch.setenv(CUT, "0.5")
    link, review = compute_thresholds(_em(0.002), calibrated=True)
    assert review <= link


def test_linear_path_untouched_by_cut(monkeypatch):
    # calibrated=False must ignore the evidence cut entirely.
    monkeypatch.setenv(CUT, "6")
    assert compute_thresholds(_em(0.2), calibrated=False) == (0.50, 0.35)
