"""Distributed (Ray) dedupe pipeline — the path that scales to 100M+.

This is the small, runnable version of what `scripts/bench_phase5_explicit.py`
runs at 100M on a multi-node cluster. It runs Ray in LOCAL mode on a tiny
synthetic dataset, so it needs only `pip install goldenmatch[ray]` — no cluster.

The pipeline (`GOLDENMATCH_DISTRIBUTED_PIPELINE=2`) is driver-collect-free:

    score  ->  local connected-components  ->  distributed join  ->  golden  ->  write

Every stage is distributed and nothing is materialized back on the driver, which
is exactly why it scales: at 100M the driver process peaks at ~0.30 GB while the
workers do all the work. The single load-bearing requirement is a GLOBAL
``__row_id__`` on the input (carried in the data) — without it, each partition
mints local ids that collide across partitions and connected-components merges
unrelated clusters.

Run:
    python examples/distributed_pipeline.py
"""
from __future__ import annotations

import os
import tempfile

try:
    import ray
except ImportError:
    raise SystemExit(
        "This example needs the Ray extra: pip install 'goldenmatch[ray]'"
    )

import polars as pl


def main() -> None:
    # 1. A tiny synthetic dataset with a GLOBAL __row_id__ (0..N-1). Five rows
    #    per entity; one of them has a typo in the first name.
    rows = []
    rid = 0
    for cid in range(2000):
        canon = f"name_{cid}"
        for k in range(5):
            first = canon.replace("a", "@") if k == 0 else canon
            rows.append({
                "__row_id__": rid,
                "first_name": first,
                "last_name": f"sur_{cid}",      # unique per entity -> blocking key
                "email": f"{first}.sur_{cid}@example.com",
            })
            rid += 1
    df = pl.DataFrame(rows)

    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "people.parquet")
        out = os.path.join(tmp, "golden")  # a directory of part files
        df.write_parquet(src)

        # 2. Route through the fully-distributed Phase-5 pipeline.
        os.environ["GOLDENMATCH_DISTRIBUTED_PIPELINE"] = "2"
        os.environ.setdefault("GOLDENMATCH_ENABLE_DISTRIBUTED_RAY", "1")
        ray.init(num_cpus=4, ignore_reinit_error=True, log_to_driver=False)

        from goldenmatch.distributed import read_partitioned
        from goldenmatch.distributed.pipeline import run_dedupe_pipeline_distributed

        ds = read_partitioned(src, n_partitions=4)
        run_dedupe_pipeline_distributed(
            ds, confidence_required=False, output_path=out,
        )

        # 3. Read the distributed-written golden output back to count it.
        import glob
        parts = glob.glob(os.path.join(out, "*.parquet"))
        golden = pl.concat([pl.read_parquet(p) for p in parts]) if parts else pl.DataFrame()
        print(f"input rows: {df.height}")
        print(f"golden records: {golden.height}  (~{2000} entities of 5)")
        print(f"golden columns: {golden.columns}")
        ray.shutdown()


if __name__ == "__main__":
    main()
