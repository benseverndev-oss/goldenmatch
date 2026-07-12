"""W4 shadow parity: the two baseline-stat profilers (``correlation.py`` and
``statistical.py``) shadow-compute their fused native kernels alongside the
authoritative scipy compute. This test proves the shadow values MATCH the scipy
values the profiler consumes -- i.e. the kernels are Flip-ready -- by running
each fixture's kernel on the SAME inputs the profiler feeds scipy and comparing
to scipy directly.

The profilers themselves keep emitting the scipy findings unchanged; the
existing (unedited) ``correlation``/``statistical`` baseline tests guard that.
Here we only assert kernel == scipy on the shadow corpus, and exercise the
profiler functions to prove the shadow blocks never raise out.

Skips cleanly when the relevant kernel isn't gated on / the extension isn't
built.
"""
from __future__ import annotations

import math

import numpy as np
import polars as pl
import pyarrow as pa
import pytest
from goldencheck.baseline import correlation as _corr
from goldencheck.baseline import statistical as _stat
from goldencheck.core._native_loader import native_enabled, native_module
from scipy import stats as _scipy_stats

_ABS_EPS = 1e-9


# ---------------------------------------------------------------------------
# (a) correlation.py _pearson_entry -> pearson_r
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not native_enabled("pearson_r"), reason="pearson_r kernel not gated on")
def test_pearson_shadow_matches_scipy() -> None:
    rng = np.random.default_rng(42)
    x = rng.normal(size=40)
    y = 2.0 * x + rng.normal(scale=0.1, size=40)
    df = pl.DataFrame({"x": x, "y": y})

    # Exercise the profiler (its shadow block must not raise out).
    _corr._pearson_entry(df, "x", "y")

    # Replicate exactly the inputs the profiler feeds scipy.
    sub = df.select(["x", "y"]).drop_nulls()
    a_vals = sub["x"].to_numpy()
    b_vals = sub["y"].to_numpy()

    r_native = native_module().pearson_r(pa.array(a_vals), pa.array(b_vals))
    r_scipy = _scipy_stats.pearsonr(a_vals, b_vals)[0]
    assert r_native == pytest.approx(r_scipy, abs=_ABS_EPS)


# ---------------------------------------------------------------------------
# (b) correlation.py _cramers_v -> chi2_contingency_stat
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    not native_enabled("chi2_contingency"), reason="chi2_contingency kernel not gated on"
)
def test_chi2_contingency_shadow_matches_scipy() -> None:
    # Two correlated categorical columns forming a non-trivial 3x3 contingency
    # table (b mostly follows a, flips every 7th row).
    labels_a: list[str] = []
    labels_b: list[str] = []
    for i in range(40):
        a = "abc"[i % 3]
        idx = "abc".index(a)
        b = "xyz"[idx] if i % 7 else "xyz"[(idx + 1) % 3]
        labels_a.append(a)
        labels_b.append(b)
    df = pl.DataFrame({"a": labels_a, "b": labels_b})

    # Exercise the profiler (its shadow block must not raise out).
    _corr._cramers_v(df, "a", "b")

    # Replicate exactly the contingency matrix the profiler feeds scipy.
    sub = df.select(["a", "b"]).drop_nulls()
    contingency = (
        sub.group_by(["a", "b"])
        .agg(pl.len().alias("_cnt"))
        .pivot(on="b", index="a", values="_cnt")
        .fill_null(0)
    )
    value_cols = [c for c in contingency.columns if c != "a"]
    matrix = contingency.select(value_cols).to_numpy()
    assert matrix.shape[0] >= 2 and matrix.shape[1] >= 2

    stat_native = native_module().chi2_contingency_stat(
        matrix.flatten().tolist(), matrix.shape[0], matrix.shape[1]
    )
    stat_scipy = _scipy_stats.chi2_contingency(matrix)[0]
    assert stat_native == pytest.approx(stat_scipy, abs=_ABS_EPS)


# ---------------------------------------------------------------------------
# (c) statistical.py _compute_benford -> chi2_gof
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not native_enabled("chi2_gof"), reason="chi2_gof kernel not gated on")
def test_chi2_gof_shadow_matches_scipy() -> None:
    # A benford-eligible "amount" column: >=30 positive rows spanning 3 orders
    # of magnitude (10..10000).
    rng = np.random.default_rng(7)
    values = np.floor(10.0 ** rng.uniform(1.0, 4.0, size=200))
    values = values[values > 0]
    assert len(values) >= 30
    span = np.log10(values.max()) - np.log10(values.min())
    assert span >= 2.0

    # Exercise the profiler (its shadow block must not raise out).
    _stat._compute_benford(values)

    # Replicate exactly the observed/expected the profiler feeds scipy.
    observed_counts, total = _stat._leading_digit_counts(values)
    expected_props = {d: math.log10(1 + 1 / d) for d in range(1, 10)}
    observed_props: list[float] = []
    expected_vals: list[float] = []
    for d in range(1, 10):
        observed_props.append(float(observed_counts.get(d, 0)))
        expected_vals.append(expected_props[d] * total)

    chi2_native, p_native = native_module().chi2_gof(observed_props, expected_vals)
    chi2_scipy, p_scipy = _scipy_stats.chisquare(f_obs=observed_props, f_exp=expected_vals)
    assert chi2_native == pytest.approx(chi2_scipy, abs=_ABS_EPS)
    assert p_native == pytest.approx(p_scipy, abs=_ABS_EPS)
    # After the profiler's round(_, 6) the p-values match exactly.
    assert round(p_native, 6) == round(float(p_scipy), 6)
