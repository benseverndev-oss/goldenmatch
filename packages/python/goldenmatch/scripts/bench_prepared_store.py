"""Benchmark the PreparedRecordStore (Distributed Plan v1 Component 1).

Runs ``dedupe_df`` twice on the same synthetic person-shape df:
  * Baseline: ``prepared_record_store=False`` (in-memory ``_PREP_CACHE`` only).
  * Treatment: ``prepared_record_store=True`` (controller-owned disk store).

Captures per-stage wall via ``goldenmatch.core.bench.bench_capture`` and
peak RSS via ``tracemalloc``. Writes one JSON object per config plus a
``diff`` block so CI can show whether the disk store pays for itself at
the requested row count.

Usage::

    python scripts/bench_prepared_store.py --rows 500000 --out bench.json

Memory-safe up to whatever the controller iteration loop can hold; the
disk-store branch is the point of comparison.

Notes:
  * Synthetic surnames span 10 soundex buckets so blocking + scoring
    don't degenerate — see ``feedback_synthetic_surname_fixtures.md``.
  * Default rows = 500_000 -- big enough that prep wall is measurable,
    small enough to fit in 64GB without per-block memory tricks.
  * ``confidence_required=False`` because RED commits at 100K+ would
    otherwise raise ``ControllerNotConfidentError`` and skip the
    measurement entirely.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tracemalloc
from pathlib import Path
from time import perf_counter

import polars as pl


def build_df(n: int) -> pl.DataFrame:
    """Person-shape synthetic df with surnames across soundex codes."""
    first_names = [
        "Alice", "Bob", "Charlie", "Dana", "Eve", "Frank",
        "Grace", "Henry", "Iris", "Jack",
    ]
    # Ten surnames spanning ten distinct soundex codes so blocking
    # produces realistically-sized buckets rather than one O(N^2) bucket.
    surnames = [
        "Smith",     # S530
        "Johnson",   # J525
        "Williams",  # W452
        "Brown",     # B650
        "Jones",     # J520
        "Garcia",    # G620
        "Miller",    # M460
        "Davis",     # D120
        "Rodriguez", # R362
        "Martinez",  # M635
    ]
    rows = []
    for i in range(n):
        rows.append({
            "first_name": first_names[i % len(first_names)],
            "last_name":  surnames[i % len(surnames)],
            "email":      f"user{i // 3}@example.com",  # ~3 duplicates per email
            "zip":        f"{10000 + (i % 100):05d}",
        })
    return pl.DataFrame(rows)


def run_one(label: str, df: pl.DataFrame, *, prepared_record_store: bool) -> dict:
    """Run dedupe_df once under bench_capture + tracemalloc."""
    import goldenmatch as gm
    from goldenmatch.config.schemas import GoldenMatchConfig
    from goldenmatch.core.bench import bench_capture

    cfg = GoldenMatchConfig(prepared_record_store=prepared_record_store)

    tracemalloc.start()
    t0 = perf_counter()
    with bench_capture() as rec:
        result = gm.dedupe_df(df, config=cfg, confidence_required=False)
    wall = perf_counter() - t0
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    bench = rec.to_dict()
    return {
        "label": label,
        "prepared_record_store": prepared_record_store,
        "rows": df.height,
        "wall_seconds": round(wall, 3),
        "peak_rss_mb": round(peak / (1024 * 1024), 2),
        "clusters": len(result.clusters),
        "stage_timings_seconds": bench["stage_timings_seconds"],
        "metrics": bench["metrics"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=500_000)
    parser.add_argument("--out", type=Path, default=Path("bench_prepared_store.json"))
    parser.add_argument(
        "--store-dir", type=Path, default=None,
        help="Directory for the prepared-record store (defaults to system tmp).",
    )
    args = parser.parse_args(argv)

    # Make sure both runs share the same controller-side conditions.
    os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    if args.store_dir is not None:
        args.store_dir.mkdir(parents=True, exist_ok=True)
        os.environ["GOLDENMATCH_PREPARED_RECORD_STORE_DIR"] = str(args.store_dir)
        os.environ["GOLDENMATCH_PREPARED_RECORD_STORE_PERSIST"] = "1"

    print(f"Building synthetic df ({args.rows:,} rows)...", flush=True)
    df = build_df(args.rows)

    print("Run 1/2: baseline (prepared_record_store=False)...", flush=True)
    baseline = run_one("baseline", df, prepared_record_store=False)
    print(f"  wall = {baseline['wall_seconds']}s; peak = {baseline['peak_rss_mb']} MB", flush=True)

    print("Run 2/2: treatment (prepared_record_store=True)...", flush=True)
    treatment = run_one("treatment", df, prepared_record_store=True)
    print(f"  wall = {treatment['wall_seconds']}s; peak = {treatment['peak_rss_mb']} MB", flush=True)

    wall_delta = baseline["wall_seconds"] - treatment["wall_seconds"]
    rss_delta = baseline["peak_rss_mb"] - treatment["peak_rss_mb"]
    out = {
        "rows": args.rows,
        "baseline": baseline,
        "treatment": treatment,
        "diff": {
            "wall_saved_seconds": round(wall_delta, 3),
            "wall_pct_change": round(
                (-wall_delta / baseline["wall_seconds"]) * 100, 2
            ) if baseline["wall_seconds"] else 0.0,
            "peak_rss_saved_mb": round(rss_delta, 2),
            "peak_rss_pct_change": round(
                (-rss_delta / baseline["peak_rss_mb"]) * 100, 2
            ) if baseline["peak_rss_mb"] else 0.0,
        },
    }
    args.out.write_text(json.dumps(out, indent=2))
    print(f"\nWrote {args.out}.", flush=True)
    print(json.dumps(out["diff"], indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
