"""Owned continuous-distribution fits + log-pdfs -- goldencheck's scipy-free
replacement for the ``scipy.stats`` distribution objects the statistical profiler
fits (``norm`` / ``lognorm`` / ``expon`` / ``uniform``).

Each entry exposes the three things ``_fit_distribution`` needs, matching the
scipy object it replaces:

* ``name`` -- the scipy distribution name, so the owned KS test (``_ks``) keys on
  the same identifier.
* ``fit(values) -> params`` -- the maximum-likelihood parameters, in scipy's
  ``args`` order/parameterization.
* ``logpdf(values, *params) -> np.ndarray`` -- per-point log density (for AIC).

``norm`` / ``expon`` / ``uniform`` are closed-form MLEs, **byte-identical** to
scipy's ``.fit`` (parity test asserts it). ``lognorm`` is fitted as the standard
TWO-parameter lognormal (``loc = 0``, the textbook MLE for strictly-positive data
-- which is exactly the domain guard the caller applies), rather than scipy's
unconstrained 3-parameter fit whose free ``loc`` is numerically unstable. This is
a deliberate, documented divergence (own the algorithm, not scipy's flaky fit):
distribution *selection* agrees with the scipy path on ~99% of columns, differing
only on genuinely-ambiguous data where either label is statistically defensible
(the selection-stability test quantifies it).
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

_LOG2PI = math.log(2.0 * math.pi)
_SQRT2PI = math.sqrt(2.0 * math.pi)


@dataclass(frozen=True)
class OwnedDist:
    """A fittable continuous distribution (scipy-free)."""

    name: str
    fit: Callable[[np.ndarray], tuple[float, ...]]
    logpdf: Callable[..., np.ndarray]


# --------------------------------------------------------------------------- #
# norm: MLE loc = mean, scale = population std (ddof=0). Matches scipy.norm.fit.
# --------------------------------------------------------------------------- #
def _norm_fit(x: np.ndarray) -> tuple[float, float]:
    x = np.asarray(x, dtype=float)
    loc = float(x.mean())
    scale = float(math.sqrt(float(((x - loc) ** 2).mean())))
    return (loc, scale)


def _norm_logpdf(x: np.ndarray, loc: float, scale: float) -> np.ndarray:
    z = (np.asarray(x, dtype=float) - loc) / scale
    return -0.5 * z * z - math.log(scale) - 0.5 * _LOG2PI


# --------------------------------------------------------------------------- #
# expon: MLE loc = min, scale = mean - min. Matches scipy.expon.fit.
# --------------------------------------------------------------------------- #
def _expon_fit(x: np.ndarray) -> tuple[float, float]:
    x = np.asarray(x, dtype=float)
    loc = float(x.min())
    scale = float(x.mean() - loc)
    return (loc, scale)


def _expon_logpdf(x: np.ndarray, loc: float, scale: float) -> np.ndarray:
    z = (np.asarray(x, dtype=float) - loc) / scale
    return np.where(z < 0, -np.inf, -math.log(scale) - z)


# --------------------------------------------------------------------------- #
# uniform: loc = min, scale = max - min. Matches scipy.uniform.fit.
# --------------------------------------------------------------------------- #
def _uniform_fit(x: np.ndarray) -> tuple[float, float]:
    x = np.asarray(x, dtype=float)
    loc = float(x.min())
    scale = float(x.max() - loc)
    return (loc, scale)


def _uniform_logpdf(x: np.ndarray, loc: float, scale: float) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    inside = (x >= loc) & (x <= loc + scale)
    return np.where(inside, -math.log(scale), -np.inf)


# --------------------------------------------------------------------------- #
# lognorm (loc = 0): closed-form MLE on log(x). params (s, loc, scale) in scipy's
# order, with loc fixed at 0 and scale = exp(mean(log x)), s = std(log x, ddof=0).
# --------------------------------------------------------------------------- #
def _lognorm_fit(x: np.ndarray) -> tuple[float, float, float]:
    lnx = np.log(np.asarray(x, dtype=float))
    mu = float(lnx.mean())
    s = float(math.sqrt(float(((lnx - mu) ** 2).mean())))
    return (s, 0.0, float(math.exp(mu)))


def _lognorm_logpdf(x: np.ndarray, s: float, loc: float, scale: float) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    z = x - loc
    out = np.full(x.shape, -np.inf, dtype=float)
    pos = z > 0
    zp = z[pos]
    logterm = np.log(zp / scale)
    out[pos] = -np.log(zp * s * _SQRT2PI) - logterm * logterm / (2.0 * s * s)
    return out


NORM = OwnedDist("norm", _norm_fit, _norm_logpdf)
LOGNORM = OwnedDist("lognorm", _lognorm_fit, _lognorm_logpdf)
EXPON = OwnedDist("expon", _expon_fit, _expon_logpdf)
UNIFORM = OwnedDist("uniform", _uniform_fit, _uniform_logpdf)
