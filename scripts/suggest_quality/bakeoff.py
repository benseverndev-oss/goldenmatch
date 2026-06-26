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

import math
from collections.abc import Callable

# Proxy signature: (clusters, n_records) -> health score.
ProxyFn = Callable[[dict, int], float]

# Candidate coverage caps to sweep (the default 0.30 plus a tighter/looser pair).
_COVERAGE_CAPS: tuple[float, ...] = (0.30, 0.15, 0.50)
_EPS: float = 1e-6  # mirrors adapter._VERIFY_EPS


def _coverage_with_cap(clusters: dict, n_records: int, cap: float) -> float:
    """Saturating coverage with an explicit cap.

    TEMPORARY FORK of ``health._coverage`` so this registry is order-independent
    of Task 6 (which adds a ``cap`` param to ``health._coverage``). Once that
    lands, delete this and call ``health._coverage(clusters, n_records, cap=cap)``
    so the bake-off and production share one formula and cannot drift.
    """
    if n_records <= 0:
        return 0.0
    n_matched = sum(i.get("size", 2) for i in clusters.values() if i.get("size", 1) > 1)
    return min((n_matched / n_records) / cap, 1.0)


def build_proxies() -> list[tuple[str, ProxyFn]]:
    """Enumerate candidate proxies as (name, fn(clusters, n_records) -> float)."""
    from goldenmatch.core.suggest import health  # noqa: PLC0415 -- local import keeps module light

    # Keys MUST equal the production GOLDENMATCH_SUGGEST_COHESION values that
    # health._select_cohesion recognizes (min_edge / mean_bottomk_edge /
    # edge_below_cutoff_fraction), so the winning proxy name maps 1:1 to the
    # default we flip later -- no name translation, no silent fall-through.
    cohesion_stats = {
        "min_edge": health._cohesion_min,
        "mean_bottomk_edge": lambda c: health._cohesion_mean_bottomk(c, health._COHESION_BOTTOMK),
        "edge_below_cutoff_fraction": lambda c: health._cohesion_edge_below_cutoff(c, health._COHESION_CUTOFF),
    }

    proxies: list[tuple[str, ProxyFn]] = [
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


def score_proxy(rows: list[dict]) -> dict[str, float | int]:
    """Classifier metrics for one proxy's rows (already filtered to that proxy)."""
    accepted = [r for r in rows if r["accept"]]
    real_wins = [r for r in rows if r["f1_delta"] > 0]
    accepted_harmful = [r for r in accepted if r["f1_delta"] < 0]
    accepted_wins = [r for r in accepted if r["f1_delta"] > 0]
    n_accepted = len(accepted)
    return {
        "n_rows": len(rows),
        "n_accepted": n_accepted,
        "n_accepted_harmful": len(accepted_harmful),
        "n_real_wins": len(real_wins),
        # precision_safe: fraction of accepts that were not harmful (1.0 if none accepted)
        "precision_safe": 1.0 if n_accepted == 0 else (n_accepted - len(accepted_harmful)) / n_accepted,
        # recall: fraction of real wins that were accepted (nan if no real wins)
        "recall": (len(accepted_wins) / len(real_wins)) if real_wins else float("nan"),
    }


def select_best(rows: list[dict]) -> tuple[str | None, dict]:
    """Pick the proxy with max recall among those with ZERO accepted-harmful rows.

    Returns (winner_name_or_None, {proxy: score_dict}). Tie-break: higher
    n_accepted, then lexical name (deterministic).
    """
    by_proxy: dict[str, list[dict]] = {}
    for r in rows:
        by_proxy.setdefault(r["proxy"], []).append(r)
    table = {name: score_proxy(rs) for name, rs in by_proxy.items()}

    # A safe proxy that accepted nothing (recall 0 or nan) is still "eligible" and
    # CAN be returned as a (useless-but-valid) winner -- semantically distinct from
    # None (no safe proxy at all), which is the signal for the Phase-B contingency.
    eligible = [name for name, s in table.items() if s["n_accepted_harmful"] == 0]

    def _key(name: str):
        s = table[name]
        rec = s["recall"]
        rec = -1.0 if math.isnan(rec) else rec  # nan recall -> sorts worst
        return (rec, s["n_accepted"], _neg_lex(name))

    winner = max(eligible, key=_key) if eligible else None
    return winner, table


def _neg_lex(name: str) -> tuple[int, ...]:
    """Lexically-smaller name wins ties (so max() prefers it): negate codepoints."""
    return tuple(-ord(c) for c in name)
