"""Phase 5.5 head-to-head: Two-Phase WCC vs label propagation.

Reads a pre-scored pairs parquet, derives all_ids from it, and times
both algorithms on the same input. Reports per-algorithm wall +
correctness check (same partition structure).

Run:
    GOLDENMATCH_ENABLE_DISTRIBUTED_RAY=1 \
    python scripts/bench_phase5_5_wcc.py \
        --pairs chains_50m.parquet
"""

from __future__ import annotations

import argparse
import os
import sys
import time


def _partitions_from_labels(label_rows: list[dict]) -> set[frozenset[int]]:
    """Group members by label, return as a hashable partition structure."""
    by_label: dict[int, list[int]] = {}
    for r in label_rows:
        by_label.setdefault(r["label"], []).append(r["id"])
    return {frozenset(members) for members in by_label.values()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pairs", type=str, required=True)
    ap.add_argument(
        "--label-prop-timeout-sec",
        type=float,
        default=300.0,
        help="Cap label_propagation iterations to avoid hanging on chains.",
    )
    ap.add_argument(
        "--skip-label-prop",
        action="store_true",
        help=(
            "Skip the label_propagation comparison entirely. At >= 5M pairs "
            "label_prop's HashAggregate-per-iter is the whole point of the "
            "Two-Phase WCC switch (it does not finish in the 30 min job cap). "
            "Set this when validating Two-Phase WCC against current-main."
        ),
    )
    args = ap.parse_args()

    os.environ.setdefault("GOLDENMATCH_ENABLE_DISTRIBUTED_RAY", "1")

    from goldenmatch.distributed import read_parquet_partitioned
    from goldenmatch.distributed.clustering import (
        label_propagation,
        two_phase_wcc,
    )

    pairs_ds = read_parquet_partitioned(args.pairs, n_partitions=64)
    ids: set[int] = set()
    for row in pairs_ds.take_all():
        ids.add(row["id_a"])
        ids.add(row["id_b"])
    all_ids = list(ids)
    print(f"loaded {len(all_ids)} unique ids")

    # Two-Phase WCC
    t_tp = time.perf_counter()
    tp_labels = two_phase_wcc(pairs_ds, all_ids=all_ids)
    tp_rows = list(tp_labels.take_all())
    tp_wall = time.perf_counter() - t_tp
    tp_partitions = _partitions_from_labels(tp_rows)
    print(f"two_phase_wcc: {tp_wall:.1f}s, {len(tp_partitions)} components")

    if args.skip_label_prop:
        print("label_propagation: skipped (--skip-label-prop)")
        print(f"two_phase_wcc_wall_s={tp_wall:.1f}")
        print(f"two_phase_wcc_components={len(tp_partitions)}")
        return 0

    # Label propagation (with iteration cap so it doesn't hang forever on chains)
    t_lp = time.perf_counter()
    try:
        lp_labels_ds, lp_iters = label_propagation(
            pairs_ds, all_ids=all_ids,
            convergence_max_iterations=30,
        )
        lp_rows = list(lp_labels_ds.take_all())
        lp_wall = time.perf_counter() - t_lp
        lp_partitions = _partitions_from_labels(lp_rows)
        print(f"label_propagation: {lp_wall:.1f}s, {lp_iters} iters, {len(lp_partitions)} components")
        partitions_match = tp_partitions == lp_partitions
        print(f"partitions_match={partitions_match}")
        if not partitions_match:
            print("WARN: partition structures differ between algorithms")
            return 1
        speedup = lp_wall / tp_wall if tp_wall > 0 else float("inf")
        print(f"two_phase_wcc_speedup_vs_label_prop={speedup:.2f}x")
    except Exception as e:
        lp_wall = time.perf_counter() - t_lp
        print(f"label_propagation FAILED after {lp_wall:.1f}s: {e}")
        print("two_phase_wcc_speedup_vs_label_prop=inf (label_propagation did not finish)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
