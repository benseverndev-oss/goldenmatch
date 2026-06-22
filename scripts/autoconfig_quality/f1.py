"""SLOW tier: full dedupe -> F1/P/R + blocking-recall/threshold-loss attribution.

Runs the real dedupe pipeline on a (row-capped) labeled dataset and computes
F1/precision/recall via evaluate_clusters, plus the blocking-recall vs
threshold-loss attribution so an F1 drop is localized. All ids are ROW INDICES
(cluster members, scored_pairs, candidate pairs, and the ground-truth pairs all
share the 0..n-1 row-index space).
"""
from __future__ import annotations

import importlib.util
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


def _candidate_pairs(df: pl.DataFrame) -> set[tuple[int, int]]:
    """Regenerate the post-blocking candidate set in row-index space.

    DedupeResult has no candidate set, so rebuild via build_blocks + __row_id__.
    Degrades to an empty set (attribution -> 0 blocking_recall) rather than
    crashing the F1 path if blocking can't be materialized.
    """
    profiles = profile_columns(df)
    blocking = build_blocking(profiles, df, n_rows_full=df.height)
    lf = df.with_row_index("__row_id__").lazy()
    cand: set[tuple[int, int]] = set()
    try:
        for b in build_blocks(lf, blocking):
            ids = b.df.collect()["__row_id__"].to_list()
            cand.update((min(a, c), max(a, c)) for a, c in combinations(ids, 2))
    except Exception:
        return set()
    return cand


def evaluate_f1(df: pl.DataFrame, gt_pairs: set, row_cap: int | None = 20_000) -> dict[str, Any]:
    """Full dedupe -> F1/P/R + blocking/threshold attribution (row-index space)."""
    if row_cap is not None and df.height > row_cap:
        df = df.head(row_cap)
        gt_pairs = {(a, b) for a, b in gt_pairs if a < row_cap and b < row_cap}
    result = goldenmatch.dedupe_df(df)
    ev = evaluate_clusters(result.clusters, gt_pairs).summary()
    emitted = {(min(a, b), max(a, b)) for a, b, _ in result.scored_pairs}
    attr = attribution(gt_pairs, _candidate_pairs(df), emitted)
    return {
        "f1": ev["f1"],
        "precision": ev["precision"],
        "recall": ev["recall"],
        "attribution": {
            k: attr[k] for k in ("blocking_recall", "final_recall", "threshold_loss")
        },
    }
