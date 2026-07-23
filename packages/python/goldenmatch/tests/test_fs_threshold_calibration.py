"""FS unsupervised link-threshold calibration (GOLDENMATCH_FS_CALIBRATE_THRESHOLD).

Default-OFF lever: instead of the fixed 0.50 link cutoff, EM picks the threshold
from the training-pair normalized-score distribution via Otsu's method. The fixed
cutoff over-merges when non-match (namesake) pairs pile up just below it
(historical_50k: F1 0.75 at 0.50 vs 0.80 at 0.55). Clean datasets have flat
threshold curves so an adaptive cutoff leaves them ~unchanged.

OFF is byte-identical (calibrated_link_threshold stays None; _fs_link_threshold
falls back to compute_thresholds' fixed 0.50).
"""
from __future__ import annotations

import numpy as np
import pytest

from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.probabilistic import (
    EMResult,
    _calibrate_link_threshold,
    _fs_calibrate_threshold_enabled,
    _fs_link_threshold,
    _otsu_threshold,
)

FLAG = "GOLDENMATCH_FS_CALIBRATE_THRESHOLD"


@pytest.fixture(autouse=True)
def _clear(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)


def _mk():
    return MatchkeyConfig(name="fs", type="probabilistic", threshold=0.8, fields=[
        MatchkeyField(field="a", scorer="jaro_winkler", levels=2),
        MatchkeyField(field="b", scorer="jaro_winkler", levels=2),
    ])


def test_flag_default_on():
    assert _fs_calibrate_threshold_enabled() is True


@pytest.mark.parametrize("falsy", ["0", "false", "off", "no", "disabled"])
def test_flag_falsy_disables(monkeypatch, falsy):
    monkeypatch.setenv(FLAG, falsy)
    assert _fs_calibrate_threshold_enabled() is False


@pytest.mark.parametrize("truthy", ["1", "true", "on", "yes", "enabled"])
def test_flag_truthy(monkeypatch, truthy):
    monkeypatch.setenv(FLAG, truthy)
    assert _fs_calibrate_threshold_enabled() is True


def test_otsu_finds_valley():
    rng = np.random.default_rng(0)
    scores = np.concatenate([rng.normal(0.45, 0.05, 2000),
                             rng.normal(0.75, 0.05, 500)])
    t = _otsu_threshold(np.clip(scores, 0, 1))
    assert 0.50 <= t <= 0.66  # in the valley between the two modes


def test_calibrate_bimodal_raises_cutoff():
    # comp_matrix: a pile of non-matches (low levels) + matches (high levels).
    comp = np.array([[0, 0]] * 300 + [[1, 1]] * 80 + [[1, 0]] * 40, dtype=np.int64)
    mw = {"a": [-1.0, 2.0], "b": [-1.0, 2.0]}
    t = _calibrate_link_threshold(comp, _mk(), mw, p_match=0.15)
    assert t is not None and 0.40 <= t <= 0.90


def test_calibrate_too_few_pairs_returns_none():
    comp = np.array([[1, 1]] * 10, dtype=np.int64)
    mw = {"a": [-1.0, 2.0], "b": [-1.0, 2.0]}
    assert _calibrate_link_threshold(comp, _mk(), mw, p_match=0.15) is None


def test_link_threshold_off_uses_fixed_default():
    em = EMResult(m_probs={}, u_probs={}, match_weights={"a": [-1.0, 2.0], "b": [-1.0, 2.0]},
                  converged=True, iterations=1, proportion_matched=0.13)
    assert _fs_link_threshold(_mk(), em, False) == 0.50  # fixed default


def test_link_threshold_uses_calibrated_when_set():
    em = EMResult(m_probs={}, u_probs={}, match_weights={"a": [-1.0, 2.0], "b": [-1.0, 2.0]},
                  converged=True, iterations=1, proportion_matched=0.13,
                  calibrated_link_threshold=0.62)
    assert _fs_link_threshold(_mk(), em, False) == 0.62


def test_configured_link_threshold_wins_over_calibrated():
    mk = _mk()
    mk.link_threshold = 0.7
    em = EMResult(m_probs={}, u_probs={}, match_weights={"a": [-1.0, 2.0], "b": [-1.0, 2.0]},
                  converged=True, iterations=1, proportion_matched=0.13,
                  calibrated_link_threshold=0.62)
    assert _fs_link_threshold(mk, em, False) == 0.7


def test_roundtrip_carries_calibrated_threshold():
    em = EMResult(m_probs={}, u_probs={}, match_weights={}, converged=True,
                  iterations=1, proportion_matched=0.1, calibrated_link_threshold=0.58)
    assert EMResult.from_dict(em.to_dict()).calibrated_link_threshold == 0.58
    off = EMResult(m_probs={}, u_probs={}, match_weights={}, converged=True,
                   iterations=1, proportion_matched=0.1)
    assert "calibrated_link_threshold" not in off.to_dict()
