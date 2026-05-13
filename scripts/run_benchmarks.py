"""Reproducible benchmark runner.

Replaces the gitignored `.profile_tmp/run_phase5_1_gate.py` and ad-hoc
DQbench shell scripts with a single committed entry point. Used by:
  - `.github/workflows/benchmarks.yml` (scheduled + workflow_dispatch)
  - Manual reproductions: `python scripts/run_benchmarks.py --datasets all`

Outputs:
  - JSON file with per-dataset {f1, precision, recall, health, stop_reason, elapsed}
  - Markdown summary appended to GITHUB_STEP_SUMMARY (or stdout when missing)

Datasets:
  dblp-acm  — Leipzig DBLP-ACM (latin-1 CSVs)
  febrl3    — recordlinkage's Febrl3 synthetic
  ncvr      — NC voter sample (10K rows)
  dqbench   — DQbench ER tier 1+2+3
  all       — all of the above

Environment:
  GOLDENMATCH_AUTOCONFIG_MEMORY=0  recommended (cross-run cache off for clean numbers)
  OPENAI_API_KEY                   required for --with-llm
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# Make `dqbench_adapters.*` importable when this file is invoked as
# `python scripts/run_benchmarks.py` from the repo root. The scripts/
# directory isn't a package (no top-level __init__.py — adding one
# would change semantics for the other scripts here), so we add the
# scripts/ directory to sys.path and import `dqbench_adapters` directly.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def _info(msg: str) -> None:
    print(f"[run_benchmarks] {msg}", flush=True)


def _measure_with_polars(
    name: str, df_loader, gt_pairs_loader,
) -> dict[str, Any]:
    """Run dedupe_df on a polars DataFrame; compare emitted pairs to ground truth."""
    import polars as pl
    from goldenmatch import dedupe_df

    start = time.time()
    df: pl.DataFrame = df_loader()
    gt_pairs: set[tuple[int, int]] = gt_pairs_loader(df)
    config_start = time.time()
    result = dedupe_df(df)
    elapsed = time.time() - config_start

    # Extract emitted pairs from clusters (canonical form: (min, max))
    emitted: set[tuple[int, int]] = set()
    if hasattr(result, "clusters") and result.clusters:
        for cluster in result.clusters.values():
            members = sorted(cluster.get("members", []))
            for i, a in enumerate(members):
                for b in members[i + 1:]:
                    emitted.add((a, b))

    tp = len(emitted & gt_pairs)
    fp = len(emitted - gt_pairs)
    fn = len(gt_pairs - emitted)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    health = "unknown"
    stop_reason = "unknown"
    if hasattr(result, "postflight_report") and result.postflight_report:
        prof = getattr(result.postflight_report, "controller_profile", None)
        if prof is not None and hasattr(prof, "health"):
            try:
                health = prof.health().value
            except Exception:
                pass
        hist = getattr(result.postflight_report, "controller_history", None)
        if hist is not None and getattr(hist, "stop_reason", None) is not None:
            stop_reason = hist.stop_reason.value

    _info(f"  {name}: f1={f1:.4f} precision={precision:.4f} recall={recall:.4f} "
          f"elapsed={elapsed:.2f}s health={health} stop_reason={stop_reason}")

    return {
        "name": name, "f1": round(f1, 4),
        "precision": round(precision, 4), "recall": round(recall, 4),
        "tp": tp, "fp": fp, "fn": fn,
        "elapsed_seconds": round(elapsed, 2),
        "health": health, "stop_reason": stop_reason,
    }


def _measure_dblp_acm(
    datasets_dir: Path,
) -> dict[str, Any] | None:
    """DBLP-ACM (Leipzig): ID-joined evaluation via `dqbench_adapters.leipzig_eval`.

    Previously this used a positional `int()` join that silently
    dropped every pair (DBLP IDs are non-numeric strings like
    `conf/vldb/...`) and reported F1=0. The shared helper joins
    emitted pairs back to source IDs the same way the package's own
    `tests/benchmarks/run_leipzig.py` harness does.
    """
    from goldenmatch import match_df

    from dqbench_adapters.leipzig_eval import run_dblp_acm_zeroconfig

    dblp_path = datasets_dir / "DBLP-ACM" / "DBLP2.csv"
    if not dblp_path.exists():
        _info(f"  DBLP-ACM: dataset files missing at {datasets_dir} — skipping")
        return None

    start = time.time()
    res = run_dblp_acm_zeroconfig(datasets_dir, match_df)
    elapsed = time.time() - start
    if res is None:
        _info(f"  DBLP-ACM: dataset files missing at {datasets_dir} — skipping")
        return None

    _info(
        f"  DBLP-ACM: f1={res.f1:.4f} precision={res.precision:.4f} "
        f"recall={res.recall:.4f} elapsed={elapsed:.2f}s"
    )
    return {
        "name": "DBLP-ACM", "f1": round(res.f1, 4),
        "precision": round(res.precision, 4), "recall": round(res.recall, 4),
        "tp": res.true_positives, "fp": res.false_positives,
        "fn": res.false_negatives,
        "elapsed_seconds": round(elapsed, 2),
        "health": "n/a", "stop_reason": "n/a",
    }


def _measure_febrl3() -> dict[str, Any] | None:
    """Febrl3 via the committed `dqbench_adapters.febrl3` helper.

    GT mapping was previously stubbed (`# GT mapping omitted in v1 of
    this script`). The helper translates emitted positional pairs back
    to rec_id strings the same way the pre-fold harness at
    `.profile_tmp/baseline_febrl3_ncvr.py` did, so F1 matches the v1.8
    CHANGELOG value (0.9443).
    """
    from goldenmatch import dedupe_df

    from dqbench_adapters.febrl3 import (
        evaluate_febrl3,
        load_febrl3_df_and_gt,
    )

    loaded = load_febrl3_df_and_gt()
    if loaded is None:
        _info("  Febrl3: recordlinkage not installed — skipping")
        return None
    df, gt_pairs = loaded

    start = time.time()
    res = evaluate_febrl3(df, gt_pairs, dedupe_df)
    elapsed = time.time() - start
    _info(
        f"  Febrl3: f1={res.f1:.4f} precision={res.precision:.4f} "
        f"recall={res.recall:.4f} elapsed={elapsed:.2f}s"
    )
    return {
        "name": "Febrl3", "f1": round(res.f1, 4),
        "precision": round(res.precision, 4), "recall": round(res.recall, 4),
        "tp": res.true_positives, "fp": res.false_positives,
        "fn": res.false_negatives,
        "elapsed_seconds": round(elapsed, 2),
        "health": "n/a", "stop_reason": "n/a",
    }


def _measure_ncvr(datasets_dir: Path) -> dict[str, Any] | None:
    """NCVR voter sample with corruption-based synthetic GT.

    Mirrors the committed logic in
    `tests/test_autoconfig_benchmarks.py::test_autoconfig_ncvr_meets_target`
    (seed=42, N=5000 base records, half corrupted into `*_DUP` pairs).
    The 0.9719 F1 in the v1.8 CHANGELOG was measured against this
    construction; the 10K-row source file is gitignored.
    """
    from goldenmatch import dedupe_df

    from dqbench_adapters.ncvr import build_ncvr_df_and_gt, evaluate_ncvr

    ncvr_path = datasets_dir / "NCVR" / "ncvoter_sample_10k.txt"
    loaded = build_ncvr_df_and_gt(ncvr_path)
    if loaded is None:
        _info(f"  NCVR: sample missing at {ncvr_path} — skipping")
        return None
    df, gt_pairs = loaded

    start = time.time()
    res = evaluate_ncvr(df, gt_pairs, dedupe_df)
    elapsed = time.time() - start
    _info(
        f"  NCVR: f1={res.f1:.4f} precision={res.precision:.4f} "
        f"recall={res.recall:.4f} elapsed={elapsed:.2f}s"
    )
    return {
        "name": "NCVR", "f1": round(res.f1, 4),
        "precision": round(res.precision, 4), "recall": round(res.recall, 4),
        "tp": res.true_positives, "fp": res.false_positives,
        "fn": res.false_negatives,
        "elapsed_seconds": round(elapsed, 2),
        "health": "n/a", "stop_reason": "n/a",
    }


def _run_dqbench(with_llm: bool = False) -> dict[str, Any] | None:
    """DQbench ER tiers via the dqbench CLI."""
    import shutil
    import subprocess
    if not shutil.which("dqbench"):
        _info("  DQbench: dqbench CLI not on PATH — skipping")
        return None
    # Adapter promoted out of the gitignored `.profile_tmp/` directory in
    # PR feature/benchmark-provenance-fix so this script reproduces the
    # v1.12 composite from a fresh `git clone`. We pass the committed
    # path explicitly so `dqbench run --adapter <path>` loads from it.
    adapter_path = Path("scripts/dqbench_adapters/goldenmatch_zeroconfig.py")
    if not adapter_path.exists():
        _info(f"  DQbench: adapter missing at {adapter_path} — skipping")
        return None

    env = os.environ.copy()
    if not with_llm:
        # Strip API keys so DQbench measures the no-LLM path
        for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
            env.pop(key, None)
        env.pop("GOLDENMATCH_AUTOCONFIG_LLM", None)

    start = time.time()
    proc = subprocess.run(
        ["dqbench", "run", "goldenmatch-zeroconfig", "--adapter", str(adapter_path)],
        capture_output=True, text=True, env=env,
    )
    elapsed = time.time() - start
    output = proc.stdout + proc.stderr

    # Parse the composite from the last "DQBench ER Score: X.XX" line
    composite = None
    for line in output.splitlines()[::-1]:
        if "DQBench ER Score" in line:
            try:
                composite = float(line.split(":")[1].split("/")[0].strip())
            except (IndexError, ValueError):
                pass
            break

    _info(f"  DQbench (with_llm={with_llm}): composite={composite} elapsed={elapsed:.1f}s")
    return {
        "name": "DQbench" + (" (with-LLM)" if with_llm else ""),
        "composite": composite, "elapsed_seconds": round(elapsed, 1),
        "raw_output_tail": "\n".join(output.splitlines()[-30:]),
    }


def _emit_markdown_summary(results: list[dict[str, Any]], summary_path: Path | None) -> None:
    lines = ["## Benchmark results", "", "| Dataset | F1 | Precision | Recall | Time | Health |",
             "|---|---|---|---|---|---|"]
    for r in results:
        if r is None:
            continue
        if "composite" in r:
            lines.append(f"| {r['name']} | composite={r['composite']} | — | — | "
                         f"{r['elapsed_seconds']}s | — |")
        else:
            lines.append(f"| {r['name']} | {r['f1']:.4f} | {r['precision']:.4f} | "
                         f"{r['recall']:.4f} | {r['elapsed_seconds']}s | "
                         f"{r.get('health', '—')} |")
    text = "\n".join(lines) + "\n"
    if summary_path and summary_path != Path("-"):
        with summary_path.open("a", encoding="utf-8") as f:
            f.write(text + "\n")
    else:
        print(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datasets", default="all",
                        choices=["all", "dblp-acm", "febrl3", "ncvr", "dqbench"])
    parser.add_argument("--with-llm", action="store_true",
                        help="Run DQbench with LLM scorer (requires OPENAI_API_KEY)")
    parser.add_argument("--output", type=Path, default=None,
                        help="Write JSON results to this path")
    parser.add_argument("--summary-md", type=Path, default=None,
                        help="Append markdown summary to this path (typically $GITHUB_STEP_SUMMARY)")
    parser.add_argument("--datasets-dir", type=Path,
                        default=Path("packages/python/goldenmatch/tests/benchmarks/datasets"),
                        help="Directory containing benchmark datasets")
    args = parser.parse_args()

    selected = {args.datasets} if args.datasets != "all" else {"dblp-acm", "febrl3", "ncvr", "dqbench"}
    results: list[dict[str, Any] | None] = []

    if "dblp-acm" in selected:
        results.append(_measure_dblp_acm(args.datasets_dir))
    if "febrl3" in selected:
        results.append(_measure_febrl3())
    if "ncvr" in selected:
        results.append(_measure_ncvr(args.datasets_dir))
    if "dqbench" in selected:
        results.append(_run_dqbench(with_llm=args.with_llm))

    results = [r for r in results if r is not None]

    if args.output:
        args.output.write_text(json.dumps({
            "results": results,
            "metadata": {
                "with_llm": args.with_llm,
                "datasets_dir": str(args.datasets_dir),
                "memory_disabled": os.environ.get("GOLDENMATCH_AUTOCONFIG_MEMORY") == "0",
            },
        }, indent=2))
        _info(f"wrote results to {args.output}")

    _emit_markdown_summary(results, args.summary_md)

    if not results:
        _info("no datasets produced results (none configured); exiting 0")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
