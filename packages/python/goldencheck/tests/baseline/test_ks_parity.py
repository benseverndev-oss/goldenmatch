"""Parity gate for the owned one-sample Kolmogorov-Smirnov test (``baseline._ks``).

Asserts ``kstwo_sf`` reproduces ``scipy.stats.kstwo.sf`` and ``kstest`` reproduces
``scipy.stats.kstest`` (statistic + p-value) across the four fitted distributions
goldencheck uses. The KS statistic is byte-identical; the p-value matches to ~1e-6
in the asymptotic tail (it only gates threshold decisions here, and the emitted
value is ``round(p, 6)``) -- so this test ALSO asserts zero threshold-decision
disagreement at the 0.01 / 0.05 gates. scipy is a test-only oracle.
"""
from __future__ import annotations

import numpy as np
import pytest
from goldencheck.baseline._ks import kstest, kstwo_sf

scipy_stats = pytest.importorskip("scipy.stats")


@pytest.mark.parametrize("n", [10, 20, 50, 100, 500, 1000, 5000])
def test_kstwo_sf_matches_scipy(n: int) -> None:
    rng = np.random.default_rng(n)
    for _ in range(40):
        d = float(rng.uniform(0.005, 0.6))
        assert kstwo_sf(d, n) == pytest.approx(float(scipy_stats.kstwo.sf(d, n)), abs=1e-5)


def test_kstwo_sf_small_n_is_exact() -> None:
    rng = np.random.default_rng(0)
    for n in (5, 10, 15, 20):
        for _ in range(20):
            d = float(rng.uniform(0.05, 0.6))
            assert kstwo_sf(d, n) == pytest.approx(float(scipy_stats.kstwo.sf(d, n)), abs=1e-12)


_DISTS = ["norm", "expon", "uniform", "lognorm"]


@pytest.mark.parametrize("seed", range(30))
def test_kstest_matches_scipy_and_decisions(seed: int) -> None:
    rng = np.random.default_rng(seed)
    kind = _DISTS[seed % len(_DISTS)]
    n = int(rng.integers(30, 2000))
    if kind == "norm":
        x = rng.normal(3.0, 2.0, n)
        params = (3.0, 2.0)
    elif kind == "expon":
        x = rng.exponential(2.0, n) + 1.0
        params = (1.0, 2.0)
    elif kind == "uniform":
        x = rng.uniform(5.0, 10.0, n)
        params = (5.0, 5.0)
    else:  # lognorm
        x = scipy_stats.lognorm.rvs(0.7, loc=0.0, scale=2.0, size=n, random_state=rng)
        params = (0.7, 0.0, 2.0)

    d_s, p_s = scipy_stats.kstest(x, kind, args=params)
    d_o, p_o = kstest(x.tolist(), kind, params)

    assert d_o == pytest.approx(float(d_s), abs=1e-12)  # statistic is exact
    assert p_o == pytest.approx(float(p_s), abs=1e-5)
    # The gate decisions (0.01 / 0.05) must be identical.
    for thr in (0.01, 0.05):
        assert (p_o < thr) == (p_s < thr)


def test_kstest_empty_and_unknown() -> None:
    d, p = kstest([], "norm", (0.0, 1.0))
    assert np.isnan(d) and np.isnan(p)
    with pytest.raises(KeyError):
        kstest([1.0, 2.0, 3.0], "weibull", (1.0,))
