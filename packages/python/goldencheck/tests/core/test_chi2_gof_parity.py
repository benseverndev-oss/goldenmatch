"""Parity: the native `chi2_gof` kernel must reproduce
`scipy.stats.chisquare(f_obs, f_exp)` -- BOTH the chi-squared statistic and the
upper-tail p-value -- the two values the `statistical.py` Benford profiler's
`_compute_benford` consumes.

This kernel has NO pure-Python fallback: the profiler consumes the scipy result
directly, so scipy IS the parity oracle. The statistic is pure arithmetic
(deterministic); the p-value is the ONE owned epsilon divergence class (statrs
`gamma_ur` vs scipy's `chdtrc`/`gammaincc`). The harness canonicalises each
float to ~9 significant figures in BOTH lanes (the `_canon_float` mechanism the
`numeric_stats`/`pearson_r` components already use) -- last-digit noise collapses
while any real divergence beyond epsilon still trips the exact-`!=` oracle, and
ACCEPTED_DIVERGENCES stays EMPTY.

Skips cleanly when the native extension isn't built (pure-Python-only env)."""
from __future__ import annotations

import pytest
from goldencheck.core._native_loader import native_available, native_module
from scipy import stats as _scipy_stats

from tests.core import parity_harness as ph

native_only = pytest.mark.skipif(
    not native_available(), reason="goldencheck native extension not built"
)


# ---------------------------------------------------------------------------
# Drive the registered chi2_gof component across seeds (native kernel vs scipy
# oracle). ACCEPTED_DIVERGENCES must stay empty.
# ---------------------------------------------------------------------------
_W4_COMPONENTS = [c for c in ph.REGISTERED_COMPONENTS if c.name == "chi2_gof"]


@native_only
@pytest.mark.parametrize("comp", _W4_COMPONENTS, ids=lambda c: c.name)
@pytest.mark.parametrize("seed", range(8))
def test_chi2_gof_component_parity(comp: ph.Component, seed: int) -> None:
    problems = ph.compare(comp, seed)
    assert problems == [], "\n".join(problems)


@native_only
def test_accepted_divergences_still_empty() -> None:
    assert ph.ACCEPTED_DIVERGENCES == ()


# ---------------------------------------------------------------------------
# Targeted assertions pinning the load-bearing behaviours directly against scipy
# (belt-and-suspenders over the harness's canon-float compare).
# ---------------------------------------------------------------------------
@native_only
def test_perfect_fit_is_chi2_zero_p_one() -> None:
    obs = [25.0, 25.0, 25.0, 25.0]
    chi2, pvalue = native_module().chi2_gof(obs, obs)
    assert chi2 == 0.0
    assert pvalue == 1.0
    s_chi2, s_p = _scipy_stats.chisquare(f_obs=obs, f_exp=obs)
    assert chi2 == pytest.approx(s_chi2, abs=1e-12)
    assert pvalue == pytest.approx(s_p)


@native_only
def test_benford_shaped_matches_scipy() -> None:
    obs = [301.0, 176.0, 125.0, 97.0, 79.0, 67.0, 58.0, 51.0, 46.0]
    exp = [301.03, 176.09, 124.94, 96.91, 79.18, 66.95, 57.99, 51.15, 45.76]
    chi2, pvalue = native_module().chi2_gof(obs, exp)
    s_chi2, s_p = _scipy_stats.chisquare(f_obs=obs, f_exp=exp)
    assert chi2 == pytest.approx(s_chi2)
    assert pvalue == pytest.approx(s_p)


@native_only
def test_strong_skew_small_p_tail_accurate() -> None:
    # A large chi2 -> tiny p, the case where `1 - cdf` would cancel. gamma_ur
    # stays tail-accurate and matches scipy's chdtrc within epsilon.
    obs = [40.0, 10.0, 5.0, 5.0]
    exp = [15.0, 15.0, 15.0, 15.0]
    chi2, pvalue = native_module().chi2_gof(obs, exp)
    s_chi2, s_p = _scipy_stats.chisquare(f_obs=obs, f_exp=exp)
    assert chi2 == pytest.approx(s_chi2)
    assert pvalue == pytest.approx(s_p, rel=1e-9)
    assert pvalue < 1e-9  # genuinely in the small-p tail
