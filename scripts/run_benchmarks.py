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
import functools
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

# Planning-effort tier applied to every dedupe/match call (spec 2026-06-06).
# Set from --planning-effort in main(); "normal" reproduces the prior numbers.
_PLANNING_EFFORT = "normal"

# Dataset sources for --download (auto-pull missing datasets). DBLP-ACM is small
# + public (Leipzig); the Magellan mirror carries identical CSVs when Leipzig
# 404s. NCVR's full source is a 4.3 GB NC SBE extract we do NOT mirror — the
# runner pulls only the small derived 10k sample from a controlled mirror URL
# (host it once on a release asset and point GOLDENMATCH_NCVR_SAMPLE_URL at it).
_DBLP_ACM_URL = os.environ.get(
    "GOLDENMATCH_DBLP_ACM_URL", "https://dbs.uni-leipzig.de/file/DBLP-ACM.zip"
)
_NCVR_SAMPLE_URL = os.environ.get("GOLDENMATCH_NCVR_SAMPLE_URL", "")

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


def _http_get(url: str, timeout: int = 180) -> bytes:
    """GET with a few retries + exponential backoff. Raises on final failure."""
    last: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (trusted bench mirrors)
                return resp.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"download failed after retries: {url} ({last})")


def _fetch_dblp_acm(datasets_dir: Path) -> bool:
    """Auto-pull the Leipzig DBLP-ACM CSVs (idempotent). Returns True if present."""
    out = datasets_dir / "DBLP-ACM"
    if (out / "DBLP2.csv").exists():
        return True
    out.mkdir(parents=True, exist_ok=True)
    _info(f"  DBLP-ACM: downloading from {_DBLP_ACM_URL} ...")
    try:
        raw = _http_get(_DBLP_ACM_URL)
        zipfile.ZipFile(io.BytesIO(raw)).extractall(out)
    except zipfile.BadZipFile:
        _info("  DBLP-ACM: response was not a zip (dead mirror returning HTML?); "
              "set GOLDENMATCH_DBLP_ACM_URL to the Magellan mirror. Skipping.")
        return False
    except Exception as exc:  # noqa: BLE001 - download is best-effort
        _info(f"  DBLP-ACM: download failed ({exc}); set GOLDENMATCH_DBLP_ACM_URL "
              "to a mirror. Skipping.")
        return False
    # The zip may nest the CSVs under a folder; flatten so DBLP2.csv sits in out/.
    if not (out / "DBLP2.csv").exists():
        for p in out.rglob("DBLP2.csv"):
            for f in p.parent.iterdir():
                f.rename(out / f.name)
            break
    ok = (out / "DBLP2.csv").exists()
    _info(f"  DBLP-ACM: {'ready' if ok else 'still missing after extract'}.")
    return ok


def _fetch_ncvr_sample(datasets_dir: Path) -> bool:
    """Pull the small derived NCVR 10k sample from a controlled mirror URL.

    The full NC SBE extract (4.3 GB) is intentionally NOT auto-pulled. Host the
    `ncvoter_sample_10k.txt` once (e.g. a release asset) and set
    GOLDENMATCH_NCVR_SAMPLE_URL.
    """
    dest = datasets_dir / "NCVR" / "ncvoter_sample_10k.txt"
    if dest.exists():
        return True
    if not _NCVR_SAMPLE_URL:
        _info("  NCVR: no local sample and GOLDENMATCH_NCVR_SAMPLE_URL unset — "
              "skipping (the 4.3 GB NC SBE source isn't mirrored; host the 10k "
              "sample on a release asset and set the URL).")
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    _info(f"  NCVR: downloading 10k sample from {_NCVR_SAMPLE_URL} ...")
    try:
        dest.write_bytes(_http_get(_NCVR_SAMPLE_URL))
    except Exception as exc:  # noqa: BLE001 - download is best-effort
        _info(f"  NCVR: sample download failed ({exc}). Skipping.")
        return False
    return dest.exists()


def _ensure_datasets(datasets_dir: Path, selected: set[str]) -> None:
    """Auto-pull any selected file-backed datasets that aren't already present.

    Febrl3 (recordlinkage) and dqbench (PyPI) are self-contained; only DBLP-ACM
    and NCVR are file-backed. Best-effort: a failed/skip download just lets the
    per-dataset runner emit its existing 'missing — skipping' notice.
    """
    if "dblp-acm" in selected:
        _fetch_dblp_acm(datasets_dir)
    if "ncvr" in selected:
        _fetch_ncvr_sample(datasets_dir)


def _measure_with_polars(
    name: str, df_loader, gt_pairs_loader,
) -> dict[str, Any]:
    """Run dedupe_df on a polars DataFrame; compare emitted pairs to ground truth."""
    import polars as pl
    from goldenmatch import dedupe_df

    df: pl.DataFrame = df_loader()
    gt_pairs: set[tuple[int, int]] = gt_pairs_loader(df)
    config_start = time.time()
    result = dedupe_df(df, planning_effort=_PLANNING_EFFORT)
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

    backend = "unknown"
    plan = getattr(getattr(result, "postflight_report", None), "controller_history", None)
    plan = getattr(plan, "execution_plan", None)
    if plan is not None and getattr(plan, "backend", None):
        backend = plan.backend

    _info(f"  {name}: f1={f1:.4f} precision={precision:.4f} recall={recall:.4f} "
          f"elapsed={elapsed:.2f}s health={health} stop_reason={stop_reason} "
          f"effort={_PLANNING_EFFORT} backend={backend}")

    return {
        "name": name, "f1": round(f1, 4),
        "precision": round(precision, 4), "recall": round(recall, 4),
        "tp": tp, "fp": fp, "fn": fn,
        "elapsed_seconds": round(elapsed, 2),
        "health": health, "stop_reason": stop_reason,
        "planning_effort": _PLANNING_EFFORT, "backend": backend,
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
    from dqbench_adapters.leipzig_eval import run_dblp_acm_zeroconfig
    from goldenmatch import match_df

    dblp_path = datasets_dir / "DBLP-ACM" / "DBLP2.csv"
    if not dblp_path.exists():
        _info(f"  DBLP-ACM: dataset files missing at {datasets_dir} — skipping")
        return None

    _match = functools.partial(match_df, planning_effort=_PLANNING_EFFORT)
    start = time.time()
    res = run_dblp_acm_zeroconfig(datasets_dir, _match)
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
    from dqbench_adapters.febrl3 import (
        evaluate_febrl3,
        load_febrl3_df_and_gt,
    )
    from goldenmatch import dedupe_df

    loaded = load_febrl3_df_and_gt()
    if loaded is None:
        _info("  Febrl3: recordlinkage not installed — skipping")
        return None
    df, gt_pairs = loaded

    _dedupe = functools.partial(dedupe_df, planning_effort=_PLANNING_EFFORT)
    start = time.time()
    res = evaluate_febrl3(df, gt_pairs, _dedupe)
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
    from dqbench_adapters.ncvr import (
        build_ncvr_df_and_gt,
        build_ncvr_synthetic_df_and_gt,
        evaluate_ncvr,
    )
    from goldenmatch import dedupe_df

    ncvr_path = datasets_dir / "NCVR" / "ncvoter_sample_10k.txt"
    loaded = build_ncvr_df_and_gt(ncvr_path)
    label = "NCVR"
    if loaded is None:
        # No real (PII-bearing, gitignored) sample -> fall back to the committed
        # PII-free synthetic NCVR-shaped fixture so the lane runs anywhere. Its
        # F1 is its OWN baseline, NOT the real-data 0.9719 -> label it distinctly.
        _info(f"  NCVR: real sample absent at {ncvr_path} — using synthetic NCVR-shaped fixture.")
        loaded = build_ncvr_synthetic_df_and_gt()
        label = "NCVR-synthetic"
    df, gt_pairs = loaded

    _dedupe = functools.partial(dedupe_df, planning_effort=_PLANNING_EFFORT)
    start = time.time()
    res = evaluate_ncvr(df, gt_pairs, _dedupe)
    elapsed = time.time() - start
    _info(
        f"  {label}: f1={res.f1:.4f} precision={res.precision:.4f} "
        f"recall={res.recall:.4f} elapsed={elapsed:.2f}s effort={_PLANNING_EFFORT}"
    )
    return {
        "name": label, "f1": round(res.f1, 4),
        "precision": round(res.precision, 4), "recall": round(res.recall, 4),
        "tp": res.true_positives, "fp": res.false_positives,
        "fn": res.false_negatives,
        "elapsed_seconds": round(elapsed, 2),
        "health": "n/a", "stop_reason": "n/a",
        "planning_effort": _PLANNING_EFFORT,
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
    # The adapter calls dedupe_df, which reads GOLDENMATCH_PLANNING_EFFORT — so
    # --planning-effort flows into the DQbench subprocess and the tiers can be
    # A/B'd on the ER composite (thinking lifts T2 by fixing budget-limited RED
    # commits: 51.56 -> 57.11 measured 2026-06-06).
    env["GOLDENMATCH_PLANNING_EFFORT"] = _PLANNING_EFFORT
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
    parser.add_argument("--planning-effort", default="normal",
                        choices=["fast", "normal", "thinking", "einstein"],
                        help="Auto-config planning-effort tier applied to every "
                             "dedupe/match call (default: normal = prior behavior). "
                             "Use to A/B the tiers head-to-head on a dataset.")
    parser.add_argument("--download", action=argparse.BooleanOptionalAction, default=True,
                        help="Auto-pull missing file-backed datasets (DBLP-ACM from "
                             "Leipzig; NCVR 10k sample from GOLDENMATCH_NCVR_SAMPLE_URL). "
                             "Default on; --no-download to use only local files.")
    args = parser.parse_args()

    # Benchmarks must NOT use the cross-run auto-config cache — a config learned
    # on a prior run would leak across datasets and make numbers irreproducible.
    # Force it off unless the caller has deliberately overridden it.
    os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")

    global _PLANNING_EFFORT
    _PLANNING_EFFORT = args.planning_effort
    _info(f"planning_effort={_PLANNING_EFFORT} memory={os.environ.get('GOLDENMATCH_AUTOCONFIG_MEMORY')}")

    selected = {args.datasets} if args.datasets != "all" else {"dblp-acm", "febrl3", "ncvr", "dqbench"}

    if args.download:
        _ensure_datasets(args.datasets_dir, selected)
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
                "planning_effort": _PLANNING_EFFORT,
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
