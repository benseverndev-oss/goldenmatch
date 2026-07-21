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
import math
import os

import polars as pl

from scripts.suggest_quality.converge import converge_unsupervised
from scripts.suggest_quality.metrics import DAMAGE_EPS, recovery_pct
from scripts.suggest_quality.oracle import (
    _auto_configure_no_rerank,
    _compute_f1,
    _run_config,
)

logger = logging.getLogger(__name__)

# ── Degenerate-ceiling floor ──────────────────────────────────────────────────
#
# ``recovery_pct = (recovered - degraded) / (ceiling - degraded)`` is only a
# meaningful signal when the zero-config CEILING is itself a competent config.
# On a dataset whose weighted zero-config ceiling is degenerate (e.g.
# ``historical_50k`` -- heavily-corrupted PII the weighted auto-config path can
# only reach F1 ~0.26 on; the FS path fares far better but the gym uses the
# weighted path), the ceiling is broken, the damage gaps are tiny, and the
# raw-diagnostic convergence (verify=False, no health safety-net) blindly
# over-applies threshold moves -- so a small F1 wiggle amplifies into a ±10x
# recovery_pct that swamps the headline mean with noise.
#
# There is no meaningful "undo the damage" target when the starting config is
# already broken, so such a dataset is skipped from the recovery evaluation
# (the same "skip when not measurable" posture as the no-gt / loader-None
# skips below). The controller's committed HEALTH is NOT a usable discriminator
# here -- every gym dataset commits a best-effort RED config under
# ``confidence_required=False`` (synthetic/anchor reach F1 1.0 yet still log RED),
# so f1_ceiling is the signal that actually separates competent from degenerate.
# Well-behaved gym datasets sit at f1_ceiling 0.96-1.0; the 0.50 floor leaves
# them wide margin while excluding the 0.26 degenerate case. Env-overridable.
_CEILING_FLOOR_DEFAULT: float = 0.50


def _ceiling_floor() -> float:
    """Min zero-config ceiling F1 for a dataset to be a valid recovery target.

    Override via ``GOLDENMATCH_SUGGEST_GYM_CEILING_FLOOR``; falls back to the
    blessed default on an unset/unparseable/out-of-range value."""
    raw = os.environ.get("GOLDENMATCH_SUGGEST_GYM_CEILING_FLOOR", "").strip()
    if not raw:
        return _CEILING_FLOOR_DEFAULT
    try:
        floor = float(raw)
    except ValueError:
        return _CEILING_FLOOR_DEFAULT
    # A floor outside [0, 1] can't be a valid F1 gate; ignore it.
    return floor if 0.0 <= floor <= 1.0 else _CEILING_FLOOR_DEFAULT


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

        # ── Degenerate-ceiling guard ─────────────────────────────────────────
        # recovery_pct against a broken ceiling is noise, not signal (see the
        # _CEILING_FLOOR_DEFAULT rationale). Skip the whole dataset so its
        # raw-diagnostic blow-ups don't poison the headline mean.
        floor = _ceiling_floor()
        if math.isnan(f1_ceiling) or f1_ceiling < floor:
            logger.info(
                "gym: skipping %r (zero-config ceiling F1=%s < floor %.2f -- "
                "degenerate ceiling, recovery%% not measurable)",
                dataset.name,
                "nan" if math.isnan(f1_ceiling) else f"{f1_ceiling:.4f}",
                floor,
            )
            # Emit a dataset-level SKIP SENTINEL so the skip is VISIBLE to the
            # gate. Without it, a blessed dataset that later drifts into a
            # degenerate ceiling is indistinguishable from an erroring/absent one
            # -- both leave no record -- and the gate re-fails it as MISSING,
            # contradicting the whole point of this guard (recovery% here is
            # unmeasurable, so it should be an advisory, not a hard failure).
            # status != "ok" so it never enters ok_records / the headline mean.
            results.append({
                "status": "skipped_degenerate_ceiling",
                "dataset": dataset.name,
                "name": "*",
                "f1_ceiling": None if math.isnan(f1_ceiling) else f1_ceiling,
                "ceiling_floor": floor,
            })
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
