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
  OPENAI_API_KEY                   required for --with-llm; IGNORED otherwise, so
                                   an ambient key cannot quietly change the numbers
                                   (see `_neutralize_ambient_llm_keys`)
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


def _url_env(name: str, default: str) -> str:
    """Dataset URL override, falling back to ``default`` when the variable is
    unset **or empty**.

    `os.environ.get(name, default)` is wrong here. `benchmarks.yml` passes
    ``GOLDENMATCH_DBLP_ACM_URL: ${{ vars.DBLP_ACM_URL }}``, and a GitHub
    workflow expands an undefined repo variable to the EMPTY STRING rather than
    leaving the variable unset -- so `.get` returns `""` and the empty override
    defeats the default instead of falling back to it.

    That silently skipped DBLP-ACM in every scheduled `benchmarks` run:

        DBLP-ACM: downloading from  ...
        DBLP-ACM: download failed (unknown url type: ''); ... Skipping.

    which is why its quality floor is still recorded as `None` / "no
    trustworthy baseline recorded yet -- these have not completed in CI". The
    dataset was never the problem; the URL was blank. Present in run #94 (Aug 3)
    and still in #97 (Aug 11).

    The product datasets escaped only because the workflow does not set their
    variables at all -- the same hazard, one `env:` line away.
    """
    return os.environ.get(name) or default


# Dataset sources for --download (auto-pull missing datasets). DBLP-ACM is small
# + public (Leipzig); the Magellan mirror carries identical CSVs when Leipzig
# 404s. NCVR's full source is a 4.3 GB NC SBE extract we do NOT mirror — the
# runner pulls only the small derived 10k sample from a controlled mirror URL
# (host it once on a release asset and point GOLDENMATCH_NCVR_SAMPLE_URL at it).
_DBLP_ACM_URL = _url_env(
    "GOLDENMATCH_DBLP_ACM_URL", "https://dbs.uni-leipzig.de/file/DBLP-ACM.zip"
)
# NCVR keeps an empty default ON PURPOSE: the 4.3 GB NC SBE source is not
# mirrored, so "unset" legitimately means "skip", and `_fetch_ncvr_sample`
# branches on the empty string. `_url_env` would be a no-op here anyway.
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
        "url": _url_env("GOLDENMATCH_ABT_BUY_URL", "https://dbs.uni-leipzig.de/file/Abt-Buy.zip"),
        "subdir": "Abt-Buy", "sentinel": "Abt.csv",
        "file_a": "Abt.csv", "file_b": "Buy.csv",
        "gt_file": "abt_buy_perfectMapping.csv", "gt_cols": ("idAbt", "idBuy"),
        "src_a": "abt", "src_b": "buy", "rename": None, "label": "Abt-Buy",
    },
    "amazon-google": {
        "url": _url_env("GOLDENMATCH_AMAZON_GOOGLE_URL", "https://dbs.uni-leipzig.de/file/Amazon-GoogleProducts.zip"),
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


def _measure_product(datasets_dir: Path, key: str) -> list[dict[str, Any]]:
    """Zero-config matching on a Leipzig two-source product dataset; pairwise F1.

    Returns TWO rows, because the dataset supports two genuinely different
    tasks and reporting one number for both was a framing error (#2717):

      * ``<label> (dedupe)`` -- both sources concatenated into one frame,
        everything compared to everything, scored against the transitive
        closure of the mapping. RENAMED from the bare ``<label>`` (#2717): the
        unsuffixed name read as THE number for the dataset when it is the
        harder and far less comparable of the two lanes.
      * ``<label> (linkage)`` -- record linkage via ``match_df(a, b)``, scored
        against the raw cross-source mapping. This is the task the published
        DeepMatcher / Ditto figures measure, so it is the row to compare
        against them.

    Measured on Amazon-Google with token blocking committed: 67.5% of the
    dedupe lane's candidates are same-source pairs, which against a
    cross-source mapping cannot be true matches and land entirely in the
    false-positive column. The engine already prints a warning naming this and
    recommending ``match_df(left, right)``. Neither lane is wrong; they answer
    different questions, and a reader cannot tell which one a bare number
    answers. Hence both, labelled.
    """
    from dqbench_adapters.leipzig_eval import (
        run_two_source_dedupe_zeroconfig,
        run_two_source_link_zeroconfig,
    )
    from goldenmatch import dedupe_df, match_df

    spec = _PRODUCT_SPECS[key]
    shared_kwargs = dict(
        subdir=spec["subdir"], file_a=spec["file_a"], file_b=spec["file_b"],
        gt_file=spec["gt_file"], gt_cols=spec["gt_cols"],
        src_a=spec["src_a"], src_b=spec["src_b"], rename=spec["rename"],
    )
    rows: list[dict[str, Any]] = []

    def _row(label: str, lane: str, res: Any, elapsed: float,
             captured: dict[str, Any]) -> dict[str, Any]:
        health, stop_reason = _controller_health(captured.get("result"))
        _info(
            f"  {label}: f1={res.f1:.4f} precision={res.precision:.4f} "
            f"recall={res.recall:.4f} elapsed={elapsed:.2f}s health={health} "
            f"stop_reason={stop_reason}"
        )
        return {
            "name": label, "f1": round(res.f1, 4),
            "precision": round(res.precision, 4), "recall": round(res.recall, 4),
            "tp": res.true_positives, "fp": res.false_positives,
            "fn": res.false_negatives,
            "elapsed_seconds": round(elapsed, 2),
            "health": health, "stop_reason": stop_reason, "domain": "product",
            "lane": lane,
            "planning_effort": _PLANNING_EFFORT,
        }

    # ---- dedupe lane (historical row; keeps the bare label) ----------------
    # The shared helper returns only the score, so the controller verdict would be
    # lost -- and this function used to hardcode `health: "n/a"`, which made the
    # RED-config check in `_check_quality_floors` unable to fire on the two
    # datasets in the worst shape. Both committed RED configs in the 2026-08-18
    # nightly and neither tripped it (#2457). Capture it off the result instead of
    # widening the helper's contract for one caller.
    dedupe_captured: dict[str, Any] = {}

    def _dedupe(frame):
        res = dedupe_df(frame, planning_effort=_PLANNING_EFFORT)
        dedupe_captured["result"] = res
        return res

    start = time.time()
    res = run_two_source_dedupe_zeroconfig(datasets_dir, _dedupe, **shared_kwargs)
    elapsed = time.time() - start
    if res is None:
        _info(f"  {spec['label']}: dataset files missing - skipping")
        return []
    rows.append(
        _row(f"{spec['label']} (dedupe)", "dedupe", res, elapsed, dedupe_captured)
    )

    # ---- linkage lane ------------------------------------------------------
    link_captured: dict[str, Any] = {}

    def _match(left, right):
        res = match_df(left, right, planning_effort=_PLANNING_EFFORT)
        link_captured["result"] = res
        return res

    start = time.time()
    link = run_two_source_link_zeroconfig(datasets_dir, _match, **shared_kwargs)
    elapsed = time.time() - start
    if link is not None:
        rows.append(
            _row(f"{spec['label']} (linkage)", "linkage", link, elapsed, link_captured)
        )
    return rows


#: Sentinel file proving a file-backed dataset is on disk. Keyed by the same
#: dataset keys `--datasets` takes. Keys absent here (febrl3, dqbench) are
#: self-contained -- they come from `recordlinkage` / PyPI, not from a file.
#: These are the SAME sentinels the fetchers check, so "present" means exactly
#: what "already downloaded" means to `_ensure_datasets`.
_DATASET_SENTINELS: dict[str, str] = {
    "dblp-acm": "DBLP-ACM/DBLP2.csv",
    "ncvr": "NCVR/ncvoter_sample_10k.txt",
    **{k: f"{v['subdir']}/{v['sentinel']}" for k, v in _PRODUCT_SPECS.items()},
}


def _dataset_present(datasets_dir: Path, key: str) -> bool:
    """Is `key` usable from disk? Non-file-backed datasets are always True."""
    sentinel = _DATASET_SENTINELS.get(key)
    return True if sentinel is None else (datasets_dir / sentinel).is_file()


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

    health, stop_reason = _controller_health(result)

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
    # DISPUTED. Set "just under the observed 0.5037" -- but that 0.5037 is
    # UNREPRODUCED, and until someone reproduces it this floor is not known to
    # describe anything. Measured 2026-08-18, all on the same 1118 ground-truth
    # pairs, every cell f1=0.17-0.18 against the baseline's 0.5037 / P=0.8219:
    #
    #   committed baseline (2026-08-11, native=1)  0.5037  P 0.8219   494 pairs
    #   CI nightly, native=0, no key               0.1723  P 0.1068
    #   local HEAD, native=0, no key               0.1723  P 0.1068  4673 pairs
    #   local HEAD, native=1, no key               0.1723  P 0.1068
    #   local 8145b498 (the baseline's OWN commit) 0.1723  P 0.1068
    #   local HEAD, native=0, WITH an LLM key      0.1838  P 0.1132
    #
    # So it is not a code regression (the publishing commit scores 0.1723), not
    # the kernel-vs-fallback split, and not the ambient LLM key -- each was
    # measured and ruled out. The baseline emitted 494 pairs where every
    # reproduction emits ~4673, so whatever produced it was configured very
    # differently. Cause still unknown; do not re-derive this floor from a number
    # nobody can reproduce.
    #
    # NOT lowered here to make the lane green: Abt-Buy also commits a RED config,
    # which fails the lane on its own, so the honest fix is a quality fix rather
    # than a smaller number.
    # ORIGIN RESOLVED 2026-08-22 (#2717): the 0.5037 was a LINKAGE measurement.
    # Running `match_df(abt, buy)` reproduces it -- F1 0.5658, precision 0.9163,
    # 490 emitted pairs, against the baseline's 0.5037 / P 0.8219 / 494 pairs.
    # The pair count matching to within 4 is what settles it. So the floor was
    # derived from the linkage lane and then enforced against the dedupe lane,
    # which is a different and strictly harder task. It is not unreproducible
    # and it was never a regression; the two numbers were never comparable.
    # Kept here at 0.45 for the DEDUPE row it currently gates -- raising or
    # retiring it belongs with the same-source framing decision, not with this
    # change -- and see "Abt-Buy (linkage)" below for where it actually applies.
    # RENAMED to "Abt-Buy (linkage)" below (#2717). The 0.45 was DERIVED from a
    # linkage run and enforced here for months; it now guards the lane it
    # describes.
    #
    # REPAIRED and un-quarantined 2026-08-24: 0.0881 -> 0.1361 (+55%) once the
    # over-merge detector could fire (#2750) and commit stopped discarding the
    # better candidate (#2748). The controller now commits iter=3 at threshold
    # 0.75 instead of falling back to v0 at 0.70.
    #
    # Floor set BELOW the observed value on purpose -- this is a local
    # Windows / native-off measurement, and the convention in this file is to
    # leave margin rather than sit on the number (see the 0.45 note above). 0.10
    # still sits above the 0.0881 it used to score, so a regression of the fix
    # trips it while ordinary run-to-run variance does not.
    #
    # Reachable headroom is far higher: a threshold sweep puts this lane at
    # 0.4059 at thr=0.95 (docs/measurements/). Raise this floor as the
    # controller learns to get there.
    "Abt-Buy (dedupe)": 0.10,
    # KNOWN BAD (#2470). Measured 0.0697 / recall 0.0419. The floor is set at the
    # observed value ONLY to stop it getting worse; it is not an endorsement, and
    # this dataset should be treated as an open quality bug rather than a passing
    # lane. Raise it as the matcher improves.
    "Amazon-Google (dedupe)": 0.05,
    # No trustworthy baseline recorded yet -- these have not completed in CI.
    "DBLP-ACM": None,
    "NCVR": None,
    # The linkage rows (#2717). Measured locally 2026-08-22 at Abt-Buy 0.5658
    # and Amazon-Google 0.4636, but a local Windows / native-off run is not a CI
    # baseline, so no floor is claimed until the lane has published one. They are
    # deliberately NOT quarantined either: a RED controller health on a lane that
    # has just been repaired is exactly the signal worth seeing.
    # The 0.45 floor above was DERIVED from a linkage run (0.5037 / P 0.8219 /
    # 494 pairs) and then enforced against the dedupe lane for months. Now that
    # the linkage row exists, the floor is applied to the lane it actually
    # describes. Measured 0.7024 (P 0.8529, R 0.5971) on 2026-08-23, so 0.45
    # leaves real margin rather than sitting on the observed value -- and a
    # regression back toward the old blocking behaviour trips it.
    "Abt-Buy (linkage)": 0.45,
    "Amazon-Google (linkage)": None,
}


# Datasets with a KNOWN, TRACKED quality bug. A breach here is reported loudly
# but does not fail the lane, because failing on a known-open bug makes the lane
# permanently red -- and a permanently red lane cannot signal a NEW regression.
# That is what happened with #2457: `main-health` opened a tracker that could
# never self-close, so a third dataset regressing would have looked identical to
# the standing failure.
#
# This is NOT the same as lowering a floor. The floor stays where it is, the
# breach is still printed, and the quarantine ratchets in BOTH directions:
#
#   * worse than `f1_at_quarantine` by more than `tolerance` -> FAILS. The bug is
#     tracked, not licensed to deepen.
#   * better than `f1_at_quarantine` + `tolerance`           -> FAILS. Someone
#     fixed it and the quarantine is now hiding good news; lift it and set a real
#     floor in the same change.
#
# Every entry needs an OPEN issue. An entry whose issue is closed is a lie about
# something being tracked, so `_quarantine_breaches` says so rather than
# silently honouring it.
_QUARANTINE: dict[str, dict[str, Any]] = {
    "Abt-Buy (dedupe)": {
        # RE-BASELINED UPWARD 2026-08-24, and re-pointed from the CLOSED #2717.
        # 0.0881 -> 0.1361 (+55%) once the over-merge detector could fire
        # (#2750: its bar sat above a cap `cluster_size_max` can never exceed,
        # and `rule_cluster_giant` kept a private copy of that dead bar) and
        # commit stopped discarding the better candidate (#2748). The controller
        # now commits iter=3 at threshold 0.75 rather than falling back to v0.
        #
        # STILL QUARANTINED, deliberately: the lane's controller health is RED
        # because the detector correctly reports it is STILL over-merging
        # (cluster_size_max 58 against a cap of 100). A threshold sweep puts the
        # reachable optimum at 0.4059 (thr=0.95, docs/measurements/), so 0.1361
        # is a real repair and nowhere near a healthy number. Un-quarantining it
        # here would claim a finished job.
        #
        # The baseline moves UP so the gain cannot be silently lost: a run below
        # 0.1361 - 0.03 now trips this, where the old 0.0881 baseline would have
        # absorbed a full regression of the fix.
        "issue": 2748,
        "f1_at_quarantine": 0.1361,
        "tolerance": 0.03,
        "why": (
            "REPAIRED but not healthy. 0.0881 -> 0.1361 via #2750 + #2748; "
            "controller health is still RED because the lane is still "
            "over-merging (precision 0.0764), and the measured reachable "
            "optimum is 0.4059 at thr=0.95. Quarantined until the controller "
            "can walk its threshold the rest of the way."
        ),
    },
    "Amazon-Google (dedupe)": {
        # RE-POINTED from #2717, which is CLOSED. An entry whose issue is closed
        # is a lie about something being tracked (see this dict's own contract),
        # and the reason this lane is still quarantined is now precisely
        # understood: it stops on `budget_time` after too few iterations to move
        # its threshold, so the fixes that repaired Abt-Buy (#2750, #2748) leave
        # it at 0.1097. Its measured optimum is 0.2211 at thr=0.75.
        "issue": 2748,
        "f1_at_quarantine": 0.1097,
        "tolerance": 0.03,
        "why": (
            "clears its own 0.05 floor but commits a RED config "
            "(budget_time), so the numbers are not trustworthy either way. "
            "Unlike Abt-Buy (dedupe), which this change repaired, this lane "
            "never gets enough iterations to reach a better threshold -- the "
            "time budget truncates exploration before the over-merge rule can "
            "walk it up. Measured headroom: 0.2211 at thr=0.75."
        ),
    },
}


def _llm_label(meta: dict[str, Any]) -> str:
    """How to describe a run's LLM exposure, honestly, from its metadata."""
    if meta.get("with_llm"):
        return "on"
    if meta.get("llm_keys_suppressed") is None:
        # Pre-guard payload: we cannot tell whether a key was in the environment.
        return "not recorded (run predates the ambient-key guard)"
    return "off"


def _controller_health(result: Any) -> tuple[str, str]:
    """Pull (health, stop_reason) off a pipeline result, or ("unknown", ...).

    Returning "unknown" rather than "n/a" matters: `_check_quality_floors` fails
    a RED run outright, so a dataset that cannot report its health must not be
    able to look like one that has none to report.
    """
    health = stop_reason = "unknown"
    report = getattr(result, "postflight_report", None)
    if not report:
        return health, stop_reason
    prof = getattr(report, "controller_profile", None)
    if prof is not None and hasattr(prof, "health"):
        try:
            health = prof.health().value
        except Exception:  # noqa: BLE001 - a health probe must never fail a run
            pass
    hist = getattr(report, "controller_history", None)
    if hist is not None and getattr(hist, "stop_reason", None) is not None:
        stop_reason = hist.stop_reason.value
    return health, stop_reason


#: Env vars `goldenmatch.core.llm_extract` auto-detects with no opt-in.
_LLM_KEY_VARS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")


def _neutralize_ambient_llm_keys(with_llm: bool) -> bool:
    """Remove ambient LLM keys unless the run explicitly asked for them.

    `llm_extract.llm_extract_features` reads `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`
    straight out of `os.environ` with no opt-in flag, so *merely having a key
    exported* changes what this lane measures. On Abt-Buy that path handles 959 of
    2173 records (44%), and it is worth a real, if modest, shift:

        no key   f1=0.1723  P=0.1068  R=0.4463   <- what CI measures
        key      f1=0.1838  P=0.1132  R=0.4875   (+0.0115 F1, ~50x the wall clock)

    A benchmark's whole job is a number that means the same thing on a laptop and
    in CI, so the key is dropped rather than merely reported. Returns True when
    something was actually removed, so the run can record that it happened.

    Scope note, because the measurement above corrected an earlier guess of mine:
    this does NOT explain the committed 0.5037 Abt-Buy baseline. That number is
    unreproduced -- see the `Abt-Buy` entry in `_F1_FLOORS`. The guard is
    justified on determinism alone, not as a fix for that.
    """
    if with_llm:
        return False
    present = [k for k in _LLM_KEY_VARS if os.environ.get(k)]
    for k in present:
        del os.environ[k]
    if present:
        _info(f"  ignoring ambient {', '.join(present)} (pass --with-llm to use it): "
              "an LLM-assisted number is not comparable with CI's.")
    return bool(present)


def _quarantine_drift(q: dict[str, Any] | None, r: dict[str, Any]) -> str | None:
    """Why this quarantine no longer describes this run, or None if it still does.

    Quarantine is a statement that a bug is known AND unchanged. The moment the
    number moves, that statement is false in one of two ways, and both matter:

      * it got WORSE -- the bug is deepening while nothing fails. That is how a
        quarantine turns into a place regressions hide.
      * it got BETTER -- someone fixed it, and a silent quarantine now suppresses
        the evidence. The lane should demand the entry be lifted and a real floor
        set, in the same change that earned it.

    A run that produced no usable f1 (crash, skip) is NOT drift: there is no
    number to compare, and inventing one would fabricate a verdict.
    """
    if not q:
        return None
    f1 = r.get("f1")
    if not isinstance(f1, (int, float)):
        return None
    base = q["f1_at_quarantine"]
    tol = q.get("tolerance", 0.03)
    name = r.get("name", "?")
    if f1 < base - tol:
        return (
            f"{name}: f1={f1:.4f} has DEGRADED past its quarantine baseline "
            f"{base:.4f} (tolerance {tol:.2f}, see #{q['issue']}). A quarantine "
            "tracks a bug; it does not license it to get worse."
        )
    if f1 > base + tol:
        return (
            f"{name}: f1={f1:.4f} has IMPROVED past its quarantine baseline "
            f"{base:.4f} (tolerance {tol:.2f}, see #{q['issue']}). Lift the "
            "_QUARANTINE entry and set a real floor in the same change -- "
            "leaving it quarantined hides the fix."
        )
    return None


def _check_quality_floors(
    results: list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    """Return (failing, quarantined) human-readable breaches.

    `failing` empty means the run is sound. `quarantined` is never empty
    silently -- its contents are printed too, they just do not fail the lane.

    Two independent failure modes, because they fail differently:
      * a metric below its floor -- the numbers are bad;
      * a RED controller health -- the numbers are MEANINGLESS, whatever they
        say, because auto-config never converged on a usable config.

    A dataset in `_QUARANTINE` routes its breaches to the second list, but only
    while it stays where it was quarantined: drifting outside the tolerance in
    EITHER direction routes them back to `failing` (see `_QUARANTINE`).
    """
    failing: list[str] = []
    quarantined: list[str] = []
    for r in results:
        name = r.get("name", "?")
        q = _QUARANTINE.get(name)
        drift = _quarantine_drift(q, r) if q else None
        # A drifted quarantine is no longer describing this run, so its
        # breaches go back to failing -- and the drift itself is reported.
        if drift:
            failing.append(drift)
            q = None
        sink = quarantined if q else failing
        floor = _F1_FLOORS.get(name)
        f1 = r.get("f1")
        if floor is not None and isinstance(f1, (int, float)) and f1 < floor:
            sink.append(
                f"{name}: f1={f1:.4f} is below the floor {floor:.4f} "
                f"(precision={r.get('precision')}, recall={r.get('recall')})"
                + (f"  [QUARANTINED -- see #{q['issue']}]" if q else "")
            )
        # A RED config means the controller gave up and committed a best-effort
        # guess. Elsewhere that is a reasonable degradation; in a lane whose only
        # job is measuring quality it is a FALSE RESULT, so it fails regardless
        # of the F1 it happens to produce.
        if str(r.get("health", "")).upper() == "RED":
            sink.append(
                f"{name}: controller health is RED "
                f"(stop_reason={r.get('stop_reason')}) -- the metrics are not "
                "trustworthy even if they clear the floor"
                + (f"  [QUARANTINED -- see #{q['issue']}]" if q else "")
            )
    return failing, quarantined


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
        # "off" here has to mean the run could not have used an LLM, not merely
        # that it did not ask for one. The old label read `with_llm` alone, so a
        # run with an ambient OPENAI_API_KEY published LLM-assisted numbers under
        # an "off" header (#2457). `llm_keys_suppressed` proves the key was
        # removed; its ABSENCE in an older payload means the run predates the
        # guard and cannot make that claim.
        f"**LLM features:** {_llm_label(meta)}",
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
    parser.add_argument("--download-only", action="store_true",
                        help="Fetch the selected datasets and exit without measuring "
                             "anything. The datasets are gitignored, so this is how a "
                             "developer gets the data for `pytest -m benchmark` without "
                             "sitting through a benchmark run.")
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

    # Same class of leak, and the one that actually bit us (#2457). See the
    # function's docstring for the incident.
    llm_keys_suppressed = _neutralize_ambient_llm_keys(args.with_llm)

    global _PLANNING_EFFORT
    _PLANNING_EFFORT = args.planning_effort
    _info(f"planning_effort={_PLANNING_EFFORT} memory={os.environ.get('GOLDENMATCH_AUTOCONFIG_MEMORY')}")

    if args.datasets == "all":
        selected = {"dblp-acm", "febrl3", "ncvr", "dqbench", "abt-buy", "amazon-google"}
    elif args.datasets == "products":
        selected = {"abt-buy", "amazon-google"}
    else:
        selected = {args.datasets}

    if args.download or args.download_only:
        _ensure_datasets(args.datasets_dir, selected)

    if args.download_only:
        # Report per-dataset presence rather than just exiting 0: `_ensure_datasets`
        # is best-effort by design (a failed pull leaves the per-dataset runner to
        # emit its own "missing -- skipping"), so a silent success here would be
        # indistinguishable from a silent failure.
        missing = [k for k in sorted(selected) if not _dataset_present(args.datasets_dir, k)]
        for key in sorted(selected):
            state = "MISSING" if key in missing else "ok"
            _info(f"  {state:8} {key}")
        if missing:
            _info("")
            _info(f"{len(missing)} dataset(s) unavailable: {', '.join(missing)}. "
                  "Benchmark tests that need them SKIP rather than fail.")
            return 1
        return 0

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
            # extend, not append: each product dataset yields a dedupe row AND
            # a linkage row (#2717). See `_measure_product`.
            results.extend(_measure_product(args.datasets_dir, key))

    results = [r for r in results if r is not None]

    payload = {
        "results": results,
        "metadata": {
            "date": datetime.date.today().isoformat(),
            "with_llm": args.with_llm,
            # Distinct from `with_llm`, which is the *request*. This is whether a
            # key was on the machine and had to be suppressed to keep the run
            # comparable -- the thing that silently moved Abt-Buy by 33 F1 points.
            "llm_keys_suppressed": llm_keys_suppressed,
            "planning_effort": _PLANNING_EFFORT,
            "native": os.environ.get("GOLDENMATCH_NATIVE"),
            "datasets_dir": str(args.datasets_dir),
            "memory_disabled": os.environ.get("GOLDENMATCH_AUTOCONFIG_MEMORY") == "0",
        },
    }

    # #2470: decide BEFORE publishing. The committed report describes itself as
    # "the current truth" and a --check gate forces PRs to match it, so putting a
    # known-bad table there is worse than leaving a stale one.
    breaches, quarantined = _check_quality_floors(results)

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

    # Printed whether or not anything failed: a quarantined breach that only
    # showed up on red runs would be invisible exactly when the lane is green,
    # which is when someone might act on it.
    if quarantined:
        _info("")
        _info("KNOWN-BAD, QUARANTINED (reported, not failing):")
        for b in quarantined:
            _info(f"  - {b}")
        _info(
            "  These do not fail the lane so that a NEW regression still can. "
            "They are not fixed and not forgiven -- see the issue on each."
        )

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
                "--no-enforce-floors to make a red run look green. If a dataset "
                "is a KNOWN open bug, quarantine it in _QUARANTINE with its "
                "issue number rather than weakening the floor."
            )
            return 1
        _info("(--no-enforce-floors: reporting only, not failing)")

    if not results:
        _info("no datasets produced results (none configured); exiting 0")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
