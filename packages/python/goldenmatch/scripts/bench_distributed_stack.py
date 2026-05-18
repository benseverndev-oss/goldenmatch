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
import threading
import tracemalloc
from pathlib import Path
from time import perf_counter

import polars as pl


def build_df(n: int) -> pl.DataFrame:
    """Diverse-surname person-shape df with REAL duplicates per email block.

    Prior version (PR #295) was degenerate: each 3-row email block had 3
    fully different person identities (different first AND last names),
    so fuzzy scoring yielded zero pairs within blocks. The 5M bench
    measured two backends spending 24 min on prep + zero scoring.

    Fixed: groups of 3 consecutive rows share email AND have small
    name-typo variants of a base person identity. Within each group:
        row 0: canonical "Alice Smith"
        row 1: "alice smith" (case + same name)
        row 2: "Alicia Smyth" (typo on both)
    This produces ~n/3 real duplicate clusters; fuzzy scoring finds them
    via Jaro-Winkler / token-sort similarity on first_name + last_name.
    """
    base_firsts = [
        "Alice", "Bob", "Charlie", "Dana", "Eve", "Frank",
        "Grace", "Henry", "Iris", "Jack",
    ]
    base_lasts = [
        "Smith", "Johnson", "Williams", "Brown", "Jones",
        "Garcia", "Miller", "Davis", "Rodriguez", "Martinez",
        "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson",
        "Thomas", "Taylor", "Moore", "Jackson", "Martin",
    ]
    # Typo variants applied within each duplicate group (rows 1 and 2 of
    # each 3-row block). Variants drop / change one character so fuzzy
    # scorers can detect similarity but exact scorers cannot.
    def _variant(s: str, kind: int) -> str:
        if kind == 0:
            return s              # canonical
        if kind == 1:
            return s.lower()      # case variant
        # kind == 2: drop or swap one char
        if len(s) > 3:
            return s[0] + s[2:]   # drop second char ("Alice" -> "Aice")
        return s + "e"

    rows = []
    for i in range(n):
        group_id = i // 3            # rows 0,1,2 share a group
        within = i % 3               # variant index 0/1/2
        first_base = base_firsts[group_id % len(base_firsts)]
        last_base = base_lasts[group_id % len(base_lasts)]
        rows.append({
            "first_name": _variant(first_base, within),
            "last_name":  _variant(last_base, within),
            "email":      f"u{group_id}@example.com",  # same per group
            "zip":        f"{10000 + (group_id % 100):05d}",
        })
    return pl.DataFrame(rows)


def _summarize_config(cfg) -> dict:
    """Snapshot the autoconfig'd config so we can see, in the diagnostic
    log, exactly what the controller picked. Without this we can't tell
    whether the disk-store + partitioned-block-scoring flags actually
    activated key-mode dispatch, vs. silently falling back to df-mode."""
    matchkeys = cfg.get_matchkeys() if hasattr(cfg, "get_matchkeys") else []
    blocking_summary = None
    if cfg.blocking is not None:
        blocking_summary = {
            "strategy": cfg.blocking.strategy,
            "max_block_size": getattr(cfg.blocking, "max_block_size", None),
            "skip_oversized": getattr(cfg.blocking, "skip_oversized", None),
            "n_keys": len(cfg.blocking.keys) if cfg.blocking.keys else 0,
            "key_fields": [
                list(k.fields) for k in (cfg.blocking.keys or [])
            ],
        }
    return {
        "backend": cfg.backend,
        "prepared_record_store": getattr(cfg, "prepared_record_store", None),
        "partitioned_block_scoring": getattr(cfg, "partitioned_block_scoring", None),
        "matchkey_count": len(matchkeys),
        "matchkey_types": [mk.type for mk in matchkeys],
        "blocking": blocking_summary,
    }


def _start_heartbeat(label: str, recorder, stop_event: threading.Event) -> threading.Thread:
    """Background thread that flushes a snapshot of the bench recorder
    state + current peak RSS + wall to stdout every 30 seconds.

    The GitHub Actions step log streams stdout in real time, but the
    artifact upload step doesn't run when the runner is hard-killed
    (e.g. by an OOM-killer or fleet-side timeout). Two consecutive 5M
    bench runs died at ~76 min with no artifact and no completion of
    the "Run bench" step -- the streaming log was the only trail. This
    thread ensures that trail always has cumulative state.
    """
    start_wall = perf_counter()

    def _tick():
        import psutil
        proc = psutil.Process()
        while not stop_event.wait(30):
            try:
                rss_mb = proc.memory_info().rss / (1024 * 1024)
                elapsed = perf_counter() - start_wall
                snapshot = recorder.to_dict()
                print(
                    f"[heartbeat {label} t={elapsed:.0f}s rss={rss_mb:.0f}MB] "
                    f"stages={snapshot['stage_timings_seconds']} "
                    f"metrics_keys={list(snapshot['metrics'].keys())}",
                    flush=True,
                )
            except Exception as e:  # noqa: BLE001 -- heartbeat must never crash the bench
                print(f"[heartbeat {label} ERROR: {e}]", flush=True)

    t = threading.Thread(target=_tick, name=f"heartbeat-{label}", daemon=True)
    t.start()
    return t


def run_one(label: str, df: pl.DataFrame, *, backend: str, prepared_record_store: bool, partitioned_block_scoring: bool) -> dict:
    import goldenmatch as gm
    from goldenmatch.core.autoconfig import auto_configure_df
    from goldenmatch.core.bench import bench_capture

    tracemalloc.start()
    t0 = perf_counter()
    stop_event = threading.Event()
    with bench_capture() as rec:
        # Heartbeat must start AFTER bench_capture so it has the
        # context-var recorder; otherwise rec.to_dict() reads an
        # empty default-constructed bag.
        hb = _start_heartbeat(label, rec, stop_event)
        try:
            cfg = auto_configure_df(df, confidence_required=False)
            cfg.backend = backend
            cfg.prepared_record_store = prepared_record_store
            cfg.partitioned_block_scoring = partitioned_block_scoring
            config_snapshot = _summarize_config(cfg)
            print(f"[run_one {label}] calling dedupe_df...", flush=True)
            # Bench reads counts from the bench recorder (metrics-only), so we
            # discard the DedupeResult here. Materializing result.clusters at
            # 5M (1.67M-cluster dict) is catastrophic and was masquerading as
            # a score_buckets hang.
            _ = gm.dedupe_df(df, config=cfg, confidence_required=False)
            print(f"[run_one {label}] dedupe_df returned at t={perf_counter()-t0:.1f}s", flush=True)
        finally:
            stop_event.set()
            hb.join(timeout=2)
    wall = perf_counter() - t0
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # Metrics-only: do NOT touch result.clusters at scale. At 5M with 1.67M
    # clusters, materializing the Python dict (and walking .values() to count
    # multi-members) is catastrophic and was masquerading as a score_buckets
    # hang. Read counts from the bench recorder, which the pipeline already
    # populates via record_metric("cluster_count", ...) and
    # record_metric("multi_member_cluster_count", ...).
    metrics = rec.to_dict()["metrics"]
    return {
        "label": label,
        "backend": backend,
        "prepared_record_store": prepared_record_store,
        "partitioned_block_scoring": partitioned_block_scoring,
        "rows": df.height,
        "wall_seconds": round(wall, 3),
        "peak_rss_mb": round(peak / (1024 * 1024), 2),
        "clusters": metrics.get("cluster_count"),
        "multi_member_clusters": metrics.get("multi_member_cluster_count"),
        "config_snapshot": config_snapshot,
        "stage_timings_seconds": rec.to_dict()["stage_timings_seconds"],
        "metrics": metrics,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=5_000_000)
    parser.add_argument("--out", type=Path, default=Path("bench_distributed_stack.json"))
    parser.add_argument("--store-dir", type=Path, default=None)
    parser.add_argument(
        "--dataset", type=Path, default=None,
        help="Pre-generated Parquet fixture from "
             "scripts/generate_bench_dataset.py. When supplied, skips "
             "build_df() and reads from this path. Required for stable "
             "comparisons across bench runs; the in-script generator "
             "stays as a fallback for ad-hoc local smoke testing.",
    )
    args = parser.parse_args(argv)

    os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    if args.store_dir is not None:
        args.store_dir.mkdir(parents=True, exist_ok=True)
        os.environ["GOLDENMATCH_PREPARED_RECORD_STORE_DIR"] = str(args.store_dir)
        os.environ["GOLDENMATCH_PREPARED_RECORD_STORE_PERSIST"] = "1"

    if args.dataset is not None:
        print(f"Loading pre-generated dataset from {args.dataset}...", flush=True)
        df = pl.read_parquet(args.dataset)
        if df.height != args.rows:
            print(
                f"  note: --rows={args.rows:,} disagrees with dataset "
                f"height ({df.height:,}); using dataset height.",
                flush=True,
            )
    else:
        print(f"Building synthetic df ({args.rows:,} rows) in-script...", flush=True)
        df = build_df(args.rows)

    # Baseline switched from "chunked" -> "bucket" 2026-05-18: the legacy
    # chunked path hangs at 62.99 GB RSS on Linux runners with realistic
    # 1.67M-block fixtures (7 consecutive runs since #296). The bucket
    # backend (added in this PR) replaces the per-block LazyFrame fan-out
    # with a two-level partition_by over an eager prepared df. See
    # goldenmatch/backends/score_buckets.py module docstring for the
    # full rationale + run IDs.
    print("Run 1/2: baseline (backend=bucket)...", flush=True)
    baseline = run_one("baseline", df, backend="bucket", prepared_record_store=False, partitioned_block_scoring=False)
    # Echo the full per-run JSON to stdout so the data is in the workflow
    # logs even when the upload-artifact step is skipped (e.g. when the
    # script exits 1 on FAIL). Critical for post-mortem investigation.
    print(f"  wall = {baseline['wall_seconds']}s; peak = {baseline['peak_rss_mb']} MB", flush=True)
    print("  baseline diagnostic:", flush=True)
    print(json.dumps(baseline, indent=2), flush=True)

    print("Run 2/2: treatment (full stack: ray + store + partitioned)...", flush=True)
    treatment = run_one("treatment", df, backend="ray", prepared_record_store=True, partitioned_block_scoring=True)
    print(f"  wall = {treatment['wall_seconds']}s; peak = {treatment['peak_rss_mb']} MB", flush=True)
    print("  treatment diagnostic:", flush=True)
    print(json.dumps(treatment, indent=2), flush=True)

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
