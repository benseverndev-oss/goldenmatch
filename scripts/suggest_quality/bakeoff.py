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


_STEP_CAP = 5


def bakeoff_dataset(df, gt_pairs, degraded_config, proxies) -> list[dict]:
    """Raw greedy convergence over `degraded_config`, emitting one row per
    (applied fix x proxy). Advances the RAW path (apply top suggestion each
    step regardless of any gate). Defers goldenmatch imports."""
    import copy  # noqa: PLC0415

    from goldenmatch.core.suggest import apply_suggestion, review_config  # noqa: PLC0415

    from scripts.suggest_quality.oracle import _compute_f1, _run_config  # noqa: PLC0415

    n = df.height
    rows: list[dict] = []
    current = copy.deepcopy(degraded_config)
    cur_clusters, cur_scored = _run_config(df, current)
    f1_current = _compute_f1(cur_clusters, cur_scored, gt_pairs)
    applied_ids: set = set()

    for step in range(_STEP_CAP):
        suggestions = review_config(df, current, verify=False)
        if not suggestions:
            break
        top = suggestions[0]
        if top.id in applied_ids:
            break
        applied_ids.add(top.id)

        candidate = apply_suggestion(current, top)
        cand_clusters, cand_scored = _run_config(df, candidate)
        f1_cand = _compute_f1(cand_clusters, cand_scored, gt_pairs)
        f1_delta = f1_cand - f1_current

        for proxy_name, proxy_fn in proxies:
            delta = proxy_fn(cand_clusters, n) - proxy_fn(cur_clusters, n)
            rows.append({
                "proxy": proxy_name,
                "step": step,
                "kind": getattr(top, "kind", None),
                "accept": delta >= -_EPS,
                "proxy_delta": delta,
                "f1_delta": f1_delta,
            })

        # advance raw path
        current, cur_clusters, cur_scored, f1_current = (
            candidate, cand_clusters, cand_scored, f1_cand
        )
    return rows


def run_bakeoff_catalog(datasets, perturbations, proxies) -> list[dict]:
    """Mirror gym.run_catalog: load -> ceiling -> per-damaging-perturbation
    raw bake-off. Each emitted row carries dataset + perturbation. Never raises."""
    import logging  # noqa: PLC0415
    import math  # noqa: PLC0415

    import polars as pl  # noqa: PLC0415

    from scripts.suggest_quality.metrics import DAMAGE_EPS  # noqa: PLC0415
    from scripts.suggest_quality.oracle import (  # noqa: PLC0415
        _auto_configure_no_rerank,
        _compute_f1,
        _run_config,
    )

    log = logging.getLogger(__name__)
    out: list[dict] = []

    for dataset in datasets:
        try:
            loaded = dataset.loader()
        except Exception as exc:
            log.warning("bakeoff: loader failed for %r: %s", dataset.name, exc)
            loaded = None
        if loaded is None:
            continue
        df, gt_pairs = loaded
        if not gt_pairs:
            continue
        if "__row_id__" not in df.columns:
            df = df.with_row_index("__row_id__").with_columns(pl.col("__row_id__").cast(pl.Int64))

        try:
            ceiling = _auto_configure_no_rerank(df)
            cc, cs = _run_config(df, ceiling)
            f1_ceiling = _compute_f1(cc, cs, gt_pairs)
        except Exception as exc:
            log.warning("bakeoff: ceiling failed for %r: %s", dataset.name, exc)
            continue

        for pert in perturbations:
            try:
                if not pert.applies_to(ceiling):
                    continue
                degraded = pert.apply(ceiling)
                dc, ds = _run_config(df, degraded)
                f1_degraded = _compute_f1(dc, ds, gt_pairs)
                if math.isnan(f1_degraded) or math.isnan(f1_ceiling):
                    continue
                if f1_ceiling - f1_degraded < DAMAGE_EPS:
                    continue  # no_damage: nothing to recover, skip
                rows = bakeoff_dataset(df, gt_pairs, degraded, proxies)
            except Exception as exc:
                log.warning("bakeoff: %r/%r failed: %s", dataset.name, pert.name, exc, exc_info=True)
                continue
            for r in rows:
                r["dataset"] = dataset.name
                r["perturbation"] = pert.name
                out.append(r)
    return out
