#!/usr/bin/env python
"""Orchestrator for the ER head-to-head scaling benchmark (Splink vs GoldenMatch).

Memory safety is the whole point: this process NEVER loads a fixture. For each
(scale, engine) it spawns an isolated subprocess; the OS reclaims that datapoint's
entire memory footprint on exit. If a datapoint is OOM-killed (SIGKILL leaves no
JSON behind), we synthesize an `OOM` result and keep going — so the 100M tier,
which is expected to exceed a single 64 GB box for the in-memory bucket backend,
produces an honest "ceiling" datapoint instead of aborting the run.

Only small JSON results ever live in this process. Output: an aggregate
`bench_results.json` + `summary.md`, also appended to $GITHUB_STEP_SUMMARY.

Usage:
    python orchestrate.py --scales 100000 1000000 5000000 25000000 100000000 \
        --engines goldenmatch splink --workdir .bench_er --dupe-rate 0.20
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Per-scale subprocess wall-clock cap (seconds). Generous; a real hang still ends.
TIMEOUT_BY_ROWS = [
    (1_000_000, 1800),
    (5_000_000, 3600),
    (25_000_000, 10800),
    (100_000_000, 21600),
]


def _timeout_for(rows: int) -> int:
    for ceiling, t in TIMEOUT_BY_ROWS:
        if rows <= ceiling:
            return t
    return 21600


def _run(cmd: list[str], timeout: int) -> tuple[int, str]:
    """Run a subprocess to completion; classify how it ended. Never raises."""
    try:
        proc = subprocess.run(cmd, timeout=timeout)
        return proc.returncode, "exited"
    except subprocess.TimeoutExpired:
        return -1, "timeout"


def _load_or_synthesize(path: Path, returncode: int, how: str, engine: str, rows: int) -> dict:
    if path.exists():
        try:
            r = json.loads(path.read_text())
            r.setdefault("returncode", returncode)
            return r
        except Exception:
            pass
    # No JSON => the child died before its finally block could write (SIGKILL/OOM).
    status = "timeout" if how == "timeout" else ("OOM" if returncode in (-9, 137) else "killed")
    return {
        "engine": engine,
        "rows_requested": rows,
        "status": status,
        "returncode": returncode,
        "note": "no result file written — process terminated by OS (likely OOM) or timed out",
    }


def generate(rows: int, dupe_rate: float, fixtures: Path) -> Path:
    out = fixtures / f"bench_{rows}.parquet"
    truth = fixtures / f"bench_{rows}.truth.parquet"
    if out.exists():
        print(f"[orchestrate] fixture exists, reusing {out}")
        return out
    print(f"[orchestrate] generating {rows:,} rows -> {out}")
    subprocess.run(
        [sys.executable, str(HERE / "generate_fixture.py"),
         "--rows", str(rows), "--dupe-rate", str(dupe_rate),
         "--out", str(out), "--ground-truth", str(truth)],
        check=True, timeout=_timeout_for(rows),
    )
    return out


def run_engine(engine: str, fixture: Path, rows: int, results_dir: Path,
               threshold: float, allow_pure_python: bool = False) -> dict:
    out = results_dir / f"{engine}_{rows}.json"
    if out.exists():
        out.unlink()  # stale result from a prior run must not masquerade as fresh
    runner = HERE / (f"run_{engine}.py")
    cmd = [sys.executable, str(runner), "--input", str(fixture),
           "--rows", str(rows), "--out", str(out), "--threshold", str(threshold)]
    if engine == "goldenmatch" and allow_pure_python:
        cmd.append("--allow-pure-python")  # local smoke only; CI builds native
    t0 = time.perf_counter()
    rc, how = _run(cmd, _timeout_for(rows))
    wall = round(time.perf_counter() - t0, 1)
    res = _load_or_synthesize(out, rc, how, engine, rows)
    res["orchestrator_wall_seconds"] = wall
    return res


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.1f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def render_markdown(results: list[dict], dupe_rate: float) -> str:
    lines = [
        "# ER head-to-head: Splink (DuckDB) vs GoldenMatch (bucket+native+arrow)",
        "",
        f"Single machine, identical parquet fixture per scale, dupe-rate={dupe_rate}. "
        "Wall is end-to-end dedupe (Splink: train+predict+cluster; GoldenMatch: "
        "auto_configure+dedupe). Peak RSS is the per-process high-water mark. "
        "`scored pairs` is recorded so blocking-aggressiveness differences are visible.",
        "",
        "| rows | engine | status | dedupe wall (s) | peak RSS (MB) | scored pairs | clusters | pairs/sec |",
        "|---:|---|---|---:|---:|---:|---:|---:|",
    ]
    for r in sorted(results, key=lambda x: (x["rows_requested"], x["engine"])):
        wall = r.get("dedupe_wall_seconds")
        pairs = r.get("scored_pairs")
        pps = round(pairs / wall) if (pairs and wall) else None
        lines.append(
            "| " + " | ".join([
                _fmt(r["rows_requested"]), r["engine"], r.get("status", "?"),
                _fmt(wall), _fmt(r.get("peak_rss_mb")), _fmt(pairs),
                _fmt(r.get("cluster_count")), _fmt(pps),
            ]) + " |"
        )
    # Per-scale head-to-head deltas (only where both engines produced a wall).
    lines += ["", "## Head-to-head (where both completed)", ""]
    by_rows: dict[int, dict[str, dict]] = {}
    for r in results:
        by_rows.setdefault(r["rows_requested"], {})[r["engine"]] = r
    lines.append("| rows | GoldenMatch wall | Splink wall | wall ratio (GM/Splink) | GM RSS | Splink RSS |")
    lines.append("|---:|---:|---:|---:|---:|---:|")
    for rows in sorted(by_rows):
        gm = by_rows[rows].get("goldenmatch", {})
        sp = by_rows[rows].get("splink", {})
        gw, sw = gm.get("dedupe_wall_seconds"), sp.get("dedupe_wall_seconds")
        ratio = round(gw / sw, 2) if (gw and sw) else None
        lines.append("| " + " | ".join([
            _fmt(rows), _fmt(gw), _fmt(sw), _fmt(ratio),
            _fmt(gm.get("peak_rss_mb")), _fmt(sp.get("peak_rss_mb")),
        ]) + " |")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scales", type=int, nargs="+",
                    default=[100_000, 1_000_000, 5_000_000, 25_000_000, 100_000_000])
    ap.add_argument("--engines", nargs="+", default=["goldenmatch", "splink"])
    ap.add_argument("--workdir", type=Path, default=Path(".bench_er"))
    ap.add_argument("--dupe-rate", type=float, default=0.20)
    ap.add_argument("--threshold", type=float, default=0.85)
    ap.add_argument("--keep-fixtures", action="store_true",
                    help="don't delete each fixture after its engines run (uses more disk)")
    ap.add_argument("--allow-pure-python", action="store_true",
                    help="LOCAL SMOKE ONLY: let GoldenMatch run without the native "
                         "Arrow runtime. Never pass this in CI — it invalidates the "
                         "'optimized backend' claim.")
    args = ap.parse_args()

    fixtures = args.workdir / "fixtures"
    results_dir = args.workdir / "results"
    fixtures.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    all_results: list[dict] = []
    for rows in args.scales:
        try:
            fixture = generate(rows, args.dupe_rate, fixtures)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            for engine in args.engines:
                all_results.append({"engine": engine, "rows_requested": rows,
                                    "status": "fixture_failed", "error": str(e)})
            continue

        for engine in args.engines:
            print(f"[orchestrate] === {engine} @ {rows:,} rows ===")
            res = run_engine(engine, fixture, rows, results_dir, args.threshold,
                             allow_pure_python=args.allow_pure_python)
            all_results.append(res)
            # Flush the aggregate after EVERY datapoint so a later OOM can't lose
            # earlier results.
            (args.workdir / "bench_results.json").write_text(json.dumps(all_results, indent=2))

        if not args.keep_fixtures:
            for f in fixtures.glob(f"bench_{rows}.*"):
                f.unlink(missing_ok=True)

    md = render_markdown(all_results, args.dupe_rate)
    (args.workdir / "summary.md").write_text(md)
    print(md)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a") as fh:
            fh.write(md)


if __name__ == "__main__":
    main()
