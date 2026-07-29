"""Owned one-sample Kolmogorov-Smirnov test -- goldencheck's scipy-free
``scipy.stats.kstest`` for the distribution-fit + drift profilers.

Two pieces, both verified against scipy (``tests/baseline/test_ks_parity.py``):

* ``kstwo_sf`` -- the exact two-sided Kolmogorov distribution survival function
  (``scipy.stats.kstwo.sf``). Marsaglia, Tsang & Wang (2003) matrix method for
  the body (matches scipy to float epsilon at small n) with their asymptotic tail
  for large ``n * d^2`` (matches to ~1e-6 -- the p-value only gates threshold
  decisions here, and the emitted value is ``round(p, 6)``; verified zero
  threshold-decision disagreements vs scipy). No scipy dependency.
* ``kstest`` -- the D+/D- statistic against a fully-specified ``norm`` /
  ``lognorm`` / ``expon`` / ``uniform`` CDF (all elementary via ``math.erf``),
  byte-identical D to scipy; returns ``(D, kstwo_sf(D, n))``.

Owning it evicts scipy from the drift detector and unblocks a cross-surface KS
test (a scipy dependency never could).
"""
from __future__ import annotations

import math
from collections.abc import Callable, Sequence

import numpy as np

_SQRT2 = math.sqrt(2.0)


def _ks_cdf(n: int, d: float) -> float:
    """P(D_n < d) -- the two-sided Kolmogorov CDF (Marsaglia-Tsang-Wang)."""
    if d <= 0.0:
        return 0.0
    if d >= 1.0:
        return 1.0
    s = d * d * n
    # Asymptotic tail: the same switch + correction as Marsaglia's reference K().
    if s > 7.24 or (s > 3.76 and n > 99):
        return 1.0 - 2.0 * math.exp(-(2.000071 + 0.331 / math.sqrt(n) + 1.409 / n) * s)
    k = int(n * d) + 1
    m = 2 * k - 1
    h = k - n * d
    hh = np.zeros((m, m))
    for i in range(m):
        for j in range(m):
            hh[i, j] = 0.0 if (i - j + 1 < 0) else 1.0
    for i in range(m):
        hh[i, 0] -= h ** (i + 1)
        hh[m - 1, i] -= h ** (m - i)
    if (2 * h - 1) > 0:
        hh[m - 1, 0] += (2 * h - 1) ** m
    for i in range(m):
        for j in range(m):
            if i - j + 1 > 0:
                fac = 1.0
                for g in range(1, i - j + 2):
                    fac *= g
                hh[i, j] /= fac
    q, e_q = _mpower(hh, 0, n, m)
    s2 = q[k - 1, k - 1]
    for i in range(1, n + 1):
        s2 = s2 * i / n
        if s2 < 1e-140:
            s2 *= 1e140
            e_q -= 140
    return s2 * (10.0 ** e_q)


def _mpower(a: np.ndarray, e_a: int, n: int, m: int) -> tuple[np.ndarray, int]:
    """``a**n`` with central-element exponent scaling (Marsaglia mPower)."""
    if n == 1:
        return a.copy(), e_a
    v, e_v = _mpower(a, e_a, n // 2, m)
    b = v @ v
    e_b = 2 * e_v
    if n % 2 == 0:
        r, e_r = b, e_b
    else:
        r, e_r = a @ b, e_a + e_b
    c = m // 2
    if r[c, c] > 1e140:
        r = r * 1e-140
        e_r += 140
    return r, e_r


def kstwo_sf(d: float, n: int) -> float:
    """Survival ``P(D_n >= d)`` = ``scipy.stats.kstwo.sf(d, n)``."""
    return max(0.0, min(1.0, 1.0 - _ks_cdf(n, d)))


def _cdf_norm(x: float, loc: float, scale: float) -> float:
    return 0.5 * (1.0 + math.erf((x - loc) / (scale * _SQRT2)))


def _cdf_expon(x: float, loc: float, scale: float) -> float:
    z = (x - loc) / scale
    return 0.0 if z < 0 else 1.0 - math.exp(-z)


def _cdf_uniform(x: float, loc: float, scale: float) -> float:
    z = (x - loc) / scale
    return 0.0 if z < 0 else (1.0 if z > 1 else z)


def _cdf_lognorm(x: float, s: float, loc: float, scale: float) -> float:
    z = x - loc
    if z <= 0:
        return 0.0
    return 0.5 * (1.0 + math.erf(math.log(z / scale) / (s * _SQRT2)))


# Keyed by the scipy distribution name (what `dist.name` yields), so callers pass
# the same identifier they gave scipy's kstest.
_CDFS: dict[str, Callable[..., float]] = {
    "norm": _cdf_norm,
    "expon": _cdf_expon,
    "uniform": _cdf_uniform,
    "lognorm": _cdf_lognorm,
}


def kstest(
    values: Sequence[float], dist_name: str, params: Sequence[float]
) -> tuple[float, float]:
    """One-sample two-sided KS test of ``values`` vs the fully-specified
    distribution ``dist_name`` (scipy name) with ``params`` (scipy ``args``).

    Returns ``(D, pvalue)`` matching
    ``scipy.stats.kstest(values, dist_name, args=params)``: the D+/D- statistic is
    byte-identical, the p-value is ``kstwo_sf(D, n)``. Raises ``KeyError`` for an
    unsupported distribution.
    """
    cdf = _CDFS[dist_name]
    xs = sorted(float(v) for v in values)
    n = len(xs)
    if n == 0:
        return (float("nan"), float("nan"))
    d_plus = 0.0
    d_minus = 0.0
    for i, x in enumerate(xs):
        c = cdf(x, *params)
        d_plus = max(d_plus, (i + 1) / n - c)
        d_minus = max(d_minus, c - i / n)
    d = max(d_plus, d_minus)
    return (d, kstwo_sf(d, n))
