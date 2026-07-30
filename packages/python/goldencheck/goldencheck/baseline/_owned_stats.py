"""Owned pure-Python statistics kernels -- goldencheck's scipy-free reference for
the baseline correlation + Benford profilers.

These mirror the ``goldencheck-core`` Rust kernels (``correlation::pearson_r`` /
``correlation::chi2_contingency_stat`` / ``gof::chi2_gof``) byte-for-byte, so the
baseline path is scipy-free whether or not the native extension is installed:
correlation/Benford now dispatch native-if-available-else-these. They reproduce
``scipy.stats.pearsonr`` / ``chi2_contingency`` / ``chisquare`` to float epsilon
(the parity test asserts ~1e-12); scipy stays a test-only oracle.

Owning them also unblocks cross-surface parity -- these are elementary + one
special function (the regularized upper incomplete gamma for the chi-squared
upper tail), all portable to TS/Rust, where a scipy dependency never could be.

NOTE: the distribution-fit + KS-test (``kstest`` / ``dist.fit``) paths in
``statistical.py`` and ``drift/detector.py`` still use scipy -- the exact
two-sided Kolmogorov distribution and the lognormal MLE are a separate, harder
tier (see the PR that introduced this module).
"""
from __future__ import annotations

import math
from collections.abc import Sequence


def gamma_ur(a: float, x: float) -> float:
    """Regularized upper incomplete gamma Q(a, x) = ``scipy.special.gammaincc``.

    Series for the lower tail when ``x < a + 1`` (then ``Q = 1 - P``); a Lentz
    continued fraction for ``Q`` directly otherwise -- the same split Numerical
    Recipes / Cephes use, so the upper tail keeps full precision for large ``x``
    (never the catastrophic ``1 - cdf`` cancellation). Mirrors the Rust kernel's
    ``statrs::function::gamma::gamma_ur``.
    """
    if x < 0.0 or a <= 0.0:
        return float("nan")
    if x == 0.0:
        return 1.0
    if x < a + 1.0:
        # Series for P(a, x); Q = 1 - P.
        ap = a
        term = 1.0 / a
        total = term
        for _ in range(1000):
            ap += 1.0
            term *= x / ap
            total += term
            if abs(term) < abs(total) * 1e-16:
                break
        p = total * math.exp(-x + a * math.log(x) - math.lgamma(a))
        return 1.0 - p
    # Lentz continued fraction for Q(a, x) directly.
    tiny = 1e-300
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-16:
            break
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def pearson_r(x: Sequence[float], y: Sequence[float]) -> float:
    """Pearson correlation coefficient, matching ``scipy.stats.pearsonr(x, y)[0]``.

    Clamped to ``[-1, 1]`` (scipy does the same, so perfectly-correlated data
    returns exactly +/-1.0). Returns NaN for empty / length-mismatched input; the
    caller pre-guards zero variance."""
    n = len(x)
    if n == 0 or n != len(y):
        return float("nan")
    mx = sum(x) / n
    my = sum(y) / n
    sxy = sxx = syy = 0.0
    for xi, yi in zip(x, y):
        dx = xi - mx
        dy = yi - my
        sxy += dx * dy
        sxx += dx * dx
        syy += dy * dy
    r = sxy / math.sqrt(sxx * syy)
    return max(-1.0, min(1.0, r))


def chi2_contingency_stat(values: Sequence[float], nrows: int, ncols: int) -> float:
    """Chi-squared statistic of a row-major contingency table, matching
    ``scipy.stats.chi2_contingency(matrix)[0]`` (default ``correction=True``).

    Expected counts are ``row_sum[i] * col_sum[j] / total``. For 2x2 tables ONLY,
    Yates' continuity correction clips each residual at 0
    (``max(|obs-exp| - 0.5, 0)``); other shapes use ``(obs-exp)^2 / exp``.
    """
    if nrows == 0 or ncols == 0 or len(values) != nrows * ncols:
        return float("nan")
    row_sums = [0.0] * nrows
    col_sums = [0.0] * ncols
    total = 0.0
    for i in range(nrows):
        for j in range(ncols):
            v = values[i * ncols + j]
            row_sums[i] += v
            col_sums[j] += v
            total += v
    yates = nrows == 2 and ncols == 2
    chi2 = 0.0
    for i in range(nrows):
        for j in range(ncols):
            obs = values[i * ncols + j]
            exp = row_sums[i] * col_sums[j] / total
            diff = abs(obs - exp)
            residual = max(diff - 0.5, 0.0) if yates else diff
            chi2 += residual * residual / exp
    return chi2


def chi2_gof(
    observed: Sequence[float], expected: Sequence[float]
) -> tuple[float, float]:
    """Chi-squared goodness-of-fit statistic + upper-tail p-value, matching
    ``scipy.stats.chisquare(f_obs=observed, f_exp=expected)`` (``ddof=0``).

    ``chi2 = Sum (obs-exp)^2 / exp`` and ``p = gammaincc((k-1)/2, chi2/2)`` (the
    regularized upper incomplete gamma, NOT ``1 - cdf``). Perfect fit ->
    ``(0.0, 1.0)``; ``df <= 0`` -> ``(chi2, NaN)``; empty / mismatched -> NaN.
    """
    n = len(observed)
    if n == 0 or n != len(expected):
        return (float("nan"), float("nan"))
    chi2 = 0.0
    for o, e in zip(observed, expected):
        chi2 += (o - e) ** 2 / e
    if chi2 == 0.0:
        return (chi2, 1.0)
    df = n - 1.0
    if df <= 0.0:
        return (chi2, float("nan"))
    return (chi2, gamma_ur(df / 2.0, chi2 / 2.0))
