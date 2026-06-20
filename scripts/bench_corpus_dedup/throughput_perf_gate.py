#!/usr/bin/env python
"""Deterministic per-PR throughput-tier cost gate (#1086).

Runs the throughput tier on the vendored OFFLINE corpus at a FIXED size+seed+config and
gates on MACHINE-INDEPENDENT cost — no wall-clock in the verdict, so it can't flake on
shared-runner noise:

  candidate_pairs   pairs the sketch blocking emits to verify (dominant cost; from posture)
  reduction_ratio   from the throughput posture
  measured_recall   pairwise recall on the injected ground-truth dups, via the shared
                    head-to-head evaluator (NOT the posture's analytic expected_recall)

Compared vs a committed baseline with tolerance (a blocking change that blows up the pair
count, or quietly drops recall, fails). `--update-baseline` regenerates it (snapshot style).
First run with no baseline committed SEEDS one and passes, printing the values to commit.

Usage:
  python throughput_perf_gate.py --check            # compare vs perf_gate_baseline.json
  python throughput_perf_gate.py --update-baseline  # regenerate the baseline
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASELINE = HERE / "perf_gate_baseline.json"
EVALUATOR = HERE.parent / "bench_er_headtohead" / "evaluate.py"

PAIRS_TOL = 0.15   # candidate_pairs may grow at most +15%
EPS = 0.01         # recall / reduction_ratio floors

# Fixed gate workload — change these only deliberately (and re-seed the baseline).
GATE_N_DOCS = 1500
GATE_SEED = 0
GATE_FRAC = 0.4
GATE_RECALL_TARGET = 0.95


def measure(workdir: Path) -> dict:
    """Build the fixed offline fixture, run the tier, return the machine-independent metrics."""
    workdir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, str(HERE / "inject_dups.py"), "--corpus", "offline",
         "--n-docs", str(GATE_N_DOCS), "--seed", str(GATE_SEED),
         "--frac", str(GATE_FRAC), "--out-dir", str(workdir)],
        check=True,
    )
    out = workdir / "gate.json"
    pred = workdir / "gate.pred.parquet"
    subprocess.run(
        [sys.executable, str(HERE / "run_goldenmatch.py"),
         "--input", str(workdir / "corpus.parquet"), "--out", str(out),
         "--pred-out", str(pred), "--recall-target", str(GATE_RECALL_TARGET)],
        check=True,
    )
    r = json.loads(out.read_text())
    if r.get("status") != "ok":
        raise RuntimeError(f"gate run did not succeed: {r.get('status')} {r.get('error')}")

    metrics_out = workdir / "gate.metrics.json"
    subprocess.run(
        [sys.executable, str(EVALUATOR), "--pred", str(pred),
         "--truth", str(workdir / "truth.parquet"), "--out", str(metrics_out)],
        check=True,
    )
    recall = json.loads(metrics_out.read_text())["pairwise"]["recall"]
    return {
        "candidate_pairs": int(r["candidate_pairs"]),
        "reduction_ratio": round(float(r["reduction_ratio"]), 4),
        "measured_recall": round(float(recall), 4),
    }


def compare(baseline: dict, current: dict) -> tuple[bool, list[str]]:
    fails = []
    if current["candidate_pairs"] > baseline["candidate_pairs"] * (1 + PAIRS_TOL):
        fails.append(
            f"candidate_pairs {current['candidate_pairs']} > "
            f"{baseline['candidate_pairs']}*(1+{PAIRS_TOL})"
        )
    if current["measured_recall"] < baseline["measured_recall"] - EPS:
        fails.append(
            f"measured_recall {current['measured_recall']} < "
            f"{baseline['measured_recall']}-{EPS}"
        )
    if current["reduction_ratio"] < baseline["reduction_ratio"] - EPS:
        fails.append(
            f"reduction_ratio {current['reduction_ratio']} < "
            f"{baseline['reduction_ratio']}-{EPS}"
        )
    return (not fails), fails


def write_baseline(path: Path, metrics: dict) -> None:
    path.write_text(json.dumps(metrics, indent=2) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--workdir", type=Path, default=HERE / ".gate_tmp")
    args = ap.parse_args()

    current = measure(args.workdir)
    print("[gate] measured:", json.dumps(current))

    if args.update_baseline:
        write_baseline(BASELINE, current)
        print(f"[gate] baseline updated -> {BASELINE}")
        return

    if not BASELINE.exists():
        # First run: seed the baseline and pass, so the gate has something to commit.
        write_baseline(BASELINE, current)
        print(f"[gate] NO baseline committed yet — SEEDED {BASELINE.name} with the values "
              f"above. Commit it so the gate starts enforcing.\n[gate] PASS (seeded)")
        return

    baseline = json.loads(BASELINE.read_text())
    ok, fails = compare(baseline, current)
    if ok:
        print("[gate] PASS")
    else:
        print("[gate] FAIL\n  - " + "\n  - ".join(fails))
        sys.exit(1)


if __name__ == "__main__":
    main()
