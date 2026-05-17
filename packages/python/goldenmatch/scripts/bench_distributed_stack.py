"""5M end-to-end bench comparing today's chunked backend against the full
Component 1+2+3 stack (prepared_record_store + partitioned_block_scoring
+ backend=ray).

Kill criterion: stack must show >= 20% wall AND >= 20% peak RSS
improvement vs chunked or PRs #280-#283 + #287 + Component 3 PRs revert
per project_distributed_plan_v1_kill_criterion.
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
    """Diverse-surname person-shape df. Per feedback_synthetic_surname_fixtures
    each surname spans its own soundex bucket so blocking doesn't degenerate
    into one O(N^2) block."""
    surnames = [
        "Smith", "Johnson", "Williams", "Brown", "Jones",
        "Garcia", "Miller", "Davis", "Rodriguez", "Martinez",
        "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson",
        "Thomas", "Taylor", "Moore", "Jackson", "Martin",
    ]
    first_names = [
        "Alice", "Bob", "Charlie", "Dana", "Eve", "Frank",
        "Grace", "Henry", "Iris", "Jack",
    ]
    rows = []
    for i in range(n):
        rows.append({
            "first_name": first_names[i % len(first_names)],
            "last_name":  surnames[i % len(surnames)],
            "email":      f"u{i // 3}@example.com",
            "zip":        f"{10000 + (i % 100):05d}",
        })
    return pl.DataFrame(rows)


def run_one(label: str, df: pl.DataFrame, *, backend: str, prepared_record_store: bool, partitioned_block_scoring: bool) -> dict:
    import goldenmatch as gm
    from goldenmatch.core.autoconfig import auto_configure_df
    from goldenmatch.core.bench import bench_capture

    tracemalloc.start()
    t0 = perf_counter()
    with bench_capture() as rec:
        cfg = auto_configure_df(df, confidence_required=False)
        cfg.backend = backend
        cfg.prepared_record_store = prepared_record_store
        cfg.partitioned_block_scoring = partitioned_block_scoring
        result = gm.dedupe_df(df, config=cfg, confidence_required=False)
    wall = perf_counter() - t0
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    return {
        "label": label,
        "backend": backend,
        "prepared_record_store": prepared_record_store,
        "partitioned_block_scoring": partitioned_block_scoring,
        "rows": df.height,
        "wall_seconds": round(wall, 3),
        "peak_rss_mb": round(peak / (1024 * 1024), 2),
        "clusters": len(result.clusters),
        "stage_timings_seconds": rec.to_dict()["stage_timings_seconds"],
        "metrics": rec.to_dict()["metrics"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=5_000_000)
    parser.add_argument("--out", type=Path, default=Path("bench_distributed_stack.json"))
    parser.add_argument("--store-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    if args.store_dir is not None:
        args.store_dir.mkdir(parents=True, exist_ok=True)
        os.environ["GOLDENMATCH_PREPARED_RECORD_STORE_DIR"] = str(args.store_dir)
        os.environ["GOLDENMATCH_PREPARED_RECORD_STORE_PERSIST"] = "1"

    print(f"Building synthetic df ({args.rows:,} rows)...", flush=True)
    df = build_df(args.rows)

    print("Run 1/2: baseline (backend=chunked)...", flush=True)
    baseline = run_one("baseline", df, backend="chunked", prepared_record_store=False, partitioned_block_scoring=False)
    print(f"  wall = {baseline['wall_seconds']}s; peak = {baseline['peak_rss_mb']} MB", flush=True)

    print("Run 2/2: treatment (full stack: ray + store + partitioned)...", flush=True)
    treatment = run_one("treatment", df, backend="ray", prepared_record_store=True, partitioned_block_scoring=True)
    print(f"  wall = {treatment['wall_seconds']}s; peak = {treatment['peak_rss_mb']} MB", flush=True)

    wall_delta = baseline["wall_seconds"] - treatment["wall_seconds"]
    rss_delta = baseline["peak_rss_mb"] - treatment["peak_rss_mb"]
    wall_pct = (-wall_delta / baseline["wall_seconds"]) * 100 if baseline["wall_seconds"] else 0.0
    rss_pct = (-rss_delta / baseline["peak_rss_mb"]) * 100 if baseline["peak_rss_mb"] else 0.0

    # Kill criterion check.
    KILL_THRESHOLD_PCT = -20.0  # negative because pct_change is the "treatment relative to baseline" signed delta
    kill_verdict = "PASS" if (wall_pct <= KILL_THRESHOLD_PCT and rss_pct <= KILL_THRESHOLD_PCT) else "FAIL"

    out = {
        "rows": args.rows,
        "baseline": baseline,
        "treatment": treatment,
        "diff": {
            "wall_saved_seconds": round(wall_delta, 3),
            "wall_pct_change": round(wall_pct, 2),
            "peak_rss_saved_mb": round(rss_delta, 2),
            "peak_rss_pct_change": round(rss_pct, 2),
        },
        "kill_criterion": {
            "threshold_pct": KILL_THRESHOLD_PCT,
            "verdict": kill_verdict,
            "note": "PASS = both wall and peak RSS improved by >= 20% (Component 1+2+3 stack stays). FAIL = revert PRs #280-#283 + #287 + Component 3 PRs.",
        },
    }
    args.out.write_text(json.dumps(out, indent=2))
    print(f"\nWrote {args.out}.", flush=True)
    print(json.dumps(out["diff"], indent=2), flush=True)
    print(f"\nKill criterion: {kill_verdict}", flush=True)
    # Non-zero exit on FAIL so the workflow run status surfaces the
    # verdict (visible in the GitHub Actions UI without opening the
    # artifact). PASS exits 0.
    return 0 if kill_verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
