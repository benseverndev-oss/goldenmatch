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
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Lane model (spec 4): a lane is {name, script, mode, env}. The sweep iterates
# lanes x shapes x scales. Lane env is applied PER SUBPROCESS only -- never to
# the orchestrator's own os.environ (spec 4 hard constraint), or the numpy FS
# lane's GOLDENMATCH_FS_NATIVE=0 would leak into the native/zeroconfig lanes.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Lane:
    name: str
    script: str
    mode: str | None = None
    env: dict[str, str] = field(default_factory=dict)


# The four run_goldenmatch.py-backed GM lanes: run_goldenmatch.py calls
# native_enabled("block_scoring") for EVERY mode above the mode branch, which
# RAISES under the default require-native when the kernel is absent -- so all
# four need --allow-pure-python locally (CI builds native and passes False).
_GM_RUN_LANES = {"gm_hand_built", "gm_probabilistic",
                 "gm_probabilistic_native", "gm_zeroconfig"}

LANES: dict[str, Lane] = {
    "splink": Lane("splink", "run_splink.py"),
    "gm_hand_built": Lane("gm_hand_built", "run_goldenmatch.py", mode="hand_built"),
    "gm_probabilistic": Lane("gm_probabilistic", "run_goldenmatch.py",
                             mode="probabilistic", env={"GOLDENMATCH_FS_NATIVE": "0"}),
    "gm_probabilistic_native": Lane("gm_probabilistic_native", "run_goldenmatch.py",
                                    mode="probabilistic", env={"GOLDENMATCH_FS_NATIVE": "1"}),
    "gm_zeroconfig": Lane("gm_zeroconfig", "run_goldenmatch.py", mode="zeroconfig"),
    "gm_converted_splink": Lane("gm_converted_splink", "run_gm_converted.py"),
}


def lane_env(lane: Lane) -> dict[str, str]:
    """A NEW env dict = parent env overlaid with the lane's extra env. NEVER
    mutates os.environ (spec 4 hard constraint)."""
    return {**os.environ, **lane.env}


def build_cmd(lane: Lane, *, input, rows: int, out, pred, threshold: float,
              shape: str, allow_pure_python: bool = False) -> list[str]:
    """Build the subprocess argv for one datapoint of `lane`."""
    cmd = [sys.executable, str(HERE / lane.script),
           "--input", str(input), "--rows", str(rows),
           "--out", str(out), "--pred-out", str(pred),
           "--threshold", str(threshold), "--shape", shape]
    if lane.mode:
        cmd += ["--mode", lane.mode]
    # Only the run_goldenmatch.py-backed lanes accept --allow-pure-python; the
    # native gate raises for ALL its modes, not just hand_built.
    if allow_pure_python and lane.name in _GM_RUN_LANES:
        cmd.append("--allow-pure-python")
    return cmd

# Per-scale subprocess wall-clock cap (seconds). Raised after the first run so a
# slow-but-progressing datapoint isn't cut off; a true hang still ends eventually.
TIMEOUT_BY_ROWS = [
    (100_000, 900),
    (1_000_000, 2400),
    (5_000_000, 7200),
    (25_000_000, 14400),
    (100_000_000, 28800),
]


def _timeout_for(rows: int) -> int:
    for ceiling, t in TIMEOUT_BY_ROWS:
        if rows <= ceiling:
            return t
    return 28800


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
               threshold: float, allow_pure_python: bool = False) -> tuple[dict, Path]:
    out = results_dir / f"{engine}_{rows}.json"
    pred = results_dir / f"{engine}_{rows}.pred.parquet"
    for stale in (out, pred):
        if stale.exists():
            stale.unlink()  # a stale artifact must not masquerade as fresh
    runner = HERE / (f"run_{engine}.py")
    cmd = [sys.executable, str(runner), "--input", str(fixture),
           "--rows", str(rows), "--out", str(out), "--pred-out", str(pred),
           "--threshold", str(threshold)]
    if engine == "goldenmatch" and allow_pure_python:
        cmd.append("--allow-pure-python")  # local smoke only; CI builds native
    t0 = time.perf_counter()
    rc, how = _run(cmd, _timeout_for(rows))
    wall = round(time.perf_counter() - t0, 1)
    res = _load_or_synthesize(out, rc, how, engine, rows)
    res["orchestrator_wall_seconds"] = wall
    return res, pred


def evaluate_datapoint(pred: Path, truth: Path, results_dir: Path, engine: str, rows: int) -> dict | None:
    """Score a prediction parquet against truth in a separate (bounded) process."""
    if not pred.exists() or not truth.exists():
        return None
    metrics_out = results_dir / f"{engine}_{rows}.metrics.json"
    rc, _ = _run(
        [sys.executable, str(HERE / "evaluate.py"),
         "--pred", str(pred), "--truth", str(truth), "--out", str(metrics_out)],
        timeout=_timeout_for(rows),
    )
    if metrics_out.exists():
        try:
            return json.loads(metrics_out.read_text())
        except Exception:
            return None
    return None


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.1f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def _r(v) -> str:
    """3-decimal formatter for ratio metrics (F1/precision/recall)."""
    return f"{v:.3f}" if isinstance(v, (int, float)) else "—"


def render_markdown(results: list[dict], dupe_rate: float) -> str:
    lines = [
        "# ER head-to-head: Splink (DuckDB) vs GoldenMatch (bucket+native+arrow)",
        "",
        f"Single machine, identical parquet fixture per scale, dupe-rate={dupe_rate}. "
        "Wall is end-to-end dedupe (Splink: train+predict+cluster; GoldenMatch: "
        "auto_configure+dedupe). Peak RSS is the per-process high-water mark. "
        "`scored pairs` is recorded so blocking-aggressiveness differences are visible.",
        "",
        "| rows | engine | status | dedupe wall (s) | peak RSS (MB) | scored pairs | clusters | pairs/sec | pairwise F1 | B³ F1 |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in sorted(results, key=lambda x: (x["rows_requested"], x["engine"])):
        wall = r.get("dedupe_wall_seconds")
        pairs = r.get("scored_pairs")
        pps = round(pairs / wall) if (pairs and wall) else None
        acc = r.get("accuracy") or {}
        pw = acc.get("pairwise") or {}
        bc = acc.get("bcubed") or {}
        lines.append(
            "| " + " | ".join([
                _fmt(r["rows_requested"]), r["engine"], r.get("status", "?"),
                _fmt(wall), _fmt(r.get("peak_rss_mb")), _fmt(pairs),
                _fmt(r.get("cluster_count")), _fmt(pps),
                _r(pw.get("f1")), _r(bc.get("f1")),
            ]) + " |"
        )

    # Accuracy detail: pairwise P/R + B³ P/R + confusion matrix.
    lines += ["", "## Accuracy (vs ground truth)", "",
              "| rows | engine | pw P | pw R | pw F1 | B³ P | B³ R | B³ F1 | TP | FP | FN |",
              "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in sorted(results, key=lambda x: (x["rows_requested"], x["engine"])):
        acc = r.get("accuracy")
        if not acc:
            continue
        pw, bc, cm = acc["pairwise"], acc["bcubed"], acc["pairwise"]["confusion"]
        lines.append("| " + " | ".join([
            _fmt(r["rows_requested"]), r["engine"],
            _r(pw["precision"]), _r(pw["recall"]), _r(pw["f1"]),
            _r(bc["precision"]), _r(bc["recall"]), _r(bc["f1"]),
            _fmt(cm["tp"]), _fmt(cm["fp"]), _fmt(cm["fn"]),
        ]) + " |")
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
                    default=[100_000, 1_000_000, 5_000_000, 25_000_000])
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

        truth = fixtures / f"bench_{rows}.truth.parquet"
        for engine in args.engines:
            print(f"[orchestrate] === {engine} @ {rows:,} rows ===")
            res, pred = run_engine(engine, fixture, rows, results_dir, args.threshold,
                                   allow_pure_python=args.allow_pure_python)
            # Accuracy eval runs here, BEFORE fixture/pred cleanup below.
            acc = evaluate_datapoint(pred, truth, results_dir, engine, rows)
            if acc is not None:
                res["accuracy"] = acc
            pred.unlink(missing_ok=True)
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
