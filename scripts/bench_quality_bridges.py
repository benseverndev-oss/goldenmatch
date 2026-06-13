"""A/B proving sweep for the GoldenCheck->GoldenMatch quality bridges.

Two bridges are env-gated default-OFF until a benchmark sweep proves them
(``core/autoconfig.py::_quality_aware_blocking_enabled`` and
``core/autoconfig_negative_evidence.py::_fd_negative_evidence_enabled``). This
runs the real-dataset benchmark (``run_benchmarks.py``) three times -- a baseline
with both gates off, then each gate on in isolation -- and emits a per-gate,
per-dataset delta table plus an advisory verdict against each gate's stated
criterion. The verdict is advisory; the maintainer flips the default from the
table.

The third bridge (quality-gated review) is deliberately NOT swept: it downgrades
borderline auto-merges to a HUMAN review queue, so in a fully-automated benchmark
it is recall-negative by construction. It stays opt-in for review workflows.

Datasets: Febrl3 (synthetic PII corruption) + NCVR (voter corruption; the
committed synthetic NCVR-shaped fixture in CI) are the proving ground -- these
bridges target person-data typos/variants. DQbench (~41 min/run) and DBLP-ACM
(bibliographic; the bridges should not move it) are deliberately dropped from
the iterative A/B: a 3-config sweep over them blew the CI 90-min cap for zero
signal on these gates. If a DQbench ``composite`` result is ever present in the
run, the verdict still guards it as a non-regression floor (see ``_gate_report``).

Benchmarks OOM the dev box; run this in CI (``bench-quality-bridges.yml``). Each
run honours ``run_benchmarks.py``'s dataset auto-pull / graceful-skip.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNNER = REPO_ROOT / "scripts" / "run_benchmarks.py"

# gate label -> env var
GATES = {
    "quality_aware_blocking": "GOLDENMATCH_QUALITY_AWARE_BLOCKING",
    "fd_negative_evidence": "GOLDENMATCH_FD_NEGATIVE_EVIDENCE",
}
# datasets the gate's stated criterion is judged on (person-data corruption).
# "NCVR-synthetic" is the CI fallback label when the real (gitignored) NCVR
# sample is absent -- run_benchmarks.py labels it distinctly, so include both.
TARGET_DATASETS = {"Febrl3", "NCVR", "NCVR-synthetic"}
# Datasets swept per config. DQbench (~41 min) and DBLP-ACM are dropped -- see
# the module docstring. NCVR still imports dqbench_adapters, so the workflow's
# dqbench install stays even though "dqbench" is no longer a swept dataset.
SWEEP_DATASETS = ("febrl3", "ncvr")
DQBENCH_FLOOR = 91.04  # v1.12 committed composite; guarded only if present.


def _run(gate_env: dict[str, str], out_path: Path, datasets_dir: Path | None) -> dict:
    """Run each ``SWEEP_DATASETS`` benchmark once with ``gate_env`` applied and
    return the merged ``{name: result}`` map. One JSON artifact is written per
    dataset (``<stem>-<dataset>.json``). Missing datasets are silently absent."""
    env = dict(os.environ)
    env["GOLDENMATCH_AUTOCONFIG_MEMORY"] = "0"  # clean numbers, no cross-run cache
    # Reset both gates off, then apply the requested overrides, so each run is
    # isolated regardless of the ambient environment.
    for var in GATES.values():
        env[var] = "0"
    env.update(gate_env)
    label = " ".join(f"{k}={v}" for k, v in gate_env.items()) or "baseline"
    merged: dict = {}
    for ds in SWEEP_DATASETS:
        ds_path = out_path.with_name(f"{out_path.stem}-{ds}.json")
        cmd = [sys.executable, str(RUNNER), "--datasets", ds, "--output", str(ds_path)]
        if datasets_dir is not None:
            cmd += ["--datasets-dir", str(datasets_dir)]
        print(f"[ab] running {ds}: {label}", flush=True)
        subprocess.run(cmd, check=True, env=env, cwd=str(REPO_ROOT))
        data = json.loads(ds_path.read_text())
        merged.update({r["name"]: r for r in data.get("results", [])})
    return merged


def _fmt(x) -> str:
    return f"{x:+.4f}" if isinstance(x, (int, float)) else "-"


def _delta(on: dict, base: dict, key: str):
    if key in on and key in base:
        return round(on[key] - base[key], 4)
    return None


def _gate_report(label: str, base: dict, on: dict) -> tuple[str, bool | None]:
    lines = [f"### `{label}` (env `{GATES[label]}`)", ""]
    lines.append("| dataset | metric | OFF | ON | Δ |")
    lines.append("|---|---|---:|---:|---:|")
    target_recall_up = False
    target_f1_up = False
    target_precision_drop = False
    target_recall_drop = False
    dqbench_ok = True
    for name in sorted(set(base) | set(on)):
        b, o = base.get(name, {}), on.get(name, {})
        if "composite" in b or "composite" in o:
            d = _delta(o, b, "composite")
            lines.append(f"| {name} | composite | {b.get('composite','-')} | {o.get('composite','-')} | {_fmt(d)} |")
            if o.get("composite") is not None and o["composite"] < DQBENCH_FLOOR - 1.0:
                dqbench_ok = False
            continue
        for m in ("f1", "precision", "recall"):
            d = _delta(o, b, m)
            lines.append(f"| {name} | {m} | {b.get(m,'-')} | {o.get(m,'-')} | {_fmt(d)} |")
            if name in TARGET_DATASETS and d is not None:
                if m == "recall" and d >= 0.005:
                    target_recall_up = True
                if m == "recall" and d <= -0.005:
                    target_recall_drop = True
                if m == "f1" and d >= 0.002:
                    target_f1_up = True
                if m == "precision" and d <= -0.005:
                    target_precision_drop = True
    # Advisory verdict against each gate's STATED criterion. dqbench_ok is True
    # by default and only tightens if a DQbench composite happens to be present
    # (it isn't in the default Febrl3+NCVR sweep), so the guard is a no-op floor.
    guard = "" if dqbench_ok else ", DQbench REGRESSED"
    if label == "quality_aware_blocking":
        # recall-up / no precision regression / (DQbench non-regression if swept)
        passed = target_recall_up and not target_precision_drop and dqbench_ok
        crit = "recall up on a corruption set, precision flat" + guard
    else:  # fd_negative_evidence
        # F1/precision up / no recall loss / (DQbench non-regression if swept)
        passed = target_f1_up and not target_recall_drop and dqbench_ok
        crit = "F1 up on a corruption set, no recall loss" + guard
    verdict = "✅ CLEARS" if passed else "❌ does not clear"
    lines += ["", f"**Advisory verdict: {verdict}** — criterion: {crit}.", ""]
    return "\n".join(lines), passed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workdir", type=Path, default=REPO_ROOT / ".ab_quality_bridges")
    ap.add_argument("--datasets-dir", type=Path, default=None)
    ap.add_argument("--summary-md", type=Path, default=None,
                    help="Write the markdown report here (e.g. $GITHUB_STEP_SUMMARY)")
    args = ap.parse_args(argv)
    args.workdir.mkdir(parents=True, exist_ok=True)

    base = _run({}, args.workdir / "baseline.json", args.datasets_dir)
    report = [
        "# Quality-bridge A/B proving sweep",
        "",
        "Baseline = both gates OFF (current default). Each gate is then enabled in "
        "isolation. Advisory verdicts apply each gate's stated proving criterion; "
        "the maintainer makes the flip call from the deltas.",
        "",
    ]
    any_clears = False
    for label, var in GATES.items():
        on = _run({var: "1"}, args.workdir / f"{label}.json", args.datasets_dir)
        section, passed = _gate_report(label, base, on)
        report.append(section)
        any_clears = any_clears or bool(passed)

    text = "\n".join(report)
    print(text)
    if args.summary_md:
        with args.summary_md.open("a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    # Exit 0 always — this is a measurement, not a gate. The verdicts are advisory.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
