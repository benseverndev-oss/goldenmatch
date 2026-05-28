#!/usr/bin/env python3
"""#510: quality-invariant scale validation harness.

The thesis: match quality and clustering behaviour are invariant across scale.
Existing scale benches measure throughput (wall, RSS) but not quality, so the
"validated" rows in `scale-envelope.md` are throughput claims, not F1 claims.
This harness fills the quality side: at each rung it generates a deterministic
synthetic person dataset (replicating the Phase 5 generator's logic, but keeping
the cluster id so we have ground truth), runs zero-config dedupe, and reports
Pairwise F1, B-cubed F1, Cluster F1, plus wall, peak RSS, cluster counts, and
the committed config the controller chose.

Per-rung output (JSON), so future rungs slot in:
    { "rows": N, "clusters": N/5, "wall_s": ..., "rss_mb_peak": ...,
      "pairwise": {"f1": ..., "p": ..., "r": ..., "tp": ..., "fp": ..., "fn": ...},
      "b_cubed":  {"f1": ..., "p": ..., "r": ...},
      "cluster":  {"f1": ..., "p": ..., "r": ..., "exact": N},
      "predicted_clusters": ..., "multi_member": ..., "committed_config": {...} }

Run a single rung locally:
    python scripts/quality_invariant_scale.py --rows 10000 --out out.json

Run the ladder via the bench-gen Railway service (large rungs): wire a Railway
one-shot job modelled on `Dockerfile.embprov` that invokes this script per N.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import tracemalloc
from pathlib import Path

if sys.platform != "win32":
    import resource as _resource
else:
    _resource = None  # Windows: fall back to tracemalloc in _peak_rss_mb

import numpy as np
import polars as pl

ROWS_PER_CLUSTER = 5
TYPO_RATE = 0.10


def generate_with_gt(n_rows: int, seed: int = 0) -> tuple[pl.DataFrame, np.ndarray]:
    """Replicate the Phase 5 generator in-process, KEEPING the cluster id.

    Returns (df, cluster_ids) where df has the four user-visible columns
    (first_name, last_name, email, zip) plus a stable `id`, and cluster_ids[i]
    is the ground-truth cluster id for the i-th row.
    """
    n_rows = (n_rows // ROWS_PER_CLUSTER) * ROWS_PER_CLUSTER  # round to whole clusters
    n_clusters = n_rows // ROWS_PER_CLUSTER
    rng = np.random.default_rng(seed)
    cids = np.repeat(np.arange(n_clusters, dtype=np.int64), ROWS_PER_CLUSTER)
    typo = rng.random(n_rows) < TYPO_RATE
    df = (
        pl.DataFrame({"__cid__": cids, "__typo__": typo})
        .with_columns(
            first_canon=pl.concat_str([pl.lit("name_"), pl.col("__cid__").cast(pl.Utf8)]),
            last_name=pl.concat_str([pl.lit("sur_"), pl.col("__cid__").cast(pl.Utf8)]),
        )
        .with_columns(
            first_name=pl.when(pl.col("__typo__"))
            .then(pl.col("first_canon").str.replace_all("a", "@", literal=True))
            .otherwise(pl.col("first_canon")),
        )
        .with_columns(
            email=pl.concat_str([pl.col("first_name"), pl.lit("."),
                                 pl.col("last_name"), pl.lit("@example.com")]),
            zip=(pl.col("__cid__") % 100000).cast(pl.Utf8).str.zfill(5),
            id=pl.int_range(0, n_rows, dtype=pl.Int64).cast(pl.Utf8),
        )
        .select("id", "first_name", "last_name", "email", "zip")
    )
    return df, cids


def _pairs_from_clusters(cluster_members: dict[int, list[int]]) -> set[tuple[int, int]]:
    out: set[tuple[int, int]] = set()
    for members in cluster_members.values():
        m = sorted(members)
        for i in range(len(m)):
            for j in range(i + 1, len(m)):
                out.add((m[i], m[j]))
    return out


def score_quality(
    predicted_members: dict[int, list[int]], gt_cids: np.ndarray
) -> dict[str, dict]:
    """Pairwise + B-cubed + Cluster F1 of `predicted_members` vs the gt_cids array.

    predicted_members is {predicted_cluster_id: [row_ids]} for MULTI-MEMBER
    clusters; singletons (rows not in any cluster) are implicit.
    """
    n_rows = len(gt_cids)
    # GT cluster -> members
    gt_clusters: dict[int, list[int]] = {}
    for i, c in enumerate(gt_cids.tolist()):
        gt_clusters.setdefault(int(c), []).append(i)

    # Build a per-row predicted-cluster lookup; rows not in any multi-member
    # predicted cluster are singletons (predicted_cluster_id = -row_id - 1, unique).
    row_to_pred: dict[int, set[int]] = {}
    pred_members_full: dict[int, set[int]] = {}
    next_singleton = -1
    for cid, members in predicted_members.items():
        s = set(members)
        pred_members_full[int(cid)] = s
        for r in members:
            row_to_pred[r] = s
    for i in range(n_rows):
        if i not in row_to_pred:
            sid = next_singleton; next_singleton -= 1
            s = {i}
            pred_members_full[sid] = s
            row_to_pred[i] = s

    # Pairwise F1
    pred_pairs = _pairs_from_clusters({k: list(v) for k, v in predicted_members.items()})
    gt_pairs = _pairs_from_clusters({k: list(v) for k, v in gt_clusters.items() if len(v) > 1})
    tp = len(pred_pairs & gt_pairs); fp = len(pred_pairs - gt_pairs); fn = len(gt_pairs - pred_pairs)
    pp = tp / (tp + fp) if (tp + fp) else 0.0
    pr = tp / (tp + fn) if (tp + fn) else 0.0
    pf1 = (2 * pp * pr / (pp + pr)) if (pp + pr) else 0.0

    # B-cubed F1
    bp_acc = 0.0; br_acc = 0.0
    for i in range(n_rows):
        true_cluster = set(gt_clusters[int(gt_cids[i])])
        pred_cluster = row_to_pred[i]
        inter = len(true_cluster & pred_cluster)
        bp_acc += inter / len(pred_cluster)
        br_acc += inter / len(true_cluster)
    bp = bp_acc / n_rows
    br = br_acc / n_rows
    bf1 = (2 * bp * br / (bp + br)) if (bp + br) else 0.0

    # Cluster F1 (exact-set match)
    gt_set = {frozenset(m) for m in gt_clusters.values() if len(m) > 1}
    pred_set = {frozenset(v) for v in predicted_members.values() if len(v) > 1}
    ctp = len(gt_set & pred_set); cfp = len(pred_set - gt_set); cfn = len(gt_set - pred_set)
    cp = ctp / (ctp + cfp) if (ctp + cfp) else 0.0
    cr = ctp / (ctp + cfn) if (ctp + cfn) else 0.0
    cf1 = (2 * cp * cr / (cp + cr)) if (cp + cr) else 0.0
    return {
        "pairwise": {"f1": pf1, "p": pp, "r": pr, "tp": tp, "fp": fp, "fn": fn},
        "b_cubed": {"f1": bf1, "p": bp, "r": br},
        "cluster": {"f1": cf1, "p": cp, "r": cr, "exact": ctp, "gt_total": len(gt_set), "pred_total": len(pred_set)},
    }


def _peak_rss_mb() -> float | None:
    """Best-effort peak RSS in MB. Linux: ru_maxrss is KB; macOS: bytes; Windows: tracemalloc fallback."""
    if sys.platform == "win32":
        try:
            cur, peak = tracemalloc.get_traced_memory()
            return peak / 1024 / 1024
        except Exception:
            return None
    try:
        ru = _resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss
        return ru / 1024 if sys.platform != "darwin" else ru / 1024 / 1024
    except Exception:
        return None


def run_rung(n_rows: int, seed: int = 0) -> dict:
    import goldenmatch
    os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    if sys.platform == "win32":
        tracemalloc.start()

    t0 = time.time()
    df, gt = generate_with_gt(n_rows, seed=seed)
    t_gen = time.time() - t0

    t1 = time.time()
    result = goldenmatch.dedupe_df(df)
    t_dedupe = time.time() - t1

    predicted: dict[int, list[int]] = {}
    for cid, c in (result.clusters or {}).items():
        members = c.get("members") or []
        if len(members) > 1:
            predicted[int(cid)] = list(members)

    metrics = score_quality(predicted, gt)

    multi = sum(1 for v in predicted.values() if len(v) > 1)
    committed_cfg: dict = {}
    try:
        from goldenmatch.core.autoconfig import _LAST_CONTROLLER_RUN
        state = _LAST_CONTROLLER_RUN.get()
        if state is not None:
            profile, history = state
            committed_cfg = {
                "health": profile.health().value,
                "stop_reason": str(history.stop_reason),
                "iterations": history.iteration,
                "decisions": [d.rule_name for d in (history.decisions or [])],
            }
    except Exception as e:
        committed_cfg = {"_capture_error": repr(e)[:120]}

    return {
        "rows": len(df),
        "clusters_gt": int(len(set(gt.tolist()))),
        "wall_s": {"generate": round(t_gen, 2), "dedupe": round(t_dedupe, 2), "total": round(t_gen + t_dedupe, 2)},
        "rss_mb_peak": _peak_rss_mb(),
        **metrics,
        "predicted_clusters": len(predicted) + (len(df) - sum(len(v) for v in predicted.values())),
        "multi_member_clusters": multi,
        "committed_config": committed_cfg,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", type=Path, default=None, help="write per-rung JSON here")
    args = ap.parse_args(argv)

    res = run_rung(args.rows, seed=args.seed)
    print(json.dumps(res, indent=2, default=str))
    if args.out:
        args.out.write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
