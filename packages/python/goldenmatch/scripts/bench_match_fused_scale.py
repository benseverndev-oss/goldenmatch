"""Scale bench for the fused Arrow-native match stage (increment 2/3).

Runs ONE path (fused | pipeline) over a seeded synthetic dedupe frame and emits
JSON: {wall_s, peak_rss_mb, n_clusters, cluster_fp}. The driver runs each path
in its OWN process so (a) peak RSS is isolated per path and (b) an OOM-kill of
the pipeline (exit 137) doesn't take down the fused run — the whole point of the
scale test is the shape where the materializing pipeline dies and the fused
kernel survives at flat memory.

Both paths do IDENTICAL scoring (score_one) so the compute is held equal; the
delta is the Polars group/materialize + the per-stage Arrow/py-list round trips
that fusion removes. Force parallel scoring on both with
GOLDENMATCH_NATIVE_RAYON_MIN_PAIRS=0 for a fair many-core comparison.

Usage:
  python bench_match_fused_scale.py --path fused    --n 5000000 --keycard 20
  python bench_match_fused_scale.py --path pipeline --n 5000000 --keycard 20
"""
import argparse
import hashlib
import json
import os
import random
import sys
import time

os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")

import polars as pl
import pyarrow as pa
import goldenmatch._native as native

SCORER_IDS = [0]  # jaro_winkler
WEIGHTS = [1.0]
TOTAL_WEIGHT = 1.0
THRESHOLD = 0.85


def peak_rss_mb() -> float:
    try:
        import resource

        kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return kb / 1024.0  # Linux ru_maxrss is KB
    except Exception:
        try:
            import psutil

            return psutil.Process().memory_info().rss / 1024.0 / 1024.0
        except Exception:
            return float("nan")


def gen(n: int, keycard: int) -> pl.DataFrame:
    rng = random.Random(11)
    firsts = ["john", "jane", "mary", "mike", "sara", "dave", "lisa", "paul", "anna", "mark"]
    lasts = ["smith", "jones", "brown", "davis", "moore", "clark", "hall", "young", "king", "wood"]
    n_keys = max(1, n // keycard)
    keys = [None] * n
    names = [None] * n
    for i in range(n):
        keys[i] = f"blk{rng.randint(0, n_keys)}"
        base = f"{rng.choice(firsts)} {rng.choice(lasts)}"
        if rng.random() < 0.3:
            base = base[:-1] + rng.choice("aeiou")
        names[i] = base
    return (
        pl.DataFrame({"blk": keys, "name": names})
        .with_row_index("__row_id__")
        .with_columns(pl.col("__row_id__").cast(pl.Int64))
    )


def cluster_fp(clusters) -> str:
    """Deterministic fingerprint of multi-member clusters (order-independent)."""
    sets = sorted(
        tuple(sorted(c)) for c in clusters if len(c) >= 2
    )
    h = hashlib.sha256()
    for s in sets:
        h.update(repr(s).encode())
    return h.hexdigest()[:16], len(sets)


def run_fused(df: pl.DataFrame):
    row_ids = df.get_column("__row_id__").to_arrow()
    key = df.get_column("blk").cast(pl.Utf8).to_arrow()
    score = df.get_column("name").cast(pl.Utf8).to_arrow()
    t0 = time.perf_counter()
    clusters = native.match_fused(
        row_ids, [key], [score], SCORER_IDS, WEIGHTS, TOTAL_WEIGHT, THRESHOLD
    )
    wall = time.perf_counter() - t0
    return wall, clusters


def run_pipeline(df: pl.DataFrame):
    t0 = time.perf_counter()
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
    labels = native.connected_components_arrow(da, db, ds, all_ids)
    wall = time.perf_counter() - t0
    return wall, labels.to_pylist()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", choices=["fused", "pipeline"], required=True)
    ap.add_argument("--n", type=int, required=True)
    ap.add_argument("--keycard", type=int, default=20)
    args = ap.parse_args()

    df = gen(args.n, args.keycard)
    if args.path == "fused":
        wall, clusters = run_fused(df)
    else:
        wall, clusters = run_pipeline(df)
    fp, n_clusters = cluster_fp(clusters)

    print(json.dumps({
        "path": args.path,
        "n": args.n,
        "keycard": args.keycard,
        "wall_s": round(wall, 3),
        "peak_rss_mb": round(peak_rss_mb(), 1),
        "n_clusters": n_clusters,
        "cluster_fp": fp,
        "rayon_min_pairs": os.environ.get("GOLDENMATCH_NATIVE_RAYON_MIN_PAIRS", "default"),
    }))


if __name__ == "__main__":
    main()
