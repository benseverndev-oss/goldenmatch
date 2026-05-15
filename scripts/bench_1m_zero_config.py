"""Bench gm.dedupe_df(df) zero-config (production caller path, _skip_finalize=True).

Measures:
- 5-run median total wall (no instrumentation overhead).
- One run wrapped in bench_capture() for per-stage attribution (from
  goldenmatch.core.bench, added by PR #239 — replaces the prior
  monkey-patch approach which couldn't catch ``from X import Y`` call-site
  bindings).
- One cProfile pass to surface cross-stage primitives (LazyFrame.collect,
  builtins.max — the hotspots stage-level timing can't see).

Per CLAUDE.md performance-audit lesson: rank stages by measured wall, not
cProfile cumtime. cProfile is the discovery surface for primitives; per-stage
wall (from bench_capture) is the production-realistic attribution.

Usage::

    python scripts/bench_1m_zero_config.py \\
        --fixture .profile_tmp/scale_fixtures/synthetic_500000_dupe15.csv \\
        --runs 5 \\
        --output .profile_tmp/bench_500k.json \\
        --cprofile-output .profile_tmp/bench_500k.prof
"""
from __future__ import annotations

import argparse
import cProfile
import json
import pstats
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import polars as pl


def load_fixture(path: Path) -> pl.DataFrame:
    df = pl.read_csv(path, ignore_errors=True, infer_schema_length=0)
    if "cluster_id" in df.columns:
        df = df.drop("cluster_id")
    return df


def time_dedupe(df: pl.DataFrame) -> float:
    """One bare run of gm.dedupe_df(df) — no instrumentation, just wall."""
    import goldenmatch as gm
    t0 = time.perf_counter()
    _ = gm.dedupe_df(df)
    return time.perf_counter() - t0


def run_with_bench_capture(df: pl.DataFrame) -> tuple[float, dict[str, Any]]:
    """One run wrapped in bench_capture() for per-stage attribution.

    Pipeline-level stages (auto_configure, exact_matching, fuzzy_scoring,
    cluster, golden, identity_resolve) populate via the bench module's
    in-pipeline wrappers added by PR #239. Worker-thread stages add to the
    same recorder under the recorder's lock — safe.
    """
    import goldenmatch as gm
    try:
        from goldenmatch.core.bench import bench_capture  # pyright: ignore[reportMissingImports]
    except ImportError:
        # Older goldenmatch versions don't have core.bench; degrade gracefully.
        wall = time_dedupe(df)
        return wall, {"timings": {}, "metrics": {}, "note": "core.bench unavailable"}

    t0 = time.perf_counter()
    with bench_capture() as bench:
        _ = gm.dedupe_df(df)
    wall = time.perf_counter() - t0
    return wall, bench.to_dict()


def run_with_cprofile(df: pl.DataFrame, out_path: Path) -> tuple[float, list[dict[str, Any]]]:
    import goldenmatch as gm
    prof = cProfile.Profile()
    t0 = time.perf_counter()
    prof.enable()
    _ = gm.dedupe_df(df)
    prof.disable()
    wall = time.perf_counter() - t0
    prof.dump_stats(str(out_path))

    stats = pstats.Stats(prof).strip_dirs().sort_stats("cumulative")
    raw = getattr(stats, "stats", {})
    top: list[dict[str, Any]] = []
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


def _verify_install(verbose: bool) -> dict[str, str]:
    """Confirm goldenmatch resolves to the worktree, not site-packages.

    Returns paths for diagnostics. The CI workflow's `pip install -e` step
    keeps site-packages out of the picture, but local runs can silently
    measure the wrong code (per CLAUDE.md "shadows worktree" gotcha) — this
    surfaces it.
    """
    import goldenmatch
    import goldenmatch._api
    import goldenmatch.core.pipeline
    paths = {
        "goldenmatch.__file__":             str(goldenmatch.__file__),
        "goldenmatch._api.__file__":        str(goldenmatch._api.__file__),
        "goldenmatch.core.pipeline.__file__": str(goldenmatch.core.pipeline.__file__),
    }
    if verbose:
        print("install paths:")
        for k, v in paths.items():
            print(f"  {k} = {v}")
    return paths


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--fixture",
        type=Path,
        default=Path(".profile_tmp/scale_fixtures/synthetic_500000_dupe15.csv"),
    )
    ap.add_argument("--runs", type=int, default=5,
                    help="wall-only runs for the median (default 5)")
    ap.add_argument(
        "--output",
        type=Path,
        default=Path(".profile_tmp/bench_zero_config.json"),
    )
    ap.add_argument(
        "--cprofile-output",
        type=Path,
        default=Path(".profile_tmp/bench_zero_config.prof"),
    )
    ap.add_argument("--skip-cprofile", action="store_true",
                    help="skip the cProfile pass (~20-30%% overhead)")
    ap.add_argument("--skip-stages", action="store_true",
                    help="skip the bench_capture() per-stage run")
    ap.add_argument("--ref", default=None,
                    help="git ref this bench is measuring (logged into the JSON; "
                         "set by CI to keep results traceable)")
    args = ap.parse_args()

    if not args.fixture.exists():
        sys.exit(f"fixture not found: {args.fixture}")

    args.output.parent.mkdir(parents=True, exist_ok=True)

    install_paths = _verify_install(verbose=True)

    print(f"Loading {args.fixture}...")
    df = load_fixture(args.fixture)
    print(f"  {df.height:,} rows x {df.width} cols")

    # 1) Wall-only median.
    walls: list[float] = []
    for i in range(args.runs):
        print(f"[wall run {i+1}/{args.runs}] starting...")
        wall = time_dedupe(df)
        walls.append(wall)
        print(f"  wall={wall:.2f}s")

    median = statistics.median(walls)
    print(f"\nmedian wall ({args.runs} runs): {median:.2f}s  "
          f"({min(walls):.2f}-{max(walls):.2f}s)")

    # 2) bench_capture() per-stage run.
    stage_dict: dict[str, Any] = {}
    stage_wall: float | None = None
    if not args.skip_stages:
        print("\n[stages run] starting (bench_capture)...")
        stage_wall, stage_dict = run_with_bench_capture(df)
        timings = stage_dict.get("timings", {})
        print(f"  wall={stage_wall:.2f}s  ({len(timings)} stages recorded)")
        for name, secs in sorted(timings.items(), key=lambda kv: -kv[1]):
            pct = 100.0 * secs / stage_wall if stage_wall else 0.0
            print(f"  {name:<40s} {secs:>8.2f}s  ({pct:5.1f}%)")
        metrics = stage_dict.get("metrics", {})
        if metrics:
            print("  metrics:")
            for k, v in sorted(metrics.items()):
                print(f"    {k} = {v}")

    # 3) cProfile pass.
    cprofile_top: list[dict[str, Any]] = []
    cprofile_wall: float | None = None
    if not args.skip_cprofile:
        print(f"\n[cprofile run] starting (output -> {args.cprofile_output})...")
        cprofile_wall, cprofile_top = run_with_cprofile(df, args.cprofile_output)
        print(f"  wall={cprofile_wall:.2f}s (cProfile adds ~20-30%% overhead)")
        print("  top 10 by cumtime:")
        for row in cprofile_top[:10]:
            print(f"    {row['cumtime_s']:>8.2f}s  "
                  f"ncalls={row['ncalls']:>10d}  {row['func']}")

    report = {
        "fixture":                       str(args.fixture),
        "ref":                           args.ref,
        "install_paths":                 install_paths,
        "n_rows":                        df.height,
        "n_cols":                        df.width,
        "runs":                          args.runs,
        "wall_seconds_runs":             [round(w, 3) for w in walls],
        "wall_seconds_median":           round(median, 3),
        "wall_seconds_min":              round(min(walls), 3),
        "wall_seconds_max":              round(max(walls), 3),
        "stage_breakdown_wall_seconds": stage_wall,
        "stage_breakdown":               stage_dict,    # {timings, metrics}
        "cprofile_wall_seconds":         cprofile_wall,
        "cprofile_top":                  cprofile_top,
        "cprofile_dump": (
            str(args.cprofile_output) if not args.skip_cprofile else None
        ),
    }
    args.output.write_text(json.dumps(report, indent=2))
    print(f"\nwrote {args.output}")


if __name__ == "__main__":
    main()
