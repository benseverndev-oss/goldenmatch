"""Parity: the native `pearson_r` + `chi2_contingency_stat` kernels must
reproduce the DETERMINISTIC scipy statistics the `correlation.py` baseline
profiler consumes -- `scipy.stats.pearsonr(a, b)[0]` (the Pearson `r`, clamped
into [-1, 1]) and `scipy.stats.chi2_contingency(m)[0]` (the Pearson chi2
statistic, with the default 2x2-only Yates continuity correction).

These kernels have NO pure-Python fallback: the profiler consumes the scipy
statistic directly, so scipy IS the parity oracle. Both are pure arithmetic
(deterministic), so the harness canonicalises each float to ~9 significant
figures in BOTH lanes (the `_canon_float` mechanism the `numeric_stats`
component already uses) -- float-reduction noise collapses while any real
divergence beyond epsilon still trips the exact-`!=` oracle, and
ACCEPTED_DIVERGENCES stays EMPTY.

Skips cleanly when the native extension isn't built (pure-Python-only env)."""
from __future__ import annotations

import pyarrow as pa
import pytest
from goldencheck.core._native_loader import native_available, native_module
from scipy import stats as _scipy_stats

from tests.core import parity_harness as ph

native_only = pytest.mark.skipif(
    not native_available(), reason="goldencheck native extension not built"
)


# ---------------------------------------------------------------------------
# Drive the two registered W4 correlation components across seeds (native
# kernel vs scipy oracle). ACCEPTED_DIVERGENCES must stay empty.
# ---------------------------------------------------------------------------
_W4_COMPONENTS = [
    c for c in ph.REGISTERED_COMPONENTS if c.name in {"pearson_r", "chi2_contingency"}
]


@native_only
@pytest.mark.parametrize("comp", _W4_COMPONENTS, ids=lambda c: c.name)
@pytest.mark.parametrize("seed", range(8))
def test_correlation_component_parity(comp: ph.Component, seed: int) -> None:
    problems = ph.compare(comp, seed)
    assert problems == [], "\n".join(problems)


@native_only
def test_accepted_divergences_still_empty() -> None:
    assert ph.ACCEPTED_DIVERGENCES == ()


# ---------------------------------------------------------------------------
# Targeted assertions pinning the two load-bearing behaviours directly against
# scipy (belt-and-suspenders over the harness's canon-float compare).
# ---------------------------------------------------------------------------
@native_only
def test_pearson_perfect_correlation_clamps_exactly() -> None:
    xs = [float(i) for i in range(40)]
    pos = [2.0 * v + 1.0 for v in xs]
    neg = [-3.0 * v + 5.0 for v in xs]
    ax = pa.array(xs, type=pa.float64())
    r_pos = native_module().pearson_r(ax, pa.array(pos, type=pa.float64()))
    r_neg = native_module().pearson_r(ax, pa.array(neg, type=pa.float64()))
    # Exactly +/-1.0 thanks to the scipy-matching clamp.
    assert r_pos == 1.0
    assert r_neg == -1.0
    assert r_pos == pytest.approx(_scipy_stats.pearsonr(xs, pos)[0])
    assert r_neg == pytest.approx(_scipy_stats.pearsonr(xs, neg)[0])


@native_only
def test_chi2_2x2_yates_clip_at_zero() -> None:
    # Every |obs-exp| < 0.5 -> Yates clips each residual to 0 -> chi2 == 0,
    # exactly matching scipy (which also returns ~0 here).
    matrix = [[5.0, 5.0], [5.0, 6.0]]
    flat = [v for row in matrix for v in row]
    stat = native_module().chi2_contingency_stat(flat, 2, 2)
    assert stat == 0.0
    assert stat == pytest.approx(_scipy_stats.chi2_contingency(matrix)[0], abs=1e-12)


@native_only
def test_chi2_2x2_yates_matches_scipy() -> None:
    matrix = [[10.0, 20.0], [30.0, 40.0]]
    flat = [v for row in matrix for v in row]
    stat = native_module().chi2_contingency_stat(flat, 2, 2)
    assert stat == pytest.approx(_scipy_stats.chi2_contingency(matrix)[0])


@native_only
def test_chi2_non_2x2_no_correction_matches_scipy() -> None:
    # 2x3 table: scipy applies NO Yates correction (only 2x2 tables do).
    matrix = [[10.0, 20.0, 30.0], [30.0, 20.0, 10.0]]
    flat = [v for row in matrix for v in row]
    stat = native_module().chi2_contingency_stat(flat, 2, 3)
    assert stat == pytest.approx(_scipy_stats.chi2_contingency(matrix)[0])
