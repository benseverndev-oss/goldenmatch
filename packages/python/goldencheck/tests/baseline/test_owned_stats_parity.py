"""Byte-parity gate for the owned baseline stats kernels (``_owned_stats``).

Asserts the pure-Python ``pearson_r`` / ``chi2_contingency_stat`` / ``chi2_gof``
(and the underlying regularized upper incomplete gamma) reproduce
``scipy.stats.pearsonr`` / ``chi2_contingency`` / ``chisquare`` (and
``scipy.special.gammaincc``) to float epsilon. These back the W4 Flip that made
``correlation.py`` + the Benford profiler scipy-free; scipy is a test-only oracle.
"""
from __future__ import annotations

import numpy as np
import pytest
from goldencheck.baseline._owned_stats import (
    chi2_contingency_stat,
    chi2_gof,
    gamma_ur,
    pearson_r,
)

scipy_stats = pytest.importorskip("scipy.stats")
scipy_special = pytest.importorskip("scipy.special")


@pytest.mark.parametrize("seed", range(15))
def test_gamma_ur_matches_scipy_gammaincc(seed: int) -> None:
    rng = np.random.default_rng(seed)
    for _ in range(200):
        a = float(rng.uniform(0.5, 20.0))
        x = float(rng.uniform(0.0, 60.0))
        assert gamma_ur(a, x) == pytest.approx(float(scipy_special.gammaincc(a, x)), abs=1e-12)


@pytest.mark.parametrize("seed", range(15))
def test_pearson_r_matches_scipy(seed: int) -> None:
    rng = np.random.default_rng(seed)
    n = int(rng.integers(3, 300))
    x = rng.normal(0, 1, n)
    y = 0.4 * x + rng.normal(0, 1, n)
    ref = float(scipy_stats.pearsonr(x, y)[0])
    assert pearson_r(x.tolist(), y.tolist()) == pytest.approx(ref, abs=1e-12)


def test_pearson_r_clamps_perfect_correlation() -> None:
    x = [1.0, 2.0, 3.0, 4.0]
    assert pearson_r(x, [2.0, 4.0, 6.0, 8.0]) == 1.0
    assert pearson_r(x, [8.0, 6.0, 4.0, 2.0]) == -1.0
    assert np.isnan(pearson_r([], []))


@pytest.mark.parametrize("seed", range(20))
def test_chi2_contingency_stat_matches_scipy(seed: int) -> None:
    rng = np.random.default_rng(seed)
    r = int(rng.integers(2, 5))
    c = int(rng.integers(2, 5))
    m = rng.integers(1, 50, (r, c)).astype(float)  # >=1 so no zero expecteds
    ref = float(scipy_stats.chi2_contingency(m)[0])  # default correction=True
    got = chi2_contingency_stat(m.flatten().tolist(), r, c)
    assert got == pytest.approx(ref, rel=1e-10, abs=1e-12)


@pytest.mark.parametrize("seed", range(20))
def test_chi2_gof_matches_scipy(seed: int) -> None:
    rng = np.random.default_rng(seed)
    k = int(rng.integers(2, 12))
    total = int(rng.integers(50, 500))
    p = rng.dirichlet(np.ones(k))
    expected = (p * total).tolist()
    observed = rng.multinomial(total, p).astype(float).tolist()
    ref_stat, ref_p = scipy_stats.chisquare(f_obs=observed, f_exp=expected)
    stat, pval = chi2_gof(observed, expected)
    assert stat == pytest.approx(float(ref_stat), rel=1e-10, abs=1e-12)
    assert pval == pytest.approx(float(ref_p), abs=1e-12)


def test_chi2_gof_edge_cases() -> None:
    assert chi2_gof([10.0, 10.0], [10.0, 10.0]) == (0.0, 1.0)  # perfect fit
    stat, pval = chi2_gof([5.0], [10.0])  # df=0
    assert stat == pytest.approx(2.5)
    assert np.isnan(pval)
    s, p = chi2_gof([], [])
    assert np.isnan(s) and np.isnan(p)
