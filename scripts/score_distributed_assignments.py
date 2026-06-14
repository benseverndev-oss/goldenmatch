"""Oracle-score a distributed Phase-5 run's cluster assignments (parquet dir)
against the deterministic QIS realistic ground truth, WITHOUT re-running the
cluster. The assignments ({member_id, cluster_id, ...}) are what the distributed
WCC produced; GT is deterministic (cids = repeat(arange(n_clusters), 5), so
gt_cids[i] = i // 5). Streams one cluster at a time -- no 20M-entry Python dict.

Usage: score_distributed_assignments.py --dir <parquet_dir> --rows <N> [--out f.json]
"""
from __future__ import annotations

import argparse
import json
import time

import numpy as np
import polars as pl

ROWS_PER_CLUSTER = 5


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", required=True, help="local dir of assignment parquet files")
    ap.add_argument("--rows", type=int, required=True, help="total rows in the run (N)")
    ap.add_argument("--out", default=None, help="write metrics JSON here")
    args = ap.parse_args()

    n_rows = (args.rows // ROWS_PER_CLUSTER) * ROWS_PER_CLUSTER
    t0 = time.time()
    gt_cids = np.arange(n_rows, dtype=np.int64) // ROWS_PER_CLUSTER
    gt_sizes_arr = np.bincount(gt_cids)
    gt_multi_total = int((gt_sizes_arr > 1).sum())
    gt_pair_total = int(np.sum(gt_sizes_arr * (gt_sizes_arr - 1) // 2))
    print(f"[score] GT ready ({time.time()-t0:.0f}s): {n_rows:,} rows, "
          f"{len(gt_sizes_arr):,} gt clusters", flush=True)

    grouped = (
        pl.scan_parquet(f"{args.dir}/*.parquet")
        .select(["member_id", "cluster_id"])
        .group_by("cluster_id")
        .agg(pl.col("member_id"))
        .collect(engine="streaming")
    )
    print(f"[score] grouped {grouped.height:,} predicted clusters "
          f"({time.time()-t0:.0f}s)", flush=True)

    in_multi = np.zeros(n_rows, dtype=bool)
    pred_tp = pred_pair_total = exact_cluster_matches = pred_multi_total = 0
    bp_acc = br_acc = 0.0
    for members_s in grouped["member_id"]:
        arr = members_s.to_numpy()
        sz = arr.shape[0]
        if sz <= 1:
            continue
        pred_multi_total += 1
        in_multi[arr] = True
        uniq, counts = np.unique(gt_cids[arr], return_counts=True)
        pred_tp += int(np.sum(counts * (counts - 1) // 2))
        pred_pair_total += sz * (sz - 1) // 2
        sq = counts.astype(np.float64) ** 2
        bp_acc += float(np.sum(sq) / sz)
        br_acc += float(np.sum(sq / gt_sizes_arr[uniq]))
        if uniq.size == 1 and sz == int(gt_sizes_arr[uniq[0]]):
            exact_cluster_matches += 1
    print(f"[score] streamed clusters ({time.time()-t0:.0f}s)", flush=True)

    singleton_mask = ~in_multi
    n_single = int(singleton_mask.sum())
    if n_single:
        gt_single = gt_cids[singleton_mask]
        bp_acc += float(n_single)
        br_acc += float(np.sum(1.0 / gt_sizes_arr[gt_single]))

    pp = pred_tp / pred_pair_total if pred_pair_total else 0.0
    pr = pred_tp / gt_pair_total if gt_pair_total else 0.0
    pf1 = (2 * pp * pr / (pp + pr)) if (pp + pr) else 0.0
    bp = bp_acc / n_rows
    br = br_acc / n_rows
    bf1 = (2 * bp * br / (bp + br)) if (bp + br) else 0.0
    cp = exact_cluster_matches / pred_multi_total if pred_multi_total else 0.0
    cr = exact_cluster_matches / gt_multi_total if gt_multi_total else 0.0
    cf1 = (2 * cp * cr / (cp + cr)) if (cp + cr) else 0.0

    res = {
        "rows": n_rows,
        "engine": "distributed-phase5",
        "pairwise": {"f1": pf1, "p": pp, "r": pr},
        "b_cubed": {"f1": bf1, "p": bp, "r": br},
        "cluster": {"f1": cf1, "p": cp, "r": cr, "exact": exact_cluster_matches},
        "multi_member_predicted": pred_multi_total,
        "multi_member_gt": gt_multi_total,
    }
    print("\n=== DISTRIBUTED %d-ROW QUALITY (from assignments) ===" % n_rows)
    print(f"pairwise: f1={pf1:.4f}  p={pp:.4f}  r={pr:.4f}")
    print(f"b_cubed:  f1={bf1:.4f}  p={bp:.4f}  r={br:.4f}")
    print(f"cluster:  f1={cf1:.4f}  p={cp:.4f}  r={cr:.4f}  exact={exact_cluster_matches:,}")
    print(f"multi-member predicted: {pred_multi_total:,}  gt: {gt_multi_total:,}")
    print(f"[score] total {time.time()-t0:.0f}s", flush=True)
    if args.out:
        with open(args.out, "w") as f:
            json.dump(res, f, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
