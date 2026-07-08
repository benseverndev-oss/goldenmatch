"""Memory-capped throughput bench for the fused match stage (the "RSS -> speed"
experiment). Ben's hypothesis: fused's ~2x lower peak RSS lets you run ~2x the
concurrent dedup jobs under a fixed RAM ceiling -> ~2x throughput, in the
MEMORY-BOUND regime the earlier (CPU-bound, 64GB) wall bench couldn't show.

This runs W concurrent dedup jobs (each on its own N-row frame) in separate
PROCESSES -- true parallelism, no GIL confound -- and reports aggregate
throughput (rows/s). Run it under a cgroup memory cap (the workflow wraps it in
`systemd-run -p MemoryMax=...`); the OOM-kill at the cap is the RSS signal. The
path that sustains more workers before the cap kills it has the higher
throughput ceiling at that RAM budget.

Per-job N is sized so the per-job working set dominates the per-process
interpreter+lib base (~0.5 GB), so the delta measured is working set, not base.

Usage (under a cap):
  systemd-run --scope -p MemoryMax=24G -p MemorySwapMax=0 -- \
    python bench_match_fused_memcap.py --path fused --n 5000000 --workers 8
"""

import argparse
import json
import os
import random
import time
from concurrent.futures import ProcessPoolExecutor

SCORER_IDS = [0]  # jaro_winkler
WEIGHTS = [1.0]
TOTAL_WEIGHT = 1.0
THRESHOLD = 0.85


def _gen(n: int, keycard: int, seed: int):
    rng = random.Random(seed)
    firsts = ["john", "jane", "mary", "mike", "sara", "dave", "lisa", "paul", "anna", "mark"]
    lasts = ["smith", "jones", "brown", "davis", "moore", "clark", "hall", "young", "king", "wood"]
    import polars as pl

    n_keys = max(1, n // keycard)
    keys = [f"blk{rng.randint(0, n_keys)}" for _ in range(n)]
    names = []
    for _ in range(n):
        base = f"{rng.choice(firsts)} {rng.choice(lasts)}"
        if rng.random() < 0.3:
            base = base[:-1] + rng.choice("aeiou")
        names.append(base)
    return (
        pl.DataFrame({"blk": keys, "name": names})
        .with_row_index("__row_id__")
        .with_columns(pl.col("__row_id__").cast(pl.Int64))
    )


def _job(args):
    """One dedup job in its own process. Returns rows processed + cluster count."""
    path, n, keycard, seed = args
    os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")
    import goldenmatch._native as native
    import polars as pl
    import pyarrow as pa

    df = _gen(n, keycard, seed)
    if path == "fused":
        row_ids = df.get_column("__row_id__").to_arrow()
        key = df.get_column("blk").cast(pl.Utf8).to_arrow()
        score = df.get_column("name").cast(pl.Utf8).to_arrow()
        clusters = native.match_fused(
            row_ids, [key], [score], SCORER_IDS, WEIGHTS, TOTAL_WEIGHT, THRESHOLD
        )
        cnt = sum(1 for c in clusters if len(c) >= 2)
    else:
        keyed = df.with_columns(pl.col("blk").cast(pl.Utf8).alias("__bk__")).filter(
            pl.col("__bk__").is_not_null()
            & ~pl.col("__bk__").str.strip_chars().str.to_lowercase().is_in(["nan", "null", "none"])
        )
        counts = keyed.group_by("__bk__").agg(pl.len().alias("__n__"))
        keep = counts.filter(pl.col("__n__") >= 2).select("__bk__")
        keyed = keyed.join(keep, on="__bk__", how="inner").sort("__bk__")
        sizes = keyed.group_by("__bk__", maintain_order=True).agg(pl.len().alias("__n__"))
        block_sizes = sizes.get_column("__n__").to_list()
        row_ids = keyed.get_column("__row_id__").to_arrow()
        fields = [keyed.get_column("name").cast(pl.Utf8).to_arrow()]
        pairs = native.score_block_pairs_arrow(
            row_ids, fields, block_sizes, SCORER_IDS, WEIGHTS, TOTAL_WEIGHT, THRESHOLD
        )
        if pairs:
            ia = pa.array([p[0] for p in pairs], type=pa.int64())
            ib = pa.array([p[1] for p in pairs], type=pa.int64())
            sc = pa.array([p[2] for p in pairs], type=pa.float64())
        else:
            ia = pa.array([], type=pa.int64())
            ib = pa.array([], type=pa.int64())
            sc = pa.array([], type=pa.float64())
        da, db, ds = native.dedup_pairs_arrow(ia, ib, sc)
        all_ids = df.get_column("__row_id__").to_arrow()
        labels = native.connected_components_arrow(da, db, ds, all_ids).to_pylist()
        cnt = sum(1 for c in labels if len(c) >= 2)
    return n, cnt


def _cgroup_peak_mb():
    """Best-effort peak RSS of this process's cgroup (systemd scope), MB."""
    try:
        with open("/proc/self/cgroup") as fh:
            rel = fh.read().strip().split(":")[-1]
        path = f"/sys/fs/cgroup{rel}/memory.peak"
        with open(path) as fh:
            return round(int(fh.read().strip()) / 1024 / 1024, 1)
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", choices=["fused", "pipeline"], required=True)
    ap.add_argument("--n", type=int, required=True)
    ap.add_argument("--keycard", type=int, default=20)
    ap.add_argument("--workers", type=int, required=True)
    args = ap.parse_args()

    jobs = [(args.path, args.n, args.keycard, 11 + i) for i in range(args.workers)]
    t0 = time.perf_counter()
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        results = list(ex.map(_job, jobs))
    wall = time.perf_counter() - t0
    total_rows = sum(r[0] for r in results)

    print(json.dumps({
        "path": args.path,
        "n": args.n,
        "workers": args.workers,
        "total_rows": total_rows,
        "wall_s": round(wall, 3),
        "rows_per_s": round(total_rows / wall) if wall > 0 else None,
        "cgroup_peak_mb": _cgroup_peak_mb(),
        "status": "ok",
    }))


if __name__ == "__main__":
    main()
