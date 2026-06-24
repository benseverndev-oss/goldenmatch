"""SLOW tier: full dedupe -> F1/P/R + blocking-recall/threshold-loss attribution.

Runs the real dedupe pipeline on a (row-capped) labeled dataset and computes
F1/precision/recall via evaluate_clusters, plus the blocking-recall vs
threshold-loss attribution so an F1 drop is localized. All ids are ROW INDICES
(cluster members, scored_pairs, candidate pairs, and the ground-truth pairs all
share the 0..n-1 row-index space).
"""
from __future__ import annotations

import importlib.util
import os
from itertools import combinations
from pathlib import Path
from typing import Any

import goldenmatch
import polars as pl
from goldenmatch.core.autoconfig import build_blocking, profile_columns
from goldenmatch.core.blocker import build_blocks
from goldenmatch.core.evaluate import evaluate_clusters

# attribution.py lives in scripts/bench_er_headtohead/, which is NOT a real
# package (no __init__.py). Load it by file path -- the same precedent as
# packages/python/goldenmatch/tests/bench/test_attribution.py -- so this does
# NOT depend on the repo root being on sys.path / on PEP 420 namespace packages.
_ATTR_PATH = Path(__file__).resolve().parents[2] / "scripts/bench_er_headtohead/attribution.py"
_spec = importlib.util.spec_from_file_location("_qh_attribution", _ATTR_PATH)
assert _spec is not None and _spec.loader is not None, f"cannot load {_ATTR_PATH}"
_attr_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_attr_mod)
attribution = _attr_mod.attribution


def _candidate_pairs(df: pl.DataFrame) -> set[tuple[int, int]] | None:
    """Post-blocking candidate set in row-index space, or None if materializing it
    would exceed GOLDENMATCH_QH_ATTR_MAX_PAIRS (default 10M) -- attribution is then
    skipped (the F1 floor never depends on this set).

    DedupeResult has no candidate set, so rebuild via build_blocks + __row_id__.
    Degrades to an empty set (attribution -> 0 blocking_recall) rather than crashing
    the F1 path if blocking itself can't be materialized.
    """
    cap = int(os.environ.get("GOLDENMATCH_QH_ATTR_MAX_PAIRS", "10000000"))
    profiles = profile_columns(df)
    blocking = build_blocking(profiles, df, n_rows_full=df.height)
    lf = df.with_row_index("__row_id__").lazy()
    blocks: list[list[int]] = []
    projected = 0
    try:
        for b in build_blocks(lf, blocking):
            ids = b.df.collect()["__row_id__"].to_list()
            projected += len(ids) * (len(ids) - 1) // 2
            if projected > cap:
                return None  # over budget -> skip attribution, keep the F1 floor
            blocks.append(ids)
    except Exception:
        return set()
    cand: set[tuple[int, int]] = set()
    for ids in blocks:
        cand.update((min(a, c), max(a, c)) for a, c in combinations(ids, 2))
    return cand


def evaluate_f1(df: pl.DataFrame, gt_pairs: set, row_cap: int | None = 20_000) -> dict[str, Any]:
    """Full dedupe -> F1/P/R + blocking/threshold attribution (row-index space)."""
    if row_cap is not None and df.height > row_cap:
        df = df.head(row_cap)
        gt_pairs = {(a, b) for a, b in gt_pairs if a < row_cap and b < row_cap}
    result = goldenmatch.dedupe_df(df)
    ev = evaluate_clusters(result.clusters, gt_pairs).summary()
    emitted = {(min(a, b), max(a, b)) for a, b, _ in result.scored_pairs}
    cand = _candidate_pairs(df)
    if cand is None:
        attr_out: dict = {"skipped": "scale"}  # candidate set too large to materialize
    else:
        attr = attribution(gt_pairs, cand, emitted)
        attr_out = {
            k: attr[k] for k in ("blocking_recall", "final_recall", "threshold_loss")
        }
    return {
        "f1": ev["f1"],
        "precision": ev["precision"],
        "recall": ev["recall"],
        "attribution": attr_out,
    }
