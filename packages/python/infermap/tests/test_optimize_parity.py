"""Parity gate for the owned Nelder-Mead optimizer (``infermap._optimize``).

Asserts ``minimize_nelder_mead`` converges to the same optimum as
``scipy.optimize.minimize(method="Nelder-Mead")`` on the convex Platt log-loss
that ``PlattCalibrator`` fits -- to ~1e-9 on the parameters AND on the resulting
calibrated probabilities. scipy is a test-only oracle (workspace dev group); the
infermap runtime is scipy-free.
"""
from __future__ import annotations

import numpy as np
import pytest
from infermap._optimize import minimize_nelder_mead

scipy_optimize = pytest.importorskip("scipy.optimize")


def _platt_nll(scores: np.ndarray, correct: np.ndarray):
    def nll(params: np.ndarray) -> float:
        a, b = params
        z = a * scores + b
        logp = -np.logaddexp(0.0, -z)
        log1mp = -np.logaddexp(0.0, z)
        return -float(np.sum(correct * logp + (1.0 - correct) * log1mp))

    return nll


@pytest.mark.parametrize("seed", range(25))
def test_owned_nm_matches_scipy_on_platt(seed: int) -> None:
    rng = np.random.default_rng(seed)
    n = int(rng.integers(30, 400))
    scores = rng.random(n)
    # Labels correlated with score (a well-posed, non-separable fit).
    p = 1.0 / (1.0 + np.exp(-(3.0 * scores - 1.2)))
    correct = (rng.random(n) < p).astype(float)
    nll = _platt_nll(scores, correct)

    x_owned, f_owned = minimize_nelder_mead(nll, [1.0, 0.0])
    res = scipy_optimize.minimize(nll, x0=np.array([1.0, 0.0]), method="Nelder-Mead")

    assert np.allclose(x_owned, res.x, atol=1e-8, rtol=0)
    assert f_owned == pytest.approx(float(res.fun), abs=1e-9)
    # The calibrated probabilities over the score range agree to ~float noise.
    grid = np.linspace(0.0, 1.0, 101)
    p_owned = 1.0 / (1.0 + np.exp(-(x_owned[0] * grid + x_owned[1])))
    p_scipy = 1.0 / (1.0 + np.exp(-(res.x[0] * grid + res.x[1])))
    assert np.max(np.abs(p_owned - p_scipy)) < 1e-9


def test_owned_nm_minimizes_simple_quadratic() -> None:
    # Sanity: min of (x-3)^2 + (y+1)^2 is (3, -1).
    x, f = minimize_nelder_mead(lambda v: (v[0] - 3.0) ** 2 + (v[1] + 1.0) ** 2, [0.0, 0.0])
    assert np.allclose(x, [3.0, -1.0], atol=1e-3)
    assert f < 1e-6


def test_platt_calibrator_fit_is_scipy_free() -> None:
    # Prove PlattCalibrator.fit does not import scipy: install a meta-path finder
    # that raises on any scipy import, drop cached scipy modules, then fit.
    import sys

    from infermap.calibration import PlattCalibrator

    class _Blocker:
        def find_spec(self, name, path=None, target=None):
            if name == "scipy" or name.startswith("scipy."):
                raise ImportError(f"scipy import blocked: {name}")
            return None

    blocker = _Blocker()
    saved = {k: v for k, v in sys.modules.items() if k == "scipy" or k.startswith("scipy.")}
    for k in saved:
        del sys.modules[k]
    sys.meta_path.insert(0, blocker)
    try:
        rng = np.random.default_rng(0)
        scores = rng.random(300)
        correct = (rng.random(300) < scores).astype(float)
        cal = PlattCalibrator()
        cal.fit(scores, correct)  # would raise if fit touched scipy
        out = cal.transform(np.array([0.0, 0.5, 1.0]))
        assert out.shape == (3,)
        assert np.all((out >= 0.0) & (out <= 1.0))
        assert not any(m == "scipy" or m.startswith("scipy.") for m in sys.modules)
    finally:
        sys.meta_path.remove(blocker)
        sys.modules.update(saved)
