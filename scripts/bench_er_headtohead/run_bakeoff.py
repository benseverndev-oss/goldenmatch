#!/usr/bin/env python
"""Unified accuracy+perf bake-off orchestrator for the ER head-to-head.

Runs THREE engines per benchmark dataset on one machine and emits a single
comparison table (bakeoff.md / bakeoff.json):

  * gm_zeroconfig    -- GoldenMatch's zero-config controller (auto_configure_df)
  * gm_probabilistic -- GoldenMatch's Fellegi-Sunter probabilistic auto-config
  * splink           -- a hand-rolled Splink spec (compound blocking + EM)

Each engine runs as a SUBPROCESS (run_goldenmatch.py / run_splink.py), so the OS
reclaims its memory on exit and one engine's failure can never poison another's
measurement. Both GoldenMatch modes write STRING-record_id predictions; Splink
writes real-record_id predictions. All three are scored by the SAME evaluator
(evaluate.evaluate), so accuracy numbers are directly comparable.

Robustness contract (mirrors run_panel.py): a missing dataset/dep, a refused
config, a non-zero exit, or a subprocess timeout becomes a `skipped`/`refused`/
`error`/`timeout` row -- NEVER fatal to the whole bake-off.

build_rows() and render_md() are PURE assembly functions (no I/O) so the table
math + null-tolerant formatting are unit-testable against stubbed engine
results, independent of the live engines.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
RUN_GM = _HERE / "run_goldenmatch.py"
RUN_SPLINK = _HERE / "run_splink.py"

ENGINES = ["gm_zeroconfig", "gm_probabilistic", "splink"]
DATASETS = ["historical_50k", "febrl3", "synthetic_person", "dblp_acm"]

# Maps a GoldenMatch bake-off engine name to run_goldenmatch.py's --mode value.
_GM_MODE = {"gm_zeroconfig": "zeroconfig", "gm_probabilistic": "probabilistic"}

# Generous per-engine subprocess cap; Splink EM + clustering and the GM
# zero-config controller can be slow on a shared runner. A timeout records
# `timeout`, never hangs the bake-off.
_ENGINE_TIMEOUT_S = 3600


def _import_sibling(name: str):
    """Import a sibling bench module whether run as a script or imported."""
    if str(_HERE) not in sys.path:
        sys.path.insert(0, str(_HERE))
    try:
        return __import__(name)
    except ImportError:
        path = _HERE / f"{name}.py"
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


def build_rows(
    per_engine_results: dict[str, dict[str, tuple[dict, dict | None]]],
) -> list[dict]:
    """Flatten per-(dataset, engine) (result, metrics) tuples into table rows.

    PURE function -- no I/O. `per_engine_results` is
        {dataset: {engine: (result_dict, metrics_dict|None)}}
    where result_dict carries at least `status` plus optional perf fields
    (dedupe_wall_seconds, peak_rss_mb, scored_pairs, rows) and metrics_dict is
    the evaluate.evaluate output (or None for skipped/refused/error/timeout).

    Each row:
        {dataset, engine, status, precision, recall, f1, bcubed_f1,
         dedupe_wall_seconds, peak_rss_mb, scored_pairs,
         throughput_pairs_per_s, rows}

    throughput_pairs_per_s = round(scored_pairs / wall) only when BOTH are
    present and wall > 0, else None. Accuracy fields are None when metrics is
    None. peak_rss_mb=None is tolerated verbatim (Windows has no rusage).
    """
    rows: list[dict] = []
    for dataset, engines in per_engine_results.items():
        for engine, (result, metrics) in engines.items():
            result = result or {}
            wall = result.get("dedupe_wall_seconds")
            scored_pairs = result.get("scored_pairs")
            throughput = None
            if (
                scored_pairs is not None
                and wall is not None
                and wall > 0
            ):
                throughput = round(scored_pairs / wall)

            row: dict = {
                "dataset": dataset,
                "engine": engine,
                "status": result.get("status", "error"),
                "precision": None,
                "recall": None,
                "f1": None,
                "bcubed_f1": None,
                "dedupe_wall_seconds": wall,
                "peak_rss_mb": result.get("peak_rss_mb"),
                "scored_pairs": scored_pairs,
                "throughput_pairs_per_s": throughput,
                "rows": result.get("rows") or result.get("rows_loaded")
                or result.get("rows_requested"),
            }
            if metrics is not None:
                pw = metrics.get("pairwise", {}) or {}
                bc = metrics.get("bcubed", {}) or {}
                row["precision"] = pw.get("precision")
                row["recall"] = pw.get("recall")
                row["f1"] = pw.get("f1")
                row["bcubed_f1"] = bc.get("f1")
            # Carry an error/reason note through for transparency in the table.
            note = result.get("reason") or result.get("error")
            if note:
                row["note"] = note
            rows.append(row)
    return rows


def _fmt(v) -> str:
    """Render a cell. None -> '-' so null RSS / skipped cells never crash."""
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


def _ratio(a, b) -> str:
    """a/b as a 2-dp ratio string, or '-' if either is missing / b == 0."""
    if a is None or b is None or b == 0:
        return "-"
    return f"{a / b:.2f}x"


def _delta(a, b) -> str:
    """a-b as a signed 4-dp delta string, or '-' if either is missing."""
    if a is None or b is None:
        return "-"
    return f"{a - b:+.4f}"


def render_md(rows: list[dict]) -> str:
    """Markdown report: a per-dataset accuracy+perf table (rows = engines) plus a
    per-dataset GM-vs-Splink delta block, plus an honest-framing footer.

    Uses _fmt() so any None cell (null RSS, skipped accuracy) renders as '-'.
    """
    # Group rows by dataset, preserving first-seen order.
    by_dataset: dict[str, list[dict]] = {}
    for r in rows:
        by_dataset.setdefault(r["dataset"], []).append(r)

    header = (
        "| Engine | Status | P | R | F1 | B3-F1 | wall(s) | peak RSS(MB) "
        "| throughput pairs/s |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"
    )

    lines: list[str] = ["# ER bake-off: GoldenMatch (zero-config + probabilistic) vs hand-rolled Splink", ""]

    for dataset, drows in by_dataset.items():
        lines.append(f"## {dataset}")
        lines.append("")
        lines.append(header)
        for r in drows:
            status = r.get("status", "-")
            note = r.get("note")
            if note and status in ("skipped", "error", "refused", "timeout"):
                status = f"{status} ({note})"
            lines.append(
                "| {engine} | {st} | {p} | {rcl} | {f1} | {b3} | {wall} "
                "| {rss} | {tput} |".format(
                    engine=r.get("engine", "-"),
                    st=status,
                    p=_fmt(r.get("precision")),
                    rcl=_fmt(r.get("recall")),
                    f1=_fmt(r.get("f1")),
                    b3=_fmt(r.get("bcubed_f1")),
                    wall=_fmt(r.get("dedupe_wall_seconds")),
                    rss=_fmt(r.get("peak_rss_mb")),
                    tput=_fmt(r.get("throughput_pairs_per_s")),
                )
            )
        lines.append("")

        # Per-dataset GM-vs-Splink delta block (wall ratio, RSS ratio, F1 delta).
        splink = next((r for r in drows if r.get("engine") == "splink"), None)
        gm_rows = [r for r in drows if r.get("engine", "").startswith("gm_")]
        if splink is not None and splink.get("dedupe_wall_seconds") is not None:
            lines.append(
                "**GoldenMatch vs Splink (ratio = GM / Splink; F1 delta = GM - Splink):**"
            )
            lines.append("")
            lines.append(
                "| GM mode | wall ratio | RSS ratio | F1 delta |\n"
                "| --- | --- | --- | --- |"
            )
            for gm in gm_rows:
                if gm.get("dedupe_wall_seconds") is None:
                    continue
                lines.append(
                    "| {mode} | {wall} | {rss} | {f1d} |".format(
                        mode=gm.get("engine", "-"),
                        wall=_ratio(
                            gm.get("dedupe_wall_seconds"),
                            splink.get("dedupe_wall_seconds"),
                        ),
                        rss=_ratio(gm.get("peak_rss_mb"), splink.get("peak_rss_mb")),
                        f1d=_delta(gm.get("f1"), splink.get("f1")),
                    )
                )
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("**Honest framing:**")
    lines.append("")
    lines.append(
        "- Pairwise P/R/F1 and B-cubed F1 come from ONE shared evaluator "
        "(`evaluate.evaluate`), so all three engines are judged by identical code."
    )
    lines.append(
        "- The ~0.97 Splink figure quoted for `historical_50k` in Splink's own "
        "docs is a CLUSTER-level metric; the F1 here is pairwise under the shared "
        "evaluator, so it will not match that number 1:1."
    )
    lines.append(
        "- Splink skips `dblp_acm` honestly (bibliographic settings are out of "
        "scope for the hand-rolled spec); that row is `skipped`, not a 0."
    )
    lines.append(
        "- Perf (wall / peak RSS / throughput) is SINGLE-RUN per engine and "
        "subject to runner variance; treat ratios as directional, not exact. "
        "`peak RSS` is null on Windows (no rusage) and renders as `-`."
    )
    return "\n".join(lines) + "\n"


def _run_gm(name, mode, ds_dir, records_parquet, rows_n, truth_path, threshold):
    """Run run_goldenmatch.py in zeroconfig/probabilistic mode as a subprocess.

    Returns (result_dict, metrics|None). status=ok + pred present -> metrics
    from the shared evaluator; refused/error/timeout -> metrics None.
    """
    res_path = ds_dir / f"gm_{mode}_res.json"
    pred_path = ds_dir / f"gm_{mode}_pred.parquet"
    cmd = [
        sys.executable,
        str(RUN_GM),
        "--input", str(records_parquet),
        "--rows", str(rows_n),
        "--mode", mode,
        "--allow-pure-python",
        "--pred-out", str(pred_path),
        "--out", str(res_path),
        "--threshold", str(threshold),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_ENGINE_TIMEOUT_S
        )
    except subprocess.TimeoutExpired:
        return ({"status": "timeout", "error": f"timed out after {_ENGINE_TIMEOUT_S}s"}, None)

    if proc.stdout:
        print(proc.stdout.rstrip())

    result: dict = {}
    if res_path.exists():
        try:
            result = json.loads(res_path.read_text())
        except Exception as e:  # noqa: BLE001
            result = {"status": "error", "error": f"unreadable result json: {e}"}

    if proc.returncode != 0 and result.get("status") not in ("ok", "refused"):
        err = (proc.stderr or "").strip()[-400:]
        result = {"status": "error", "error": f"run_goldenmatch exited {proc.returncode}: {err}"}
        return result, None

    if result.get("status") != "ok":
        # refused / error / unknown -> no metrics.
        return result, None

    if not pred_path.exists():
        result["status"] = "error"
        result["error"] = "goldenmatch reported ok but wrote no pred parquet"
        return result, None

    try:
        evaluate_mod = _import_sibling("evaluate")
        metrics = evaluate_mod.evaluate(pred_path, truth_path)
    except Exception as e:  # noqa: BLE001 - eval failure -> error row, never fatal
        result["status"] = "error"
        result["error"] = f"evaluate failed: {type(e).__name__}: {e}"
        return result, None
    return result, metrics


def _run_splink(name, ds_dir, truth_path, threshold):
    """Run run_splink.py --dataset <name> as a subprocess; return (result, metrics).

    Mirrors run_panel.py::_run_splink: a dataset Splink declines -> status
    `skipped` (metrics None); a non-zero exit/timeout -> `error`/`timeout`.
    """
    res_path = ds_dir / "splink_res.json"
    pred_path = ds_dir / "splink_pred.parquet"
    cmd = [
        sys.executable,
        str(RUN_SPLINK),
        "--dataset", name,
        "--out", str(res_path),
        "--pred-out", str(pred_path),
        "--threshold", str(threshold),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_ENGINE_TIMEOUT_S
        )
    except subprocess.TimeoutExpired:
        return ({"status": "timeout", "error": f"timed out after {_ENGINE_TIMEOUT_S}s"}, None)

    if proc.stdout:
        print(proc.stdout.rstrip())

    result: dict = {}
    if res_path.exists():
        try:
            result = json.loads(res_path.read_text())
        except Exception as e:  # noqa: BLE001
            result = {"status": "error", "error": f"unreadable result json: {e}"}

    status = result.get("status")
    if proc.returncode != 0 and status not in ("skipped", "ok"):
        err = (proc.stderr or "").strip()[-400:]
        return ({"status": "error", "error": f"run_splink exited {proc.returncode}: {err}"}, None)

    if status == "skipped":
        return result, None
    if status != "ok":
        if "error" not in result:
            result["error"] = f"splink status={status}"
        result["status"] = "error"
        return result, None

    if not pred_path.exists():
        result["status"] = "error"
        result["error"] = "splink reported ok but wrote no pred parquet"
        return result, None

    try:
        evaluate_mod = _import_sibling("evaluate")
        metrics = evaluate_mod.evaluate(pred_path, truth_path)
    except Exception as e:  # noqa: BLE001
        result["status"] = "error"
        result["error"] = f"evaluate failed: {type(e).__name__}: {e}"
        return result, None
    return result, metrics


def _print_progress(dataset, engine, result, metrics) -> None:
    f1 = None
    if metrics is not None:
        f1 = (metrics.get("pairwise", {}) or {}).get("f1")
    print(
        f"[bakeoff] {dataset} {engine}: status={result.get('status')} "
        f"F1={f1} wall={result.get('dedupe_wall_seconds')}s "
        f"peak_rss={result.get('peak_rss_mb')}MB"
    )


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="bakeoff_out", type=Path)
    ap.add_argument(
        "--datasets",
        default=",".join(DATASETS),
        help="comma-separated dataset names",
    )
    ap.add_argument("--threshold", type=float, default=0.85)
    args = ap.parse_args(argv)

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    names = [n.strip() for n in args.datasets.split(",") if n.strip()]

    datasets = _import_sibling("datasets")

    import polars as pl

    per_engine_results: dict[str, dict[str, tuple[dict, dict | None]]] = {}
    for name in names:
        print(f"\n=== dataset: {name} ===")
        per_engine_results[name] = {}
        try:
            records, truth = datasets.load_dataset(name)
        except getattr(datasets, "DatasetUnavailable", Exception) as e:
            reason = f"{type(e).__name__}: {e}"
            print(f"[bakeoff] {name}: unavailable -> skipping all engines ({reason})")
            for eng in ENGINES:
                per_engine_results[name][eng] = ({"status": "skipped", "reason": reason}, None)
            continue
        except Exception as e:  # noqa: BLE001 - load failure -> error, never fatal
            reason = f"{type(e).__name__}: {e}"
            print(f"[bakeoff] {name}: load error -> skipping all engines ({reason})")
            for eng in ENGINES:
                per_engine_results[name][eng] = ({"status": "error", "error": reason}, None)
            continue

        ds_dir = out_dir / name
        ds_dir.mkdir(parents=True, exist_ok=True)

        # Records: preserve the REAL record_id verbatim for the engine subprocess.
        records_parquet = ds_dir / "records.parquet"
        records.write_parquet(records_parquet)
        # Truth in STRING record_id space so it joins both GM (string preds) and
        # Splink (real-id preds) under the shared evaluator.
        truth_path = ds_dir / "truth.parquet"
        truth.with_columns(pl.col("record_id").cast(pl.Utf8)).write_parquet(truth_path)

        rows_n = records.height

        for eng in ("gm_zeroconfig", "gm_probabilistic"):
            result, metrics = _run_gm(
                name, _GM_MODE[eng], ds_dir, records_parquet, rows_n,
                truth_path, args.threshold,
            )
            per_engine_results[name][eng] = (result, metrics)
            _print_progress(name, eng, result, metrics)

        result, metrics = _run_splink(name, ds_dir, truth_path, args.threshold)
        per_engine_results[name]["splink"] = (result, metrics)
        _print_progress(name, "splink", result, metrics)

    rows = build_rows(per_engine_results)
    (out_dir / "bakeoff.json").write_text(json.dumps(rows, indent=2))
    md = render_md(rows)
    (out_dir / "bakeoff.md").write_text(md)
    print("\n" + md)


if __name__ == "__main__":
    main(sys.argv[1:])
