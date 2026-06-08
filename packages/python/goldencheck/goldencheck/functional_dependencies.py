"""Discovered functional dependencies as a structured API -- the bridge
GoldenMatch consumes to find data-driven identity anchors for negative evidence.

``functional_dependencies(df)`` returns the strict + approximate single-column
FDs in the data as ``FunctionalDependency(determinant, dependents, confidence)``
records, grouped by determinant. ``confidence`` is the strongest dependency the
determinant supports (1.0 for a strict FD; ``1 - violations/rows`` for an
approximate one). Only determinants reaching ``min_confidence`` are returned.

This wraps the same discovery the FD profilers use (the pyo3-free + native
kernels), but returns structured FDs instead of ``Finding`` objects -- the shape
a consumer (e.g. GoldenMatch's negative-evidence selection) actually wants.
Fail-soft: empty list on a trivial frame; uses the native kernels when present,
the pure-Python fallbacks otherwise.
"""
from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from goldencheck.core._native_loader import native_enabled, native_module
from goldencheck.relations import approx_fd as _afd
from goldencheck.relations import functional_dependency as _fd

__all__ = ["FunctionalDependency", "functional_dependencies"]


@dataclass(frozen=True)
class FunctionalDependency:
    """``determinant`` functionally determines every column in ``dependents``.
    ``confidence`` is the strongest such dependency (1.0 = strict)."""
    determinant: str
    dependents: list[str]
    confidence: float


def _strict_pairs(df: pl.DataFrame, n: int) -> list[tuple[str, str]]:
    cols = _fd._select_candidates(df, n)
    if len(cols) < 2:
        return []
    pairs: list[tuple[int, int]]
    if native_enabled("functional_dependencies"):
        try:
            pairs = native_module().discover_functional_dependencies([df[c].to_arrow() for c in cols])
        except Exception:  # noqa: BLE001 - native failure -> Python fallback
            pairs = _fd._discover_polars(df, cols, n)
    else:
        pairs = _fd._discover_polars(df, cols, n)
    return [(cols[i], cols[j]) for i, j in pairs]


def _approx_triples(df: pl.DataFrame, n: int, min_conf: float) -> list[tuple[str, str, float]]:
    cols = _afd._select_candidates(df)
    if len(cols) < 2:
        return []
    triples: list[tuple[int, int, int]]
    if native_enabled("approximate_fd"):
        try:
            triples = native_module().discover_approximate_fds([df[c].to_arrow() for c in cols], min_conf)
        except Exception:  # noqa: BLE001 - native failure -> Python fallback
            triples = _afd._discover_python([_afd._intern(df[c].to_list()) for c in cols], n, min_conf)
    else:
        triples = _afd._discover_python([_afd._intern(df[c].to_list()) for c in cols], n, min_conf)
    return [(cols[i], cols[j], 1.0 - viol / n) for i, j, viol in triples]


def functional_dependencies(
    df: pl.DataFrame,
    *,
    min_confidence: float = 0.95,
) -> list[FunctionalDependency]:
    """Strict + approximate single-column FDs, grouped by determinant.

    A determinant is returned when it determines at least one column at
    >= ``min_confidence``. Sorted by confidence desc then name."""
    n = df.height
    if n < 2 or df.width < 2:
        return []

    # determinant -> {dependent: confidence}; strict (1.0) wins ties.
    det_to: dict[str, dict[str, float]] = {}
    for det, dep in _strict_pairs(df, n):
        det_to.setdefault(det, {})[dep] = 1.0
    for det, dep, conf in _approx_triples(df, n, min_confidence):
        deps = det_to.setdefault(det, {})
        if conf > deps.get(dep, 0.0):
            deps[dep] = conf

    out: list[FunctionalDependency] = []
    for det, deps in det_to.items():
        if not deps:
            continue
        max_conf = max(deps.values())
        if max_conf < min_confidence:
            continue
        out.append(FunctionalDependency(determinant=det, dependents=sorted(deps), confidence=max_conf))
    out.sort(key=lambda f: (-f.confidence, f.determinant))
    return out
