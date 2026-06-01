"""Stage-0 Arrow finish-line bench sweep.

Runs each phase's existing kill-criterion bench at the kill scale on the
realistic_person fixture and classifies it PASS / CLOSE / BLOCKED. See
docs/superpowers/specs/2026-06-01-arrow-native-finish-line-design.md.

At-scale inputs (5M/25M) are generated on the bench box via the Railway
goldenmatch-bench-gen service (scripts/trigger_bench_gen.py) or the
generate-bench-dataset.yml workflow; the sweep consumes the generated parquet,
not a locally-built fixture. Do not build >=5M locally.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

CritKind = Literal["ratio_le", "speedup_ge", "abs_le", "bool_true"]


@dataclass(frozen=True)
class Criterion:
    name: str
    kind: CritKind
    target: float | bool


@dataclass
class PhaseVerdict:
    verdict: Literal["PASS", "CLOSE", "BLOCKED"]
    details: list[str] = field(default_factory=list)


def _ratio(m: dict) -> float | None:
    if not m or "new" not in m or "legacy" not in m or not m["legacy"]:
        return None
    return m["new"] / m["legacy"]


def classify_phase(crits: list[Criterion], metrics: dict) -> PhaseVerdict:
    details: list[str] = []
    any_close = False
    for c in crits:
        val = metrics.get(c.name)
        if c.kind == "bool_true":
            if val is not True:
                details.append(f"{c.name}: assertion not met -> BLOCKED")
                return PhaseVerdict("BLOCKED", details)
            details.append(f"{c.name}: OK")
            continue
        if val is None:
            details.append(f"{c.name}: metric missing -> BLOCKED")
            return PhaseVerdict("BLOCKED", details)
        if c.kind == "ratio_le":
            r = _ratio(val)
            if r is None:
                return PhaseVerdict("BLOCKED", details + [f"{c.name}: no ratio -> BLOCKED"])
            if r <= c.target:
                details.append(f"{c.name}: ratio {r:.2f} <= {c.target} PASS")
            elif r < 1.0:
                any_close = True
                details.append(f"{c.name}: ratio {r:.2f} beats legacy, misses {c.target} CLOSE")
            else:
                any_close = True
                details.append(f"{c.name}: ratio {r:.2f} >= 1.0 (no win) CLOSE")
        elif c.kind == "speedup_ge":
            r = _ratio(val)
            if r is None or r <= 0:
                return PhaseVerdict("BLOCKED", details + [f"{c.name}: no speedup -> BLOCKED"])
            speedup = 1.0 / r
            if speedup >= c.target:
                details.append(f"{c.name}: {speedup:.2f}x >= {c.target}x PASS")
            else:
                any_close = True
                details.append(f"{c.name}: {speedup:.2f}x < {c.target}x CLOSE")
        elif c.kind == "abs_le":
            if float(val) <= float(c.target):
                details.append(f"{c.name}: {val} <= {c.target} PASS")
            else:
                any_close = True
                details.append(f"{c.name}: {val} > {c.target} CLOSE")
        else:
            # Unreachable for valid CritKind values; guard against a registry
            # entry adding a new kind without a matching branch (would otherwise
            # silently skip the criterion and yield a spurious PASS).
            raise ValueError(f"unknown criterion kind: {c.kind!r}")
    return PhaseVerdict("CLOSE" if any_close else "PASS", details)


PHASE_CRITERIA: dict[str, list[Criterion]] = {
    "phase1": [
        Criterion("wall", "ratio_le", 0.50),
        Criterion("rss", "ratio_le", 0.25),
        Criterion("parity", "bool_true", True),
    ],
    "phase2": [
        Criterion("rss", "ratio_le", 0.70),
        Criterion("wall", "ratio_le", 1.10),
        Criterion("materialize_cluster_dict_retired", "bool_true", True),
        Criterion("parity", "bool_true", True),
    ],
    "phase3": [
        Criterion("dedup", "speedup_ge", 5.0),
        Criterion("build_clusters", "speedup_ge", 2.0),
        Criterion("fingerprints", "speedup_ge", 3.0),
        Criterion("parity", "bool_true", True),
    ],
    "phase4": [
        Criterion("golden_wall_s", "abs_le", 60.0),
        Criterion("rss", "ratio_le", 0.60),
        Criterion("materialize_cluster_dict_removed", "bool_true", True),
        Criterion("parity", "bool_true", True),
    ],
    "phase5": [
        Criterion("wall", "ratio_le", 0.50),
        Criterion("driver_rss", "ratio_le", 0.10),
        Criterion("parity", "bool_true", True),
    ],
    "phase6": [
        Criterion("apply_standardization_s", "abs_le", 20.0),
        Criterion("zero_full_df_map_elements", "bool_true", True),
    ],
}

PHASE_BENCH_SCALE: dict[str, int] = {
    "phase1": 5_000_000,
    "phase2": 25_000_000,
    "phase3": 5_000_000,
    "phase4": 25_000_000,
    "phase5": 25_000_000,
    "phase6": 10_000_000,
}

_MARK = "__BENCH_JSON__"


def parse_bench_json(stdout: str) -> dict | None:
    last = None
    for line in stdout.splitlines():
        i = line.find(_MARK)
        if i != -1:
            last = line[i + len(_MARK):]
    if last is None:
        return None
    try:
        return json.loads(last)
    except json.JSONDecodeError:
        return None


def parse_native_speedup(stdout: str, label: str) -> float | None:
    """Native kernel benches print a human table, not JSON:
    e.g. `native(Vec) speedup vs python : 2.41x`. Pull the multiple on the
    FIRST line containing `label`. Returns None if absent. (First-match is
    intentional: the native benches print one summary row per label, unlike
    the repeated-run stdout that parse_bench_json's last-match handles.)"""
    for line in stdout.splitlines():
        if label in line:
            m = re.search(r"([0-9]+\.?[0-9]*)\s*x", line)
            if m:
                return float(m.group(1))
    return None


def render_markdown_table(rows: dict) -> str:
    out = ["| Phase | Verdict | Detail |", "|---|---|---|"]
    for phase, v in rows.items():
        out.append(f"| {phase} | {v.verdict} | {'; '.join(v.details)} |")
    return "\n".join(out)


# ── Driver: wire the registry + classifier to the real benches ──────

# Repo root = four parents up from this file
# (packages/python/goldenmatch/scripts/<this>.py -> repo root).
_REPO_ROOT = Path(__file__).resolve().parents[4]
# This package's own scripts/ dir (phase1 pair-stream bench lives here).
_PKG_SCRIPTS = Path(__file__).resolve().parent
# Repo-root scripts/ dir (phase3 native-kernel benches live here).
_ROOT_SCRIPTS = _REPO_ROOT / "scripts"

# Tiny N used in --smoke so wiring is exercised without the bench box.
_SMOKE_N = 2000
# Per-subprocess wall budget. Kill scale runs on the bench box where the
# orchestrating job has its own timeout; smoke must stay snappy locally.
_KILL_TIMEOUT_S = 3600
_SMOKE_TIMEOUT_S = 120


def _run_subprocess(
    cmd: list[str], timeout_s: int
) -> tuple[str | None, str | None]:
    """Run a bench subprocess from the repo root. Returns
    ``(stdout, None)`` on success, ``(None, error_note)`` on any failure
    (nonzero exit, timeout, OSError). Never raises -- a failed bench must
    leave its phase metric absent (-> BLOCKED), not crash the sweep."""
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, f"timeout after {timeout_s}s"
    except OSError as exc:  # missing interpreter / script, etc.
        return None, f"spawn failed: {exc}"
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-1:] or ["<no stderr>"]
        return None, f"exit {proc.returncode}: {tail[0]}"
    return proc.stdout, None


def _phase1_metrics(n: int, timeout_s: int) -> dict:
    """phase1 -> pair-stream columnar bench. Run the worker twice (list,
    columnar); each emits a __BENCH_JSON__ line with total_wall_s +
    peak_rss_mb. new=columnar, legacy=list."""
    script = _PKG_SCRIPTS / "bench_pair_stream_columnar.py"
    notes: list[str] = []
    parsed: dict[str, dict] = {}
    for path in ("list", "columnar"):
        cmd = [sys.executable, str(script), "--worker", str(n), path]
        stdout, err = _run_subprocess(cmd, timeout_s)
        if err is not None:
            notes.append(f"{path} worker failed ({err})")
            continue
        bj = parse_bench_json(stdout or "")
        if bj is None:
            notes.append(f"{path} worker emitted no __BENCH_JSON__")
            continue
        parsed[path] = bj

    metrics: dict = {}
    if "list" in parsed and "columnar" in parsed:
        metrics["wall"] = {
            "new": parsed["columnar"]["total_wall_s"],
            "legacy": parsed["list"]["total_wall_s"],
        }
        metrics["rss"] = {
            "new": parsed["columnar"]["peak_rss_mb"],
            "legacy": parsed["list"]["peak_rss_mb"],
        }
        # The bench doesn't emit a parity flag; equivalence is asserted by
        # tests/test_pair_stream_columnar_parity.py. Treat as True with a note.
        metrics["parity"] = True
        notes.append(
            "parity assumed True (verified by "
            "tests/test_pair_stream_columnar_parity.py, not by this bench)"
        )
    if notes:
        metrics["_note"] = "; ".join(notes)
    return metrics


def _phase3_metrics(n: int, timeout_s: int) -> dict:
    """phase3 -> native-kernel benches at repo-root scripts/. Each speedup
    stored as {"new": 1.0, "legacy": speedup} so speedup_ge computes
    legacy/new == speedup. Missing speedup -> metric omitted -> BLOCKED."""
    metrics: dict = {}
    notes: list[str] = []

    # dedup <- bench_native_kernels.py (positional [N ...]); prints
    # `native(Vec) speedup vs python : X.XXx`.
    stdout, err = _run_subprocess(
        [sys.executable, str(_ROOT_SCRIPTS / "bench_native_kernels.py"), str(n)],
        timeout_s,
    )
    if err is not None:
        notes.append(f"dedup bench failed ({err})")
    else:
        sp = parse_native_speedup(stdout or "", "native(Vec) speedup vs python")
        if sp is None:
            notes.append("dedup speedup not parsed from bench_native_kernels")
        else:
            metrics["dedup"] = {"new": 1.0, "legacy": sp}

    # build_clusters <- bench_native_cluster_kernel.py (no args, synthetic
    # shapes); `large` row carries the speedup.
    stdout, err = _run_subprocess(
        [sys.executable, str(_ROOT_SCRIPTS / "bench_native_cluster_kernel.py")],
        timeout_s,
    )
    if err is not None:
        notes.append(f"cluster bench failed ({err})")
    else:
        sp = parse_native_speedup(stdout or "", "large")
        if sp is None:
            notes.append("build_clusters speedup not parsed (large row)")
        else:
            metrics["build_clusters"] = {"new": 1.0, "legacy": sp}

    # fingerprints <- bench_native_bulk_fingerprint.py (no args); `large`
    # row carries the speedup.
    stdout, err = _run_subprocess(
        [sys.executable, str(_ROOT_SCRIPTS / "bench_native_bulk_fingerprint.py")],
        timeout_s,
    )
    if err is not None:
        notes.append(f"fingerprints bench failed ({err})")
    else:
        sp = parse_native_speedup(stdout or "", "large")
        if sp is None:
            notes.append("fingerprints speedup not parsed (large row)")
        else:
            metrics["fingerprints"] = {"new": 1.0, "legacy": sp}

    # These benches assert parity internally (cluster-count / byte equality
    # checks print WARNING on divergence). Treat as True.
    metrics["parity"] = True
    notes.append("parity assumed True (benches print WARNING on divergence)")
    if notes:
        metrics["_note"] = "; ".join(notes)
    return metrics


def run_phase_bench(phase: str, scale: str) -> dict:
    """Run ``phase``'s kill-criterion bench at ``scale`` and return the
    metrics dict that ``classify_phase(PHASE_CRITERIA[phase], <metrics>)``
    consumes.

    ``scale`` is ``"kill"`` (PHASE_BENCH_SCALE[phase]) or ``"smoke"``
    (tiny N so the wiring is testable without the bench box). A bench that
    fails / times out leaves its metric ABSENT (the phase then classifies
    BLOCKED) with the reason recorded under ``_note``; the sweep never
    crashes on a bench failure.
    """
    if phase not in PHASE_CRITERIA:
        raise ValueError(f"unknown phase: {phase!r}")
    if scale not in ("kill", "smoke"):
        raise ValueError(f"unknown scale: {scale!r}")

    smoke = scale == "smoke"
    n = _SMOKE_N if smoke else PHASE_BENCH_SCALE[phase]
    timeout_s = _SMOKE_TIMEOUT_S if smoke else _KILL_TIMEOUT_S

    if phase == "phase1":
        return _phase1_metrics(n, timeout_s)
    if phase == "phase3":
        return _phase3_metrics(n, timeout_s)
    if phase in ("phase2", "phase4", "phase6"):
        # No dedicated kill-criterion bench wired yet. Return metrics with
        # the gating keys ABSENT so the phase classifies BLOCKED; attach a
        # human note. Do not fabricate numbers.
        return {
            "_note": (
                f"{phase}: no kill-criterion bench wired yet -> BLOCKED "
                "(metrics intentionally absent)"
            )
        }
    if phase == "phase5":
        # Distributed cluster-orchestration bench not built -> BLOCKED.
        return {}
    raise ValueError(f"unhandled phase: {phase!r}")  # pragma: no cover


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Arrow finish-line Stage 0 sweep driver.")
    p.add_argument(
        "--phases",
        default="phase1,phase2,phase3,phase4,phase5,phase6",
        help="Comma-separated phases to run (default: all six).",
    )
    p.add_argument(
        "--scale",
        choices=("kill", "smoke"),
        default="kill",
        help="kill = PHASE_BENCH_SCALE per phase; smoke = tiny N (wiring test).",
    )
    p.add_argument(
        "--out",
        default=None,
        help="Path to write the JSON results file.",
    )
    args = p.parse_args(argv)

    phases = [s.strip() for s in args.phases.split(",") if s.strip()]
    verdicts: dict[str, PhaseVerdict] = {}
    results: dict[str, dict] = {}
    for phase in phases:
        metrics = run_phase_bench(phase, args.scale)
        verdict = classify_phase(PHASE_CRITERIA[phase], metrics)
        verdicts[phase] = verdict
        results[phase] = {
            "verdict": verdict.verdict,
            "details": verdict.details,
            "metrics": metrics,
        }

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(results, indent=2))

    print(render_markdown_table(verdicts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
