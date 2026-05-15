"""Bench gm.dedupe_df(df) zero-config (production path, _skip_finalize=True).

Measures total wall (5-run median) + one cProfile pass + per-stage wall via
monkey-patched timing around the pipeline's major entry points. Targets the
post-controller full-df work — the stage that real `gm.dedupe_df(big_df)`
callers pay for, after the controller commits a config and skips finalize.

Per CLAUDE.md performance-audit lesson: rank stages by measured wall, not
cProfile cumtime (threaded sections lie about cumulative time).

Usage:
    python scripts/bench_1m_zero_config.py \\
        --fixture .profile_tmp/scale_fixtures/synthetic_1000000_dupe15.csv \\
        --runs 5 \\
        --output .profile_tmp/bench_1m_zero_config.json
"""
from __future__ import annotations

import argparse
import cProfile
import json
import pstats
import statistics
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import polars as pl


# ── Per-stage timing harness ────────────────────────────────────────────────

class StageTimer:
    """Collects (stage_name, wall_seconds) entries via monkey-patched wrappers."""

    def __init__(self) -> None:
        self.events: list[tuple[str, float]] = []
        self._patches: list[tuple[Any, str, Any]] = []

    @contextmanager
    def time(self, name: str):
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.events.append((name, time.perf_counter() - t0))

    def patch(self, module: Any, attr: str, label: str) -> None:
        original = getattr(module, attr)

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            t0 = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                self.events.append((label, time.perf_counter() - t0))

        setattr(module, attr, wrapper)
        self._patches.append((module, attr, original))

    def restore(self) -> None:
        for module, attr, original in self._patches:
            setattr(module, attr, original)
        self._patches.clear()

    def aggregate(self) -> dict[str, dict[str, float]]:
        """Group by stage name, return {name: {total_s, calls, mean_s}}."""
        agg: dict[str, list[float]] = {}
        for name, dt in self.events:
            agg.setdefault(name, []).append(dt)
        return {
            name: {
                "total_s": round(sum(times), 4),
                "calls": len(times),
                "mean_s": round(statistics.mean(times), 6),
            }
            for name, times in sorted(
                agg.items(), key=lambda kv: -sum(kv[1])
            )
        }


def install_stage_patches(timer: StageTimer) -> None:
    """Wrap the major _run_dedupe_pipeline entry points for wall-time capture.

    Patched at module level so the per-stage timing reflects what the
    production pipeline actually calls. Restore via timer.restore().
    """
    from goldenmatch.core import (
        blocker,
        cluster,
        matchkey,
        pipeline,
        scorer,
    )
    # Stage entry points worth attributing — chosen by hypothesis (caller
    # named precompute_matchkey_transforms, phone_e164, build_learned_blocks).
    # Wide net so we don't miss a surprise hotspot.
    targets = [
        (matchkey, "compute_matchkeys",              "compute_matchkeys"),
        (matchkey, "precompute_matchkey_transforms", "precompute_matchkey_transforms"),
        (blocker,  "build_blocks",                   "build_blocks"),
        (scorer,   "find_exact_matches",             "find_exact_matches"),
        (scorer,   "score_blocks_parallel",          "score_blocks_parallel"),
        (cluster,  "build_clusters",                 "build_clusters"),
    ]
    for module, attr, label in targets:
        if hasattr(module, attr):
            timer.patch(module, attr, label)

    # Optional: domain extraction, golden, auto-fix — only if present.
    try:
        from goldenmatch.core import golden
        if hasattr(golden, "build_golden_records"):
            timer.patch(golden, "build_golden_records", "build_golden_records")
    except ImportError:
        pass

    try:
        from goldenmatch.core import autofix
        if hasattr(autofix, "auto_fix_dataframe"):
            timer.patch(autofix, "auto_fix_dataframe", "auto_fix_dataframe")
    except ImportError:
        pass

    # Pipeline-level transform stages — try to patch where they live.
    if hasattr(pipeline, "_apply_domain_extraction"):
        timer.patch(pipeline, "_apply_domain_extraction", "_apply_domain_extraction")


# ── Bench harness ───────────────────────────────────────────────────────────

def load_fixture(path: Path) -> pl.DataFrame:
    df = pl.read_csv(path, ignore_errors=True, infer_schema_length=0)
    if "cluster_id" in df.columns:
        df = df.drop("cluster_id")
    return df


def time_dedupe(df: pl.DataFrame) -> float:
    import goldenmatch as gm
    t0 = time.perf_counter()
    _ = gm.dedupe_df(df)
    return time.perf_counter() - t0


def run_with_stages(df: pl.DataFrame) -> tuple[float, dict]:
    timer = StageTimer()
    install_stage_patches(timer)
    try:
        wall = time_dedupe(df)
    finally:
        timer.restore()
    return wall, timer.aggregate()


def run_with_cprofile(df: pl.DataFrame, out_path: Path) -> tuple[float, list[dict]]:
    import goldenmatch as gm
    prof = cProfile.Profile()
    t0 = time.perf_counter()
    prof.enable()
    _ = gm.dedupe_df(df)
    prof.disable()
    wall = time.perf_counter() - t0
    prof.dump_stats(str(out_path))
    stats = pstats.Stats(prof).strip_dirs().sort_stats("cumulative")
    # Top 30 by cumtime, in dict form. pstats.Stats.stats is documented but
    # not in the type stubs; access via getattr to keep pyright quiet.
    raw = getattr(stats, "stats", {})
    top: list[dict] = []
    for func, entry in raw.items():
        # entry = (cc, nc, tt, ct, callers)
        _cc, nc, tt, ct, _callers = entry
        filename, lineno, name = func
        top.append({
            "func": f"{Path(filename).name}:{lineno}:{name}",
            "ncalls": nc,
            "tottime_s": round(tt, 4),
            "cumtime_s": round(ct, 4),
        })
    top.sort(key=lambda r: -r["cumtime_s"])
    return wall, top[:30]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--fixture",
        type=Path,
        default=Path(".profile_tmp/scale_fixtures/synthetic_1000000_dupe15.csv"),
    )
    ap.add_argument("--runs", type=int, default=5,
                    help="number of wall-only runs for the median (default 5)")
    ap.add_argument(
        "--output",
        type=Path,
        default=Path(".profile_tmp/bench_1m_zero_config.json"),
    )
    ap.add_argument(
        "--cprofile-output",
        type=Path,
        default=Path(".profile_tmp/bench_1m_zero_config.prof"),
    )
    ap.add_argument("--skip-cprofile", action="store_true",
                    help="skip the cProfile pass (~20-30% overhead)")
    ap.add_argument("--skip-stages", action="store_true",
                    help="skip the per-stage timed run")
    args = ap.parse_args()

    if not args.fixture.exists():
        sys.exit(f"fixture not found: {args.fixture}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    print(f"Loading {args.fixture}...")
    df = load_fixture(args.fixture)
    print(f"  {df.height:,} rows × {df.width} cols")

    # 1) Wall-only median.
    walls: list[float] = []
    for i in range(args.runs):
        print(f"[wall run {i+1}/{args.runs}] starting...")
        wall = time_dedupe(df)
        walls.append(wall)
        print(f"  wall={wall:.2f}s")

    median = statistics.median(walls)
    print(f"\nmedian wall ({args.runs} runs): {median:.2f}s  ({min(walls):.2f}-{max(walls):.2f}s)")

    # 2) Per-stage timed run.
    stage_breakdown: dict = {}
    stage_wall: float | None = None
    if not args.skip_stages:
        print("\n[stages run] starting (with monkey-patched timing)...")
        stage_wall, stage_breakdown = run_with_stages(df)
        print(f"  wall={stage_wall:.2f}s")
        for name, info in stage_breakdown.items():
            pct = 100.0 * info["total_s"] / stage_wall if stage_wall else 0.0
            print(f"  {name:<40s} {info['total_s']:>8.2f}s  ({pct:5.1f}%)  calls={info['calls']}")

    # 3) cProfile pass.
    cprofile_top: list[dict] = []
    cprofile_wall: float | None = None
    if not args.skip_cprofile:
        print(f"\n[cprofile run] starting (output -> {args.cprofile_output})...")
        cprofile_wall, cprofile_top = run_with_cprofile(df, args.cprofile_output)
        print(f"  wall={cprofile_wall:.2f}s (cProfile adds ~20-30% overhead)")
        print("  top 10 by cumtime:")
        for row in cprofile_top[:10]:
            print(f"    {row['cumtime_s']:>8.2f}s  ncalls={row['ncalls']:>10d}  {row['func']}")

    report = {
        "fixture": str(args.fixture),
        "n_rows": df.height,
        "n_cols": df.width,
        "runs": args.runs,
        "wall_seconds_runs": [round(w, 3) for w in walls],
        "wall_seconds_median": round(median, 3),
        "wall_seconds_min": round(min(walls), 3),
        "wall_seconds_max": round(max(walls), 3),
        "stage_breakdown_wall_seconds": stage_wall,
        "stage_breakdown": stage_breakdown,
        "cprofile_wall_seconds": cprofile_wall,
        "cprofile_top": cprofile_top,
        "cprofile_dump": str(args.cprofile_output) if not args.skip_cprofile else None,
    }
    args.output.write_text(json.dumps(report, indent=2))
    print(f"\nwrote {args.output}")


if __name__ == "__main__":
    main()
