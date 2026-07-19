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
# NOTE: keep top-level imports stdlib-only -- merge_results.py imports this module's render_markdown() in a dependency-free CI job (no uv sync).
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
    extra_args: tuple[str, ...] = ()


# The four run_goldenmatch.py-backed GM lanes: run_goldenmatch.py calls
# native_enabled("block_scoring") for EVERY mode above the mode branch, which
# RAISES under the default require-native when the kernel is absent -- so all
# four need --allow-pure-python locally (CI builds native and passes False).
_GM_RUN_LANES = {"gm_hand_built", "gm_probabilistic",
                 "gm_probabilistic_native", "gm_zeroconfig"}

LANES: dict[str, Lane] = {
    "splink": Lane("splink", "run_splink.py"),
    "gm_hand_built": Lane("gm_hand_built", "run_goldenmatch.py", mode="hand_built"),
    # Both FS lanes score in POSTERIOR calibration so GM's match score is a true
    # probability on the SAME scale as Splink's, making the shared --threshold a
    # fair cut for both engines. GM's default is `linear` (min-max of the weight
    # envelope); at a fixed high threshold that scale mismatch admits a mass of
    # weak-but-positive person-shape pairs and catastrophically over-merges
    # (person 1M linear -> F1 0.00), which is a bench artifact, not a model gap.
    # Posterior recovers precision 1.0 / zero FP at the same 0.85 cut.
    "gm_probabilistic": Lane("gm_probabilistic", "run_goldenmatch.py",
                             mode="probabilistic",
                             env={"GOLDENMATCH_FS_NATIVE": "0",
                                  "GOLDENMATCH_FS_CALIBRATED": "posterior"},
                             extra_args=("--fs-basic-scorers",)),
    "gm_probabilistic_native": Lane("gm_probabilistic_native", "run_goldenmatch.py",
                                    mode="probabilistic",
                                    env={"GOLDENMATCH_FS_NATIVE": "1",
                                         "GOLDENMATCH_FS_CALIBRATED": "posterior"},
                                    extra_args=("--fs-basic-scorers",)),
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
    cmd += list(lane.extra_args)
    return cmd

# Per-scale subprocess wall-clock cap (seconds). Raised after the first run so a
# slow-but-progressing datapoint isn't cut off; a true hang still ends eventually.
TIMEOUT_BY_ROWS = [
    (100_000, 900),       # 15 min
    (1_000_000, 1800),    # 30 min
    (5_000_000, 5400),    # 90 min
    (25_000_000, 9000),   # 150 min
    (100_000_000, 18000), # 300 min  -> 25M + 100M = 450 min, under the ~560 cap
]


def _timeout_for(rows: int) -> int:
    for ceiling, t in TIMEOUT_BY_ROWS:
        if rows <= ceiling:
            return t
    return 18000


def _run(cmd: list[str], timeout: int) -> tuple[int, str]:
    """Run a subprocess to completion; classify how it ended. Never raises."""
    try:
        proc = subprocess.run(cmd, timeout=timeout)
        return proc.returncode, "exited"
    except subprocess.TimeoutExpired:
        return -1, "timeout"


def _load_or_synthesize(path: Path, returncode: int, how: str,
                        shape: str, lane: str, rows: int) -> dict:
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
        "shape": shape,
        "lane": lane,
        "rows_requested": rows,
        "status": status,
        "returncode": returncode,
        "note": "no result file written -- process terminated by OS (likely OOM) or timed out",
    }


def _fixture_paths(fixtures: Path, shape: str, rows: int) -> tuple[Path, Path]:
    return (fixtures / f"bench_{shape}_{rows}.parquet",
            fixtures / f"bench_{shape}_{rows}.truth.parquet")


def generate(shape: str, rows: int, dupe_rate: float, fixtures: Path, seed: int = 42) -> Path:
    out, truth = _fixture_paths(fixtures, shape, rows)
    if out.exists():
        print(f"[orchestrate] fixture exists, reusing {out}")
        return out
    print(f"[orchestrate] generating {shape} {rows:,} rows -> {out}")
    subprocess.run(
        [sys.executable, str(HERE / "generate_fixture.py"),
         "--rows", str(rows), "--dupe-rate", str(dupe_rate),
         "--out", str(out), "--ground-truth", str(truth),
         "--seed", str(seed), "--shape", shape],  # --shape: without it a biblio
        check=True, timeout=_timeout_for(rows),    # fixture gets person columns
    )
    return out


def run_engine(lane: Lane, shape: str, fixture: Path, rows: int, results_dir: Path,
               threshold: float, allow_pure_python: bool = False) -> tuple[dict, Path]:
    slug = f"{shape}_{lane.name}_{rows}"
    out = results_dir / f"{slug}.json"
    pred = results_dir / f"{slug}.pred.parquet"
    for stale in (out, pred):
        if stale.exists():
            stale.unlink()  # a stale artifact must not masquerade as fresh
    cmd = build_cmd(lane, input=fixture, rows=rows, out=out, pred=pred,
                    threshold=threshold, shape=shape,
                    allow_pure_python=allow_pure_python)
    t0 = time.perf_counter()
    rc, how = _run_with_env(cmd, lane_env(lane), _timeout_for(rows))
    wall = round(time.perf_counter() - t0, 1)
    res = _load_or_synthesize(out, rc, how, shape, lane.name, rows)
    res["orchestrator_wall_seconds"] = wall
    # Stamp the stable (shape, lane, scale) triple regardless of what the child
    # wrote (a synthesized OOM result already has them, but an ok child may not).
    res["shape"] = shape
    res["lane"] = lane.name
    res["rows_requested"] = rows
    return res, pred


def _run_with_env(cmd: list[str], env: dict[str, str], timeout: int) -> tuple[int, str]:
    """Like _run but with an explicit per-subprocess env (never mutates parent)."""
    try:
        proc = subprocess.run(cmd, env=env, timeout=timeout)
        return proc.returncode, "exited"
    except subprocess.TimeoutExpired:
        return -1, "timeout"


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
        return "-"
    if isinstance(v, float):
        return f"{v:,.1f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def _r(v) -> str:
    """3-decimal formatter for ratio metrics (F1/precision/recall)."""
    return f"{v:.3f}" if isinstance(v, (int, float)) else "-"


def _lane_sort_key(r: dict) -> tuple:
    """Order rows by (scale, lane) with splink first per scale (reference col)."""
    return (r.get("rows_requested", 0), 0 if r.get("lane") == "splink" else 1,
            r.get("lane", ""))


def _shape_section(shape: str, rows_for_shape: list[dict]) -> list[str]:
    lines = [f"## {shape}", ""]

    # Main table: one row per (lane, scale), splink first per scale.
    lines += [
        "| scale | lane | status | dedupe wall (s) | peak RSS (MB) | scored pairs "
        "| clusters | pairs/sec | pairwise F1 | B3 F1 |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in sorted(rows_for_shape, key=_lane_sort_key):
        wall = r.get("dedupe_wall_seconds")
        pairs = r.get("scored_pairs")
        pps = round(pairs / wall) if (pairs and wall) else None
        acc = r.get("accuracy") or {}
        pw = acc.get("pairwise") or {}
        bc = acc.get("bcubed") or {}
        lines.append("| " + " | ".join([
            _fmt(r.get("rows_requested")), r.get("lane", "?"), r.get("status", "?"),
            _fmt(wall), _fmt(r.get("peak_rss_mb")), _fmt(pairs),
            _fmt(r.get("cluster_count")), _fmt(pps),
            _r(pw.get("f1")), _r(bc.get("f1")),
        ]) + " |")

    # Accuracy detail: pairwise P/R/F1 + B3 P/R/F1 + confusion.
    lines += ["", f"### {shape}: accuracy (vs ground truth)", "",
              "| scale | lane | pw P | pw R | pw F1 | B3 P | B3 R | B3 F1 | TP | FP | FN |",
              "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in sorted(rows_for_shape, key=_lane_sort_key):
        acc = r.get("accuracy")
        if not acc:
            continue
        pw, bc, cm = acc["pairwise"], acc["bcubed"], acc["pairwise"]["confusion"]
        lines.append("| " + " | ".join([
            _fmt(r.get("rows_requested")), r.get("lane", "?"),
            _r(pw["precision"]), _r(pw["recall"]), _r(pw["f1"]),
            _r(bc["precision"]), _r(bc["recall"]), _r(bc["f1"]),
            _fmt(cm["tp"]), _fmt(cm["fp"]), _fmt(cm["fn"]),
        ]) + " |")

    # Head-to-head: each GM lane vs the splink reference row at that scale.
    lines += ["", f"### {shape}: head-to-head (each GM lane vs splink)", "",
              "| scale | gm lane | gm wall | splink wall | wall ratio (GM/Splink) "
              "| gm RSS | splink RSS | RSS ratio (GM/Splink) |",
              "|---:|---|---:|---:|---:|---:|---:|---:|"]
    by_scale: dict[int, dict[str, dict]] = {}
    for r in rows_for_shape:
        rid = r.get("rows_requested")
        if rid is None:
            continue
        by_scale.setdefault(rid, {})[r.get("lane")] = r
    for scale in sorted(k for k in by_scale if k is not None):
        sp = by_scale[scale].get("splink", {})
        sw = sp.get("dedupe_wall_seconds")
        srss = sp.get("peak_rss_mb")
        for lane in sorted(by_scale[scale]):
            if lane == "splink":
                continue
            gm = by_scale[scale][lane]
            gw = gm.get("dedupe_wall_seconds")
            grss = gm.get("peak_rss_mb")
            wratio = round(gw / sw, 2) if (gw and sw) else None
            rratio = round(grss / srss, 2) if (grss and srss) else None
            lines.append("| " + " | ".join([
                _fmt(scale), lane, _fmt(gw), _fmt(sw), _fmt(wratio),
                _fmt(grss), _fmt(srss), _fmt(rratio),
            ]) + " |")
    lines.append("")
    return lines


def render_markdown(results: list[dict], header: dict) -> str:
    """Render one section per shape (spec 9 item 1). splink is the fixed
    reference column; head-to-head deltas are per GM lane vs splink."""
    dupe_rate = (header or {}).get("dupe_rate")
    lines = [
        "# ER head-to-head: Splink (DuckDB) vs GoldenMatch (bucket+native+arrow)",
        "",
        f"Single machine, identical parquet fixture per (shape, scale), "
        f"dupe-rate={dupe_rate}. Wall is end-to-end dedupe (Splink: "
        "train+predict+cluster; GoldenMatch: auto_configure+dedupe). Peak RSS is "
        "the per-process high-water mark. `scored pairs` is recorded so "
        "blocking-aggressiveness differences are visible. splink is the reference "
        "lane; head-to-head deltas are each GM lane vs splink.",
        "",
    ]
    by_shape: dict[str, list[dict]] = {}
    for r in results:
        by_shape.setdefault(r.get("shape", "unknown"), []).append(r)
    for shape in sorted(by_shape):
        lines += _shape_section(shape, by_shape[shape])
    return "\n".join(lines) + "\n"


def _git_sha() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(HERE),
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return None


def _total_ram_gb() -> float | None:
    """Best-effort total RAM in GB via /proc/meminfo (None on Windows)."""
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / (1024 * 1024), 1)
    except Exception:
        pass
    return None


def _goldenmatch_version() -> str | None:
    try:
        import goldenmatch
        return getattr(goldenmatch, "__version__", None)
    except Exception:
        return None


def _build_header(*, dupe_rate: float, threshold: float, seed: int, run_tag: str) -> dict:
    """One reproducibility header per run (spec 8). run_timestamp is the merge
    tiebreak; splink_version is filled in later from the first splink result."""
    return {
        "run_timestamp": time.time(),
        "git_sha": _git_sha(),
        "runner_label": os.environ.get("RUNNER_NAME")
                        or os.environ.get("RUNNER_LABEL") or "local",
        "cpu_count": os.cpu_count(),
        "total_ram_gb": _total_ram_gb(),
        "dupe_rate": dupe_rate,
        "threshold": threshold,
        "seed": seed,
        "goldenmatch_version": _goldenmatch_version(),
        "splink_version": None,
        "run_tag": run_tag,
    }


def _datapoint_key(r: dict) -> tuple:
    """The stable (shape, lane, scale) identity of a datapoint."""
    return (r.get("shape"), r.get("lane"), r.get("rows_requested"))


def _load_resume_state(agg_path: Path) -> tuple[dict | None, list[dict], set[tuple]]:
    """Load a prior flushed aggregate for RESUME: returns (header, results,
    done_keys).

    Every datapoint already in the aggregate is a completed, orchestrator-observed
    outcome (ok / OOM / timeout / killed / fixture_failed) -- re-running it wastes a
    runner, and an expected-OOM ceiling tier would just OOM again -- so we treat each
    recorded (shape, lane, rows) triple as done and skip it. The prior header is
    reused so run_timestamp (the merge-results tiebreak) and any resolved
    splink_version stay stable across the resumed attempt.

    This is what lets a preempted job continue instead of recomputing from zero:
    the workflow restores the workdir across runner attempts/dispatches (the cache
    step in bench-er-headtohead.yml), and the per-datapoint flush below means we
    resume from the last datapoint that completed before the runner was reclaimed.
    A corrupt/half-written aggregate is treated as no prior state (start clean)."""
    if not agg_path.exists():
        return None, [], set()
    try:
        obj = json.loads(agg_path.read_text())
    except Exception:
        return None, [], set()
    results = [r for r in (obj.get("results") or [])
               if _datapoint_key(r) != (None, None, None)]
    done = {_datapoint_key(r) for r in results}
    return obj.get("header"), results, done


def run_sweep(*, scales, shapes, lanes, workdir, dupe_rate, threshold,
              allow_pure_python: bool = False, seed: int = 42,
              run_tag: str = "local") -> dict:
    """Sweep lanes x shapes x scales, one isolated subprocess per datapoint.
    Flushes the {header, results} object after EVERY datapoint. RESUMABLE: if the
    workdir already holds a flushed aggregate (restored across a preempted runner
    attempt), datapoints recorded there are skipped so the sweep continues instead
    of recomputing from zero. Does NOT render markdown (that is main()/merge's job
    -- the renderer keys on fields this stamps but is decoupled from the flush).
    Returns the aggregate object."""
    workdir = Path(workdir)
    fixtures = workdir / "fixtures"
    results_dir = workdir / "results"
    fixtures.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    lane_objs = [LANES[name] for name in lanes]
    agg_path = workdir / "bench_results.json"

    # Resume from any prior partial aggregate restored into the workdir.
    prior_header, all_results, done = _load_resume_state(agg_path)
    header = prior_header or _build_header(dupe_rate=dupe_rate, threshold=threshold,
                                           seed=seed, run_tag=run_tag)
    if done:
        print(f"[orchestrate] resume: {len(done)} datapoint(s) already recorded, skipping them")

    def _flush() -> None:
        agg_path.write_text(json.dumps({"header": header, "results": all_results}, indent=2))

    _flush()  # persist the header (new or resumed) even before the first datapoint
    for shape in shapes:
        for rows in scales:
            _, truth = _fixture_paths(fixtures, shape, rows)
            pending = [ln for ln in lane_objs if (shape, ln.name, rows) not in done]
            if not pending:
                print(f"[orchestrate] resume: {shape} {rows:,} fully done, skipping")
                continue
            try:
                fixture = generate(shape, rows, dupe_rate, fixtures, seed=seed)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                for lane in pending:
                    all_results.append({"shape": shape, "lane": lane.name,
                                        "rows_requested": rows,
                                        "status": "fixture_failed", "error": str(e)})
                    done.add((shape, lane.name, rows))
                    _flush()
                continue

            for lane in pending:
                print(f"[orchestrate] === {lane.name} @ {shape} {rows:,} rows ===")
                res, pred = run_engine(lane, shape, fixture, rows, results_dir,
                                       threshold, allow_pure_python=allow_pure_python)
                # Accuracy eval runs here, BEFORE pred cleanup below.
                acc = evaluate_datapoint(pred, truth, results_dir,
                                         f"{shape}_{lane.name}", rows)
                if acc is not None:
                    res["accuracy"] = acc
                pred.unlink(missing_ok=True)
                if (lane.name == "splink" and res.get("splink_version")
                        and not header["splink_version"]):
                    header["splink_version"] = res["splink_version"]
                all_results.append(res)
                done.add((shape, lane.name, rows))
                _flush()  # after EVERY datapoint so a later OOM can't lose earlier points

            # Clean this (shape, scale) fixture before the next -- bound disk.
            for f in fixtures.glob(f"bench_{shape}_{rows}.*"):
                f.unlink(missing_ok=True)

    return {"header": header, "results": all_results}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scales", type=int, nargs="+",
                    default=[100_000, 1_000_000, 5_000_000, 25_000_000])
    ap.add_argument("--lanes", nargs="+", default=list(LANES),
                    help="lane names to run (default: all 6)")
    ap.add_argument("--shapes", nargs="+", default=["person", "biblio"])
    ap.add_argument("--workdir", type=Path, default=Path(".bench_er"))
    ap.add_argument("--dupe-rate", type=float, default=0.20)
    ap.add_argument("--threshold", type=float, default=0.85)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--run-tag", default="local")
    ap.add_argument("--allow-pure-python", action="store_true",
                    help="LOCAL SMOKE ONLY: let GoldenMatch run without the native "
                         "Arrow runtime. Never pass this in CI -- it invalidates the "
                         "'optimized backend' claim.")
    args = ap.parse_args()

    agg = run_sweep(scales=args.scales, shapes=args.shapes, lanes=args.lanes,
                    workdir=args.workdir, dupe_rate=args.dupe_rate,
                    threshold=args.threshold, allow_pure_python=args.allow_pure_python,
                    seed=args.seed, run_tag=args.run_tag)

    md = render_markdown(agg["results"], agg["header"])
    (args.workdir / "summary.md").write_text(md)
    print(md)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a") as fh:
            fh.write(md)


if __name__ == "__main__":
    main()
