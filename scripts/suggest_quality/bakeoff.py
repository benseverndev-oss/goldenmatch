"""Verify-gate proxy bake-off.

Offline harness that scores every candidate unsupervised health proxy as an
accept/reject classifier against ground-truth F1, to pick the proxy with the
highest recall at zero accepted-harmful fixes. See
docs/superpowers/specs/2026-06-26-suggest-verify-gate-proxy-design.md.

Pure functions (registry + scoring + selection) at the top are import-safe and
unit-tested; the raw per-fix evaluation loop (added in a later task) defers all
goldenmatch imports.
"""
from __future__ import annotations

from collections.abc import Callable

# Candidate coverage caps to sweep (the default 0.30 plus a tighter/looser pair).
_COVERAGE_CAPS: tuple[float, ...] = (0.30, 0.15, 0.50)
_EPS: float = 1e-6  # mirrors adapter._VERIFY_EPS


def _coverage_with_cap(clusters: dict, n_records: int, cap: float) -> float:
    """Saturating coverage with an explicit cap (mirrors health._coverage)."""
    if n_records <= 0:
        return 0.0
    n_matched = sum(i.get("size", 2) for i in clusters.values() if i.get("size", 1) > 1)
    return min((n_matched / n_records) / cap, 1.0)


def build_proxies() -> list[tuple[str, Callable]]:
    """Enumerate candidate proxies as (name, fn(clusters, n_records) -> float)."""
    from goldenmatch.core.suggest import health  # local import keeps module light

    # Keys MUST equal the production GOLDENMATCH_SUGGEST_COHESION values that
    # health._select_cohesion recognizes (min_edge / mean_bottomk_edge /
    # edge_below_cutoff_fraction), so the winning proxy name maps 1:1 to the
    # default we flip later -- no name translation, no silent fall-through.
    cohesion_stats = {
        "min_edge": health._cohesion_min,
        "mean_bottomk_edge": lambda c: health._cohesion_mean_bottomk(c, health._COHESION_BOTTOMK),
        "edge_below_cutoff_fraction": lambda c: health._cohesion_edge_below_cutoff(c, health._COHESION_CUTOFF),
    }

    proxies: list[tuple[str, Callable]] = [
        ("legacy", lambda c, n: float(health._health_legacy(c, n))),
    ]
    for stat_name, stat_fn in cohesion_stats.items():
        for cap in _COVERAGE_CAPS:
            suffix = "" if cap == 0.30 else f"_cap{int(cap * 100)}"
            name = f"cohesion_{stat_name}{suffix}"
            proxies.append(
                (name, (lambda c, n, sf=stat_fn, cp=cap: float(sf(c) * _coverage_with_cap(c, n, cp))))
            )
    return proxies
