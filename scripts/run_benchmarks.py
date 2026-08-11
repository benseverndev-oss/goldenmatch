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
import datetime
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


# Leipzig product-matching datasets (heterogeneous two-source schemas). Each spec
# feeds `leipzig_eval.run_two_source_dedupe_zeroconfig`. These are the honest
# HARD domain for a zero-config PII/bibliographic engine -- product titles resist
# blocking (see #715) -- so the benchmark tracks them as an improvement target.
_PRODUCT_SPECS: dict[str, dict[str, Any]] = {
    "abt-buy": {
        "url": os.environ.get("GOLDENMATCH_ABT_BUY_URL", "https://dbs.uni-leipzig.de/file/Abt-Buy.zip"),
        "subdir": "Abt-Buy", "sentinel": "Abt.csv",
        "file_a": "Abt.csv", "file_b": "Buy.csv",
        "gt_file": "abt_buy_perfectMapping.csv", "gt_cols": ("idAbt", "idBuy"),
        "src_a": "abt", "src_b": "buy", "rename": None, "label": "Abt-Buy",
    },
    "amazon-google": {
        "url": os.environ.get("GOLDENMATCH_AMAZON_GOOGLE_URL", "https://dbs.uni-leipzig.de/file/Amazon-GoogleProducts.zip"),
        "subdir": "Amazon-Google", "sentinel": "Amazon.csv",
        "file_a": "Amazon.csv", "file_b": "GoogleProducts.csv",
        "gt_file": "Amzon_GoogleProducts_perfectMapping.csv", "gt_cols": ("idAmazon", "idGoogleBase"),
        "src_a": "amazon", "src_b": "google", "rename": {"name": "title"}, "label": "Amazon-Google",
    },
}


def _fetch_leipzig_product(datasets_dir: Path, key: str) -> bool:
    """Auto-pull a Leipzig product dataset zip (idempotent). Returns True if present."""
    spec = _PRODUCT_SPECS[key]
    out = datasets_dir / spec["subdir"]
    if (out / spec["sentinel"]).exists():
        return True
    out.mkdir(parents=True, exist_ok=True)
    _info(f"  {spec['label']}: downloading from {spec['url']} ...")
    try:
        zipfile.ZipFile(io.BytesIO(_http_get(spec["url"]))).extractall(out)
    except Exception as exc:  # noqa: BLE001 - download is best-effort
        _info(f"  {spec['label']}: download failed ({exc}). Skipping.")
        return False
    # Flatten if the zip nested the CSVs under a folder.
    if not (out / spec["sentinel"]).exists():
        for p in out.rglob(spec["sentinel"]):
            for f in p.parent.iterdir():
                f.rename(out / f.name)
            break
    return (out / spec["sentinel"]).exists()


def _measure_product(datasets_dir: Path, key: str) -> dict[str, Any] | None:
    """Zero-config dedupe on a Leipzig two-source product dataset; pairwise F1."""
    from dqbench_adapters.leipzig_eval import run_two_source_dedupe_zeroconfig
    from goldenmatch import dedupe_df

    spec = _PRODUCT_SPECS[key]
    _dedupe = functools.partial(dedupe_df, planning_effort=_PLANNING_EFFORT)
    start = time.time()
    res = run_two_source_dedupe_zeroconfig(
        datasets_dir, _dedupe,
        subdir=spec["subdir"], file_a=spec["file_a"], file_b=spec["file_b"],
        gt_file=spec["gt_file"], gt_cols=spec["gt_cols"],
        src_a=spec["src_a"], src_b=spec["src_b"], rename=spec["rename"],
    )
    elapsed = time.time() - start
    if res is None:
        _info(f"  {spec['label']}: dataset files missing — skipping")
        return None
    _info(
        f"  {spec['label']}: f1={res.f1:.4f} precision={res.precision:.4f} "
        f"recall={res.recall:.4f} elapsed={elapsed:.2f}s"
    )
    return {
        "name": spec["label"], "f1": round(res.f1, 4),
        "precision": round(res.precision, 4), "recall": round(res.recall, 4),
        "tp": res.true_positives, "fp": res.false_positives, "fn": res.false_negatives,
        "elapsed_seconds": round(elapsed, 2),
        "health": "n/a", "stop_reason": "n/a", "domain": "product",
        "planning_effort": _PLANNING_EFFORT,
    }


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
    for key in ("abt-buy", "amazon-google"):
        if key in selected:
            _fetch_leipzig_product(datasets_dir, key)


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


# ---------------------------------------------------------------------------
# Quality floors (#2470)
# ---------------------------------------------------------------------------
#
# Without these the lane reports success on any run that does not CRASH, so a
# quality collapse is indistinguishable from a healthy run. Run 31414594892 was
# GREEN with Amazon-Google at f1=0.0697 / recall=0.0419 -- finding 4.2% of true
# matches -- while the engine itself had logged that it committed a best-effort
# RED config.
#
# Floors are set at roughly the CURRENT HONEST values, deliberately not at
# aspirational ones: the job of this gate is to catch regressions, not to fail
# every run until the matcher improves. Raising a floor after a genuine
# improvement is a reviewable one-line diff, which is the point of committing
# them next to the datasets.
#
# `None` means "no floor yet" -- a dataset nobody has a trustworthy baseline for.
# That is honest, and it is visible, which an absent entry would not be.
_F1_FLOORS: dict[str, float | None] = {
    # measured 0.9912
    "Febrl3": 0.95,
    # measured 0.5037 -- product matching is genuinely hard; this pins the floor
    # just under the observed value so a real regression trips it.
    "Abt-Buy": 0.45,
    # KNOWN BAD (#2470). Measured 0.0697 / recall 0.0419. The floor is set at the
    # observed value ONLY to stop it getting worse; it is not an endorsement, and
    # this dataset should be treated as an open quality bug rather than a passing
    # lane. Raise it as the matcher improves.
    "Amazon-Google": 0.05,
    # No trustworthy baseline recorded yet -- these have not completed in CI.
    "DBLP-ACM": None,
    "NCVR": None,
}


def _check_quality_floors(results: list[dict[str, Any]]) -> list[str]:
    """Return a list of human-readable breaches. Empty means the run is sound.

    Two independent failure modes, because they fail differently:
      * a metric below its floor -- the numbers are bad;
      * a RED controller health -- the numbers are MEANINGLESS, whatever they
        say, because auto-config never converged on a usable config.
    """
    breaches: list[str] = []
    for r in results:
        name = r.get("name", "?")
        floor = _F1_FLOORS.get(name)
        f1 = r.get("f1")
        if floor is not None and isinstance(f1, (int, float)) and f1 < floor:
            breaches.append(
                f"{name}: f1={f1:.4f} is below the floor {floor:.4f} "
                f"(precision={r.get('precision')}, recall={r.get('recall')})"
            )
        # A RED config means the controller gave up and committed a best-effort
        # guess. Elsewhere that is a reasonable degradation; in a lane whose only
        # job is measuring quality it is a FALSE RESULT, so it fails regardless
        # of the F1 it happens to produce.
        if str(r.get("health", "")).upper() == "RED":
            breaches.append(
                f"{name}: controller health is RED "
                f"(stop_reason={r.get('stop_reason')}) -- the metrics are not "
                "trustworthy even if they clear the floor"
            )
    return breaches


def _emit_markdown_summary(results: list[dict[str, Any] | None], summary_path: Path | None) -> None:
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


def _render_report(payload: dict[str, Any]) -> str:
    """Deterministic markdown SoT from a results payload (date carried in the
    payload's metadata, NOT `today`, so `--check` is reproducible). This is the
    committed number-of-record so published benchmarks never drift stale — the
    CI run regenerates BOTH the JSON and this doc, and `--check` fails a PR whose
    doc no longer matches its JSON."""
    meta = payload.get("metadata", {})
    results = [r for r in payload.get("results", []) if r]
    date = meta.get("date", "—")
    native = meta.get("native")
    native_label = "native (GOLDENMATCH_NATIVE=1)" if native in ("1", "true", "True") else "pure-Python"
    lines = [
        "# GoldenMatch benchmark results",
        "",
        "**GENERATED — do not hand-edit.** Regenerated by",
        "`python scripts/run_benchmarks.py --report docs/benchmarks/latest-results.md`",
        "(the scheduled `.github/workflows/benchmarks.yml` commits it). A `--check`",
        "gate fails any PR whose doc drifts from its JSON, so these numbers are the",
        "current truth, not a stale copy pasted from a case study.",
        "",
        f"**Run date:** {date} &nbsp;·&nbsp; **path:** {native_label} &nbsp;·&nbsp; "
        f"**planning_effort:** {meta.get('planning_effort', '—')} &nbsp;·&nbsp; "
        f"**LLM features:** {'on' if meta.get('with_llm') else 'off'}",
        "",
    ]
    if not results:
        lines += [
            "> **Awaiting the first native benchmark run.** These numbers are produced"
            " by CI on the perf path (`GOLDENMATCH_NATIVE=1`), not on a laptop — the"
            " zero-config controller is too slow locally to publish honest wall-times."
            " Trigger `.github/workflows/benchmarks.yml` (workflow_dispatch) or wait for"
            " the weekly run; it commits the filled-in table here.",
            "",
            "_Classification: benchmarks/generated — regenerated by"
            " `scripts/run_benchmarks.py`._",
        ]
        return "\n".join(lines) + "\n"
    lines += [
        "| Dataset | Domain | F1 | Precision | Recall | Time |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        if "composite" in r:
            lines.append(
                f"| {r['name']} | benchmark-suite | composite={r['composite']} | — | — | "
                f"{r['elapsed_seconds']}s |"
            )
            continue
        lines.append(
            f"| {r['name']} | {r.get('domain', 'record')} | {r['f1']:.4f} | "
            f"{r['precision']:.4f} | {r['recall']:.4f} | {r['elapsed_seconds']}s |"
        )
    lines += [
        "",
        "## Reading these numbers",
        "",
        "- **Record-domain** (DBLP-ACM, Febrl3, NCVR, DQbench) is the engine's home"
        " turf — bibliographic / PII / voter records.",
        "- **Product-domain** (Abt-Buy, Amazon-Google) is deliberately the HARD case:"
        " short product titles resist blocking (see issue #715). We publish these to"
        " make the gap visible and track it down, not to flatter the zero-config path.",
        "",
        "_Classification: benchmarks/generated — regenerated by"
        " `scripts/run_benchmarks.py`._",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datasets", default="all",
                        choices=["all", "dblp-acm", "febrl3", "ncvr", "dqbench",
                                 "abt-buy", "amazon-google", "products"])
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
    parser.add_argument("--report", type=Path, default=None,
                        help="Write a committed, deterministic markdown SoT (+ sibling "
                             ".json) to this path (e.g. docs/benchmarks/latest-results.md). "
                             "The scheduled workflow commits it so published numbers never "
                             "go stale.")
    parser.add_argument("--check", action="store_true",
                        help="Do not run: render the report from the committed .json and "
                             "fail (exit 1) if the committed .md is stale. Pairs with --report.")
    parser.add_argument("--enforce-floors", action=argparse.BooleanOptionalAction,
                        default=True,
                        help="Fail (exit 1) when a dataset falls below its committed "
                             "F1 floor or reports RED controller health (#2470). On by "
                             "default: a benchmark lane that cannot fail measures nothing.")
    parser.add_argument("--download", action=argparse.BooleanOptionalAction, default=True,
                        help="Auto-pull missing file-backed datasets (DBLP-ACM from "
                             "Leipzig; NCVR 10k sample from GOLDENMATCH_NCVR_SAMPLE_URL). "
                             "Default on; --no-download to use only local files.")
    args = parser.parse_args()

    if args.check:
        # Staleness gate only: no benchmark run. Render from the committed JSON and
        # compare byte-for-byte with the committed markdown.
        if not args.report:
            _info("--check requires --report <path-to-committed .md>")
            return 2
        json_path = args.report.with_suffix(".json")
        if not json_path.exists() or not args.report.exists():
            _info(f"--check: missing {json_path} or {args.report}; run --report first")
            return 1
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        expected = _render_report(payload)
        actual = args.report.read_text(encoding="utf-8")
        if expected != actual:
            _info(f"{args.report} is stale vs {json_path}. Regenerate with "
                  f"python scripts/run_benchmarks.py --report {args.report} --no-download "
                  f"(or re-run the benchmark).")
            return 1
        _info(f"{args.report} is current vs {json_path}.")
        return 0

    # Benchmarks must NOT use the cross-run auto-config cache — a config learned
    # on a prior run would leak across datasets and make numbers irreproducible.
    # Force it off unless the caller has deliberately overridden it.
    os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")

    global _PLANNING_EFFORT
    _PLANNING_EFFORT = args.planning_effort
    _info(f"planning_effort={_PLANNING_EFFORT} memory={os.environ.get('GOLDENMATCH_AUTOCONFIG_MEMORY')}")

    if args.datasets == "all":
        selected = {"dblp-acm", "febrl3", "ncvr", "dqbench", "abt-buy", "amazon-google"}
    elif args.datasets == "products":
        selected = {"abt-buy", "amazon-google"}
    else:
        selected = {args.datasets}

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
    for key in ("abt-buy", "amazon-google"):
        if key in selected:
            results.append(_measure_product(args.datasets_dir, key))

    results = [r for r in results if r is not None]

    payload = {
        "results": results,
        "metadata": {
            "date": datetime.date.today().isoformat(),
            "with_llm": args.with_llm,
            "planning_effort": _PLANNING_EFFORT,
            "native": os.environ.get("GOLDENMATCH_NATIVE"),
            "datasets_dir": str(args.datasets_dir),
            "memory_disabled": os.environ.get("GOLDENMATCH_AUTOCONFIG_MEMORY") == "0",
        },
    }

    # #2470: decide BEFORE publishing. The committed report describes itself as
    # "the current truth" and a --check gate forces PRs to match it, so putting a
    # known-bad table there is worse than leaving a stale one.
    breaches = _check_quality_floors(results)

    if args.output:
        args.output.write_text(json.dumps(payload, indent=2))
        _info(f"wrote results to {args.output}")

    if args.report and breaches and args.enforce_floors:
        _info(
            f"REFUSING to publish {args.report}: {len(breaches)} quality "
            "breach(es) listed below."
        )
    elif args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.with_suffix(".json").write_text(json.dumps(payload, indent=2) + "\n",
                                                     encoding="utf-8")
        args.report.write_text(_render_report(payload), encoding="utf-8")
        _info(f"wrote committed report to {args.report} (+ .json)")

    _emit_markdown_summary(results, args.summary_md)

    if breaches:
        _info("")
        _info("QUALITY FLOOR BREACHES (#2470):")
        for b in breaches:
            _info(f"  - {b}")
        if args.enforce_floors:
            _info("")
            _info(
                "Failing the lane. If a floor is genuinely wrong, change it in "
                "_F1_FLOORS as a reviewable diff -- do NOT pass "
                "--no-enforce-floors to make a red run look green."
            )
            return 1
        _info("(--no-enforce-floors: reporting only, not failing)")

    if not results:
        _info("no datasets produced results (none configured); exiting 0")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
