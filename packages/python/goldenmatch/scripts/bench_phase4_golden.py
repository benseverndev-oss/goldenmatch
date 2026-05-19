"""Phase 4 kill criterion: golden stage < 180s at 25M.

Pre-runs dedupe via in-memory bucket backend (same as Phase 3 bench), then
times only the distributed golden stage via the polymorphic dispatch on
`core.golden.build_golden_records_batch`.

Run:
    GOLDENMATCH_ENABLE_DISTRIBUTED_RAY=1 \
    python scripts/bench_phase4_golden.py \
        --input bench-dataset-v1/bench_25000000.parquet
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import psutil

KILL_WALL_SEC = 180.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=str, required=True)
    ap.add_argument("--kill-wall-sec", type=float, default=KILL_WALL_SEC)
    args = ap.parse_args()

    os.environ["GOLDENMATCH_ENABLE_DISTRIBUTED_RAY"] = "1"

    import goldenmatch as gm
    import polars as pl
    import ray
    from goldenmatch.core.autoconfig import auto_configure_df
    from goldenmatch.core.golden import build_golden_records_batch

    proc = psutil.Process()
    baseline = proc.memory_info().rss

    t_load = time.perf_counter()
    df = pl.read_parquet(args.input)
    load_wall = time.perf_counter() - t_load

    # Pre-pipeline via in-memory bucket backend (same as Phase 3 bench).
    # dedupe_df adds __row_id__ internally as a 0..N-1 sequential index
    # matching the input row order. cluster members reference those ints.
    t_dedupe = time.perf_counter()
    cfg = auto_configure_df(df, confidence_required=False, _skip_finalize=True)
    cfg.backend = "bucket"
    result = gm.dedupe_df(df, config=cfg, confidence_required=False)
    dedupe_wall = time.perf_counter() - t_dedupe

    # Reconstruct multi_df: rows in multi-member clusters with __cluster_id__.
    # cluster members are __row_id__ ints from dedupe_df's internal 0..N-1
    # numbering, which lines up with df row position via with_row_index.
    member_to_cid: dict[int, int] = {}
    for cid, info in result.clusters.items():
        if info["size"] > 1:
            for m in info["members"]:
                member_to_cid[m] = cid

    if not member_to_cid:
        print("no multi-member clusters; nothing to bench")
        return 0

    # Add __row_id__ now (AFTER dedupe so we don't conflict with goldenmatch's
    # internal column), then map cluster_id from member_to_cid.
    multi_df = (
        df.with_row_index("__row_id__")
        .with_columns(pl.col("__row_id__").cast(pl.Int64))
        .with_columns(
            pl.col("__row_id__")
            .replace_strict(
                list(member_to_cid.keys()),
                list(member_to_cid.values()),
                default=-1,
            )
            .alias("__cluster_id__")
        )
        .filter(pl.col("__cluster_id__") != -1)
    )

    print(
        f"multi_df_rows={multi_df.height} "
        f"multi_clusters={len(set(member_to_cid.values()))}"
    )

    from goldenmatch.config.schemas import GoldenRulesConfig

    rules = cfg.golden_rules or GoldenRulesConfig()

    # Convert to Ray Dataset and time only the golden stage.
    ds = ray.data.from_arrow(multi_df.to_arrow())
    t_golden = time.perf_counter()
    out = build_golden_records_batch(ds, rules)
    golden_wall = time.perf_counter() - t_golden

    peak_gb = proc.memory_info().rss / 1024**3

    print(f"load_wall_sec={load_wall:.1f}")
    print(f"dedupe_wall_sec={dedupe_wall:.1f}")
    print(f"golden_wall_sec={golden_wall:.1f}")
    print(f"golden_records={len(out)}")
    print(f"driver_peak_rss_gb={peak_gb:.2f}")
    print(f"baseline_rss_gb={baseline / 1024**3:.2f}")

    if golden_wall >= args.kill_wall_sec:
        print(f"KILL: golden wall {golden_wall:.1f}s >= {args.kill_wall_sec}s")
        return 1
    print(f"PASS: golden wall {golden_wall:.1f}s under {args.kill_wall_sec}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
