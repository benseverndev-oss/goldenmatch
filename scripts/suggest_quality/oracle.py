"""Oracle enumeration: measure the true F1 lift of every emitted suggestion.

``evaluate_dataset(name, df, gt_pairs)`` is the core loop:

1. Baseline: auto-configure the df (rerank disabled), run it, expand clusters
   to predicted pairs, compute baseline F1 via evaluate_pairs.
2. Suggestions: review_config(df, baseline_config) -> ranked list[Suggestion].
3. Oracle: for each suggestion, apply it, re-run, compute F1, record lift.
4. Convergence: greedily apply top suggestion, re-run, repeat up to STEP_CAP
   or until no suggestion has positive measured lift.
5. Return a per-dataset record dict.

Predicted pairs derivation
--------------------------
Reuses ``evaluate_clusters`` from ``goldenmatch.core.evaluate``, which expands
each multi-member cluster into all (min, max) member pairs and calls
``evaluate_pairs`` internally.  This is the canonical approach used by existing
benchmarks in the codebase.
"""
from __future__ import annotations

import copy
import logging
import math
from itertools import combinations

import polars as pl

logger = logging.getLogger(__name__)

# Greedy convergence step cap
_CONVERGENCE_STEP_CAP = 5


def _auto_configure_no_rerank(df: pl.DataFrame):
    """Auto-configure df with rerank disabled on all matchkeys.

    Returns a GoldenMatchConfig.  Never raises ControllerNotConfidentError --
    falls back to allow_red_config=True so CI-scale anchors always produce a
    config.
    """
    from goldenmatch.core.autoconfig import auto_configure_df  # noqa: PLC0415

    try:
        config = auto_configure_df(df, confidence_required=False)
    except Exception as exc:
        logger.debug("auto_configure_df first attempt failed: %s", exc, exc_info=True)
        # Ultra-fallback: allow any config including RED
        config = auto_configure_df(df, confidence_required=False, allow_red_config=True)

    # Disable rerank to avoid HuggingFace model downloads in offline/CI env
    try:
        for mk in config.get_matchkeys():
            if getattr(mk, "rerank", False):
                mk.rerank = False
    except Exception:
        logger.debug("_auto_configure_no_rerank: failed to disable rerank", exc_info=True)

    return config


def _run_config(df: pl.DataFrame, config) -> tuple[dict, list]:
    """Run the pipeline for a given config; return (clusters, scored_pairs).

    Uses MatchEngine._run_pipeline (same path as review_config).
    """
    from goldenmatch.tui.engine import MatchEngine  # noqa: PLC0415

    _config = copy.deepcopy(config)
    # Disable rerank to be safe
    try:
        for mk in _config.get_matchkeys():
            if getattr(mk, "rerank", False):
                mk.rerank = False
    except Exception:
        pass

    engine = MatchEngine.from_dataframe(df)
    result = engine._run_pipeline(df, _config)
    return result.clusters, result.scored_pairs


def _clusters_to_predicted_pairs(
    clusters: dict,
    scored_pairs: list[tuple[int, int, float]],
) -> list[tuple[int, int, float]]:
    """Expand multi-member clusters to pairwise predicted pairs.

    Reuses the same approach as ``evaluate_clusters``:
    for each multi-member cluster, emit all (min(a,b), max(a,b), 1.0) pairs.

    We ignore ``scored_pairs`` directly because cluster membership (post-WCC)
    is the canonical output -- just like the existing evaluate_clusters.
    """
    predicted: list[tuple[int, int, float]] = []
    for _cid, info in clusters.items():
        members = info.get("members", [])
        if len(members) < 2:
            continue
        for a, b in combinations(sorted(members), 2):
            predicted.append((a, b, 1.0))
    return predicted


def _compute_f1(
    clusters: dict,
    scored_pairs: list,
    gt_pairs: set,
) -> float:
    """Derive F1 from cluster output vs ground truth pairs.

    When gt_pairs is empty the dataset is a blocking-shape anchor (no truth),
    so F1 is reported as NaN (not applicable).
    """
    if not gt_pairs:
        return float("nan")

    from goldenmatch.core.evaluate import evaluate_pairs  # noqa: PLC0415

    predicted = _clusters_to_predicted_pairs(clusters, scored_pairs)
    result = evaluate_pairs(predicted, gt_pairs)
    return result.f1


def evaluate_dataset(
    name: str,
    df: pl.DataFrame,
    gt_pairs: set,
    *,
    row_cap: int | None = None,
) -> dict:
    """Run the oracle loop for one dataset; return a metrics record.

    Args:
        name: Dataset name (used as a key in the record).
        df: The labeled DataFrame.
        gt_pairs: Ground truth as canonical (min, max) row-index pairs.
        row_cap: If given, truncate df to this many rows before running.

    Returns:
        dict with keys:
            name (str)
            rows (int)
            gt_pairs (int)
            baseline_f1 (float)
            n_suggestions (int)
            suggested_order_lifts (list[float])
            convergence_final_f1 (float)
            convergence_steps (int)
            native_available (bool)
            error (str | None)
    """
    # Row cap
    if row_cap is not None and df.height > row_cap:
        df = df.head(row_cap)
        # Rebuild gt_pairs to only include pairs within the cap
        n = df.height
        gt_pairs = {(a, b) for a, b in gt_pairs if a < n and b < n}

    record: dict = {
        "name": name,
        "rows": df.height,
        "gt_pairs": len(gt_pairs),
        "baseline_f1": float("nan"),
        "n_suggestions": 0,
        "suggested_order_lifts": [],
        "convergence_final_f1": float("nan"),
        "convergence_steps": 0,
        "native_available": False,
        "error": None,
    }

    # Ensure __row_id__ is present (needed by collision_rates in review_config)
    if "__row_id__" not in df.columns:
        df = df.with_row_index("__row_id__").with_columns(
            pl.col("__row_id__").cast(pl.Int64)
        )

    try:
        # ── Step 1: Baseline ──────────────────────────────────────────────────
        baseline_config = _auto_configure_no_rerank(df)
        baseline_clusters, baseline_scored_pairs = _run_config(df, baseline_config)
        baseline_f1 = _compute_f1(baseline_clusters, baseline_scored_pairs, gt_pairs)
        record["baseline_f1"] = baseline_f1

        # ── Step 2: Suggestions ───────────────────────────────────────────────
        try:
            from goldenmatch.core.suggest import SuggestionsNativeRequired, review_config  # noqa: PLC0415

            suggestions = review_config(df, baseline_config)
            record["native_available"] = True
        except SuggestionsNativeRequired:
            record["native_available"] = False
            record["n_suggestions"] = 0
            record["suggested_order_lifts"] = []
            record["convergence_final_f1"] = baseline_f1
            record["convergence_steps"] = 0
            return record
        except Exception as e:
            record["error"] = f"review_config failed: {e}"
            record["convergence_final_f1"] = baseline_f1
            return record

        record["n_suggestions"] = len(suggestions)

        # ── Step 3: Oracle (true lift per suggestion) ─────────────────────────
        lifts: list[float] = []
        for suggestion in suggestions:
            try:
                from goldenmatch.core.suggest import apply_suggestion  # noqa: PLC0415

                cfg2 = apply_suggestion(baseline_config, suggestion)
                clusters2, scored2 = _run_config(df, cfg2)
                f1_after = _compute_f1(clusters2, scored2, gt_pairs)
                lift = (
                    f1_after - baseline_f1
                    if not (math.isnan(f1_after) or math.isnan(baseline_f1))
                    else float("nan")
                )
                lifts.append(lift)
            except Exception as exc:
                logger.debug(
                    "oracle: suggestion %r failed: %s", suggestion.id, exc, exc_info=True
                )
                lifts.append(float("nan"))

        record["suggested_order_lifts"] = lifts

        # ── Step 4: Convergence ───────────────────────────────────────────────
        convergence_steps: list[tuple[str, float]] = []
        applied_ids: set[str] = set()
        current_config = copy.deepcopy(baseline_config)
        current_f1 = baseline_f1

        for _step in range(_CONVERGENCE_STEP_CAP):
            # Re-run suggestions on current config
            try:
                step_suggestions = review_config(df, current_config)
            except Exception:
                break

            if not step_suggestions:
                break

            # Pick the top suggestion; measure its lift
            top = step_suggestions[0]
            # Guard against cycling: if the kernel keeps emitting the same
            # patch (e.g. an idempotent no-op), stop rather than burn steps.
            if top.id in applied_ids:
                break
            applied_ids.add(top.id)
            try:
                cfg_next = apply_suggestion(current_config, top)
                clusters_next, scored_next = _run_config(df, cfg_next)
                f1_next = _compute_f1(clusters_next, scored_next, gt_pairs)
            except Exception as exc:
                logger.debug("convergence step failed: %s", exc, exc_info=True)
                break

            measured_lift = (
                f1_next - current_f1
                if not (math.isnan(f1_next) or math.isnan(current_f1))
                else float("nan")
            )

            if math.isnan(measured_lift) or measured_lift <= 0:
                break  # No positive lift; stop

            convergence_steps.append((top.id, f1_next))
            current_config = cfg_next
            current_f1 = f1_next

        record["convergence_steps"] = len(convergence_steps)
        record["convergence_final_f1"] = (
            convergence_steps[-1][1] if convergence_steps else baseline_f1
        )

    except Exception as exc:
        record["error"] = str(exc)
        logger.warning("evaluate_dataset(%s) failed: %s", name, exc, exc_info=True)

    return record
