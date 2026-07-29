"""Owned derivative-free optimizer — infermap's replacement for the ``scipy``
runtime dependency.

infermap used ``scipy`` for exactly one thing: a 2-parameter
``scipy.optimize.minimize(..., method="Nelder-Mead")`` call fitting the Platt
(sigmoid) calibrator's log-loss. This module owns that one algorithm so
``pip install infermap`` no longer pulls scipy (a compiled BLAS/LAPACK/Fortran
tree, tens of MB beyond numpy). scipy stays a test-only parity oracle in the
workspace dev group -- "own it, don't clone it".

``minimize_nelder_mead`` mirrors scipy's ``_minimize_neldermead`` exactly (initial
simplex construction, reflection/expansion/contraction/shrink coefficients, the
``xatol`` + ``fatol`` convergence test, and a stable simplex sort), so on the
convex objectives infermap fits it converges to scipy's optimum to ~1e-12 (the
parity test in ``tests/test_optimize_parity.py`` asserts this against scipy).
"""
from __future__ import annotations

from collections.abc import Callable, Sequence

import numpy as np


def minimize_nelder_mead(
    func: Callable[[np.ndarray], float],
    x0: Sequence[float] | np.ndarray,
    *,
    xatol: float = 1e-4,
    fatol: float = 1e-4,
    maxiter: int | None = None,
    maxfev: int | None = None,
) -> tuple[np.ndarray, float]:
    """Minimize ``func`` over ``x0`` with the Nelder-Mead simplex method.

    Returns ``(x_opt, f_opt)``. Defaults match scipy's Nelder-Mead: the initial
    simplex perturbs each coordinate by 5% (``0.00025`` when it is zero),
    coefficients ``(rho, chi, psi, sigma) = (1, 2, 0.5, 0.5)``, convergence when
    BOTH the simplex spread (``xatol``) and the function spread (``fatol``) are
    below tolerance, and ``maxiter = maxfev = 200 * n``.
    """
    start = np.asarray(x0, dtype=float).flatten()
    n = start.size
    max_iter = n * 200 if maxiter is None else maxiter
    max_fev = n * 200 if maxfev is None else maxfev
    rho, chi, psi, sigma = 1.0, 2.0, 0.5, 0.5
    nonzdelt, zdelt = 0.05, 0.00025

    # Initial simplex: the start point plus one vertex per coordinate.
    sim = np.empty((n + 1, n), dtype=float)
    sim[0] = start
    for k in range(n):
        y = np.array(start, copy=True)
        y[k] = (1 + nonzdelt) * y[k] if y[k] != 0 else zdelt
        sim[k + 1] = y

    fsim = np.array([float(func(s)) for s in sim])
    ncalls = n + 1
    order = np.argsort(fsim, kind="stable")
    sim, fsim = sim[order], fsim[order]

    iterations = 1
    while ncalls < max_fev and iterations < max_iter:
        if (
            np.max(np.abs(sim[1:] - sim[0])) <= xatol
            and np.max(np.abs(fsim[0] - fsim[1:])) <= fatol
        ):
            break
        xbar = np.add.reduce(sim[:-1], 0) / n
        xr = xbar + rho * (xbar - sim[-1])
        fxr = float(func(xr))
        ncalls += 1
        doshrink = False

        if fxr < fsim[0]:
            xe = xbar + rho * chi * (xbar - sim[-1])
            fxe = float(func(xe))
            ncalls += 1
            if fxe < fxr:
                sim[-1], fsim[-1] = xe, fxe
            else:
                sim[-1], fsim[-1] = xr, fxr
        elif fxr < fsim[-2]:
            sim[-1], fsim[-1] = xr, fxr
        else:
            # Contraction.
            if fxr < fsim[-1]:
                xc = xbar + psi * rho * (xbar - sim[-1])
                fxc = float(func(xc))
                ncalls += 1
                if fxc <= fxr:
                    sim[-1], fsim[-1] = xc, fxc
                else:
                    doshrink = True
            else:
                xcc = xbar + psi * (sim[-1] - xbar)
                fxcc = float(func(xcc))
                ncalls += 1
                if fxcc < fsim[-1]:
                    sim[-1], fsim[-1] = xcc, fxcc
                else:
                    doshrink = True
            if doshrink:
                for j in range(1, n + 1):
                    sim[j] = sim[0] + sigma * (sim[j] - sim[0])
                    fsim[j] = float(func(sim[j]))
                    ncalls += 1

        order = np.argsort(fsim, kind="stable")
        sim, fsim = sim[order], fsim[order]
        iterations += 1

    return sim[0], fsim[0]
