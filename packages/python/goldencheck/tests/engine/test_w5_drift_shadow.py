"""Shadow-compute proof for the two W5 drift reuse kernels.

`drift/detector.py` now shadow-computes the W4 native kernels
(`chi2_gof` / `pearson_r`) alongside the authoritative scipy path in
`_compute_benford_pvalue` and `_compute_correlation`, discarding the result.
This test proves the kernel values MATCH the scipy values drift feeds them --
on the exact input shapes drift builds (a benford observed/expected pair and a
correlated Float64 numeric pair) -- i.e. they are ready to become authoritative
at a future Flip.

It asserts nothing about drift's emitted `Finding`s; the existing drift tests
stay green unedited. Each half skips cleanly when the relevant native kernel
isn't built/enabled (e.g. before the W4 rebase lands the symbols) -- the skip is
VISIBLE, so a bad rebase that drops the kernel surfaces as a skip, not a silent
false-green.
"""
from __future__ import annotations

import math

import numpy as np
import pyarrow as pa
import pytest
import scipy.stats as _stats
from goldencheck.core._native_loader import native_enabled, native_module

_EPS = 1e-9

chi2_only = pytest.mark.skipif(
    not native_enabled("chi2_gof"),
    reason="goldencheck native chi2_gof kernel not built/enabled",
)
pearson_only = pytest.mark.skipif(
    not native_enabled("pearson_r"),
    reason="goldencheck native pearson_r kernel not built/enabled",
)


@chi2_only
def test_chi2_gof_shadow_matches_scipy_on_benford_inputs() -> None:
    # The shapes drift's _compute_benford_pvalue feeds scipy: 9 observed
    # leading-digit counts vs Benford expected counts scaled to the same total.
    observed = [301, 176, 125, 97, 79, 67, 58, 51, 46]
    total = sum(observed)
    expected = [math.log10(1 + 1 / d) * total for d in range(1, 10)]

    _chi2, scipy_p = _stats.chisquare(f_obs=observed, f_exp=expected)
    kernel = native_module().chi2_gof(observed, expected)
    # chi2_gof returns the p-value (matching scipy's second return element).
    kernel_p = kernel[1] if isinstance(kernel, (tuple, list)) else kernel
    assert abs(float(kernel_p) - float(scipy_p)) < _EPS


@pearson_only
def test_pearson_r_shadow_matches_scipy_on_correlated_pair() -> None:
    # The shape drift's _compute_correlation pearson branch feeds scipy: two
    # already-Float64 numpy arrays. Build a correlated pair (>= 30 rows).
    rng = np.random.default_rng(42)
    a_vals = np.arange(50, dtype=np.float64)
    b_vals = 2.0 * a_vals + rng.normal(0.0, 3.0, size=50)

    scipy_corr, _ = _stats.pearsonr(a_vals, b_vals)
    kernel = native_module().pearson_r(
        pa.array(a_vals, type=pa.float64()),
        pa.array(b_vals, type=pa.float64()),
    )
    kernel_corr = kernel[0] if isinstance(kernel, (tuple, list)) else kernel
    assert abs(float(kernel_corr) - float(scipy_corr)) < _EPS
