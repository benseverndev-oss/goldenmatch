"""Recovery-eval loop: measure how well converge_unsupervised undoes each perturbation.

``evaluate_perturbation(df, gt_pairs, perturbation, ceiling_config, f1_ceiling)``
is the core primitive; ``run_catalog(datasets, perturbations)`` iterates the
full cross-product.

Honest-mirror invariant
------------------------
The gym mirrors production: each damaging perturbation is converged TWICE.

  * **live** (``verify=True``) -- the headline.  Production reality: the
    user-facing self-verification health-proxy filter is ON, so this is what a
    user actually gets.  ``recovery_pct`` (no suffix) aliases the live value.
  * **raw** (``verify=False``) -- a diagnostic.  Self-verify OFF; what the rules
    could mechanically do.

``verification_gap = recovery_pct_raw - recovery_pct_live`` quantifies how much
correct fixing the health proxy suppresses -- the gym's most valuable signal.

Status values
-------------
``"ok"``         -- damage was measurable and recovery was attempted.
``"no_damage"``  -- perturbation did not lower F1 by at least DAMAGE_EPS.
``"n/a"``        -- perturbation does not apply to the ceiling config.
``"error"``      -- an exception was caught; the run continues.
"""
from __future__ import annotations

import logging

import polars as pl

from scripts.suggest_quality.converge import converge_unsupervised
from scripts.suggest_quality.metrics import DAMAGE_EPS, recovery_pct
from scripts.suggest_quality.oracle import (
    _auto_configure_no_rerank,
    _compute_f1,
    _run_config,
)

logger = logging.getLogger(__name__)


def evaluate_perturbation(
    df: pl.DataFrame,
    gt_pairs: set,
    perturbation,
    ceiling_config,
    f1_ceiling: float,
) -> dict:
    """Evaluate one perturbation on a single dataset.

    Args:
        df: The labeled DataFrame (must already have ``__row_id__``).
        gt_pairs: Ground-truth (min, max) row-index pair set.
        perturbation: A ``Perturbation`` instance from ``perturbations.CATALOG``.
        ceiling_config: The zero-config ceiling (built once per dataset).
        f1_ceiling: Pre-computed F1 for ``ceiling_config``.

    Returns:
        A dict with at least ``"status"`` and ``"name"``.  Full schema
        documented in the module docstring.
    """
    base = {
        "name": perturbation.name,
        "builds_on_existing_rule": perturbation.builds_on_existing_rule,
    }

    # ── Step 1: applicability guard ───────────────────────────────────────────
    try:
        applies = perturbation.applies_to(ceiling_config)
    except Exception:
        applies = False

    if not applies:
        return {**base, "status": "n/a"}

    # ── Step 2: apply perturbation and measure degraded F1 ───────────────────
    degraded = perturbation.apply(ceiling_config)
    clusters_d, scored_d = _run_config(df, degraded)
    f1_degraded = _compute_f1(clusters_d, scored_d, gt_pairs)

    no_damage_base = {
        **base,
        "f1_ceiling": f1_ceiling,
        "f1_degraded": f1_degraded,
    }

    # ── Step 3: damage check ──────────────────────────────────────────────────
    import math
    if math.isnan(f1_degraded) or math.isnan(f1_ceiling):
        return {**no_damage_base, "status": "no_damage"}
    if f1_ceiling - f1_degraded < DAMAGE_EPS:
        return {**no_damage_base, "status": "no_damage"}

    # ── Step 4: convergence run TWICE (live = production, raw = diagnostic) ───
    #
    # The gym is an honest mirror of production.  We MUST report what a user
    # genuinely gets, not the raw-rules path dressed up as production.
    #
    #   live = converge_unsupervised(verify=True)   -- the HEADLINE.  This is
    #          production reality: review_config's self-verification (the
    #          unsupervised health-proxy filter) is ON, exactly as a user
    #          experiences it.  If the proxy suppresses the correct fix, live
    #          recovery is genuinely ~0 -- that is a real finding, not a bug.
    #
    #   raw  = converge_unsupervised(verify=False)  -- a DIAGNOSTIC.  Self-verify
    #          is OFF, so this measures what the rules could mechanically do.
    #          The gap (raw - live) is the most valuable signal the gym gives:
    #          it quantifies how much correct fixing the health proxy suppresses.
    live_recovered, live_trail = converge_unsupervised(df, degraded, verify=True)
    clusters_live, scored_live = _run_config(df, live_recovered)
    f1_recovered_live = _compute_f1(clusters_live, scored_live, gt_pairs)

    raw_recovered, raw_trail = converge_unsupervised(df, degraded, verify=False)
    clusters_raw, scored_raw = _run_config(df, raw_recovered)
    f1_recovered_raw = _compute_f1(clusters_raw, scored_raw, gt_pairs)

    # ── Step 5: recovery percentages ──────────────────────────────────────────
    recovery_pct_live = recovery_pct(f1_degraded, f1_recovered_live, f1_ceiling)
    recovery_pct_raw = recovery_pct(f1_degraded, f1_recovered_raw, f1_ceiling)

    # ── Step 6: did the expected rule fire (per path)? ────────────────────────
    def _fired(trail) -> bool:
        return any(
            getattr(s, "kind", None) == perturbation.expected_rule for s in trail
        )

    expected_rule_fired_live = _fired(live_trail)
    expected_rule_fired_raw = _fired(raw_trail)

    # ── Step 7: full result dict ──────────────────────────────────────────────
    # `recovery_pct` / `f1_recovered` / `expected_rule_fired` / `n_applied` /
    # `applied_kinds` are LIVE aliases so downstream code / the headline use
    # production reality by default.
    return {
        "status": "ok",
        "name": perturbation.name,
        "expected_rule": perturbation.expected_rule,
        "builds_on_existing_rule": perturbation.builds_on_existing_rule,
        "f1_ceiling": f1_ceiling,
        "f1_degraded": f1_degraded,
        # ── live (production headline) ────────────────────────────────────────
        "f1_recovered_live": f1_recovered_live,
        "recovery_pct_live": recovery_pct_live,
        "expected_rule_fired_live": expected_rule_fired_live,
        "n_applied_live": len(live_trail),
        "applied_kinds_live": [getattr(s, "kind", None) for s in live_trail],
        # ── raw (diagnostic) ──────────────────────────────────────────────────
        "f1_recovered_raw": f1_recovered_raw,
        "recovery_pct_raw": recovery_pct_raw,
        "expected_rule_fired_raw": expected_rule_fired_raw,
        "n_applied_raw": len(raw_trail),
        "applied_kinds_raw": [getattr(s, "kind", None) for s in raw_trail],
        # ── the signal ────────────────────────────────────────────────────────
        "verification_gap": recovery_pct_raw - recovery_pct_live,
        # ── live aliases (default = production reality) ───────────────────────
        "f1_recovered": f1_recovered_live,
        "recovery_pct": recovery_pct_live,
        "expected_rule_fired": expected_rule_fired_live,
        "n_applied": len(live_trail),
        "applied_kinds": [getattr(s, "kind", None) for s in live_trail],
    }


def run_catalog(datasets, perturbations) -> list[dict]:
    """Evaluate every (dataset, perturbation) pair.

    Args:
        datasets: Iterable of ``Dataset`` instances (from ``datasets.REGISTRY``).
        perturbations: Iterable of ``Perturbation`` instances.

    Returns:
        Flat list of result dicts; each has a ``"dataset"`` key added.
        Never raises -- errors are caught per-pair and appended as
        ``{"status": "error", ...}``.
    """
    results: list[dict] = []

    for dataset in datasets:
        # ── Load dataset ──────────────────────────────────────────────────────
        try:
            loaded = dataset.loader()
        except Exception as exc:
            logger.warning("gym: loader failed for %r: %s", dataset.name, exc)
            loaded = None

        if loaded is None:
            logger.info("gym: skipping %r (loader returned None)", dataset.name)
            continue

        df, gt_pairs = loaded

        if not gt_pairs:
            logger.info(
                "gym: skipping %r (no gt_pairs -- blocking-shape anchor)", dataset.name
            )
            continue

        # ── Ensure __row_id__ (mirrors oracle.evaluate_dataset) ──────────────
        if "__row_id__" not in df.columns:
            df = df.with_row_index("__row_id__").with_columns(
                pl.col("__row_id__").cast(pl.Int64)
            )

        # ── Build ceiling config once per dataset ─────────────────────────────
        try:
            ceiling_config = _auto_configure_no_rerank(df)
            clusters_c, scored_c = _run_config(df, ceiling_config)
            f1_ceiling = _compute_f1(clusters_c, scored_c, gt_pairs)
        except Exception as exc:
            logger.warning(
                "gym: ceiling build failed for %r: %s", dataset.name, exc
            )
            continue

        # ── Loop perturbations ────────────────────────────────────────────────
        for perturbation in perturbations:
            try:
                record = evaluate_perturbation(
                    df, gt_pairs, perturbation, ceiling_config, f1_ceiling
                )
            except Exception as exc:
                logger.warning(
                    "gym: evaluate_perturbation(%r, %r) failed: %s",
                    dataset.name,
                    perturbation.name,
                    exc,
                    exc_info=True,
                )
                record = {
                    "status": "error",
                    "name": perturbation.name,
                    "error": str(exc),
                    "builds_on_existing_rule": perturbation.builds_on_existing_rule,
                }

            record["dataset"] = dataset.name
            results.append(record)

    return results
