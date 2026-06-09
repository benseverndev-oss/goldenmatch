#!/usr/bin/env python
"""Probabilistic-vs-Splink accuracy panel orchestrator.

Runs GoldenMatch's PROBABILISTIC path (auto_configure_probabilistic_df +
dedupe_df) and Splink across several datasets on one machine, then emits a
comparison table (panel.md / panel.json) plus a recall-attribution split
(blocking_recall vs threshold_loss) for the GoldenMatch side.

GoldenMatch runs IN-PROCESS (we need the dumped candidate/emitted pair sets via
the GOLDENMATCH_BENCH_DUMP_PAIRS hook). Splink runs as a SUBPROCESS of
run_splink.py --dataset (OS reclaims its memory on exit; a missing/unsupported
dataset there is recorded as skipped, never fatal).

The internal __row_id__ == input row position (dedupe_df preserves input order),
so dataset record_id space is recovered via rid[__row_id__] for both cluster
members AND the dumped (a, b) pairs before evaluating / attributing.

Robustness contract: a missing dataset/dep or an engine failure becomes a
`skipped`/`error` row, NEVER fatal to the whole panel.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

_HERE = Path(__file__).resolve().parent

# Generous per-engine subprocess cap; Splink EM + clustering on historical_50k
# can be slow on a shared runner. A timeout records `error`, never hangs the panel.
_SPLINK_TIMEOUT_S = 3600


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


def _gm_predictions_path(records, truth, gm_pred_path: Path, dump_dir: Path):
    """Run the GoldenMatch probabilistic path, write predictions, return the
    (candidate_recordid, emitted_recordid) pair sets in dataset record_id space.

    Caller owns try/except; this raises on any GM failure so the caller records
    an `error` row for the dataset.
    """
    import numpy as np
    import polars as pl  # noqa: F401  (ensures polars import errors surface here)
    import pyarrow as pa
    import pyarrow.parquet as pq
    from goldenmatch.core.autoconfig import auto_configure_probabilistic_df

    try:
        from goldenmatch import dedupe_df
    except ImportError:  # older layouts expose this on _api
        from goldenmatch._api import dedupe_df

    rid = records["record_id"].to_list()

    dump_dir.mkdir(parents=True, exist_ok=True)
    os.environ["GOLDENMATCH_BENCH_DUMP_PAIRS"] = str(dump_dir)
    try:
        cfg = auto_configure_probabilistic_df(records)
        ded = dedupe_df(records, config=cfg)
    finally:
        os.environ.pop("GOLDENMATCH_BENCH_DUMP_PAIRS", None)

    # Per-record cluster assignment. clusters is {cid: {"members": [__row_id__...]}}
    # (or objects with .members). Map every internal __row_id__ back to record_id.
    clusters = getattr(ded, "clusters", None) or {}
    rec_ids: list = []
    pred_cids: list = []
    for cid, c in clusters.items():
        members = c["members"] if isinstance(c, dict) else c.members
        for m in members:
            rec_ids.append(rid[m])
            pred_cids.append(cid)

    gm_pred_path.parent.mkdir(parents=True, exist_ok=True)
    # record_id may be int (synthetic) or str (dblp_acm/febrl3) -> use a string
    # column to keep the join key type-stable against the truth parquet.
    pq.write_table(
        pa.table(
            {
                "record_id": pa.array([str(r) for r in rec_ids], pa.string()),
                "pred_cluster_id": pa.array(
                    np.asarray(pred_cids, dtype=np.int64)
                ),
            }
        ),
        gm_pred_path,
        compression="zstd",
    )

    candidate_recordid = _remap_pairs(dump_dir / "candidate_pairs.parquet", rid)
    emitted_recordid = _remap_pairs(dump_dir / "emitted_pairs.parquet", rid)
    return candidate_recordid, emitted_recordid


def _remap_pairs(path: Path, rid: list) -> set:
    """Remap dumped (a, b) __row_id__ pairs to canonical record_id pairs.

    Missing dump file (e.g. zero probabilistic blocks) -> empty set.
    """
    if not path.exists():
        return set()
    import polars as pl

    df = pl.read_parquet(path)
    out: set = set()
    for a, b in zip(df["a"].to_list(), df["b"].to_list()):
        ra, rb = rid[a], rid[b]
        # canonical (min, max) in string space (record_ids may be int or str).
        sa, sb = str(ra), str(rb)
        out.add((sa, sb) if sa <= sb else (sb, sa))
    return out


def _truth_to_string_pairs(truth_to_pairs, truth) -> set:
    """truth_to_pairs returns canonical (min,max) in native id space; restate
    to string space + re-canonicalize so it joins the remapped GM pairs."""
    out: set = set()
    for a, b in truth_to_pairs(truth):
        sa, sb = str(a), str(b)
        out.add((sa, sb) if sa <= sb else (sb, sa))
    return out


def _run_goldenmatch(name, records, truth, out_dir, truth_path, threshold, mods):
    """Returns a panel row dict for the goldenmatch engine on one dataset."""
    datasets, attribution_mod, evaluate_mod = mods
    row: dict = {"dataset": name, "engine": "goldenmatch", "status": "error"}
    try:
        ds_dir = out_dir / name
        gm_pred_path = ds_dir / "gm_pred.parquet"
        dump_dir = ds_dir / "gm_pairs"

        candidate_recordid, emitted_recordid = _gm_predictions_path(
            records, truth, gm_pred_path, dump_dir
        )

        metrics = evaluate_mod.evaluate(gm_pred_path, truth_path)
        gt = _truth_to_string_pairs(attribution_mod.truth_to_pairs, truth)
        attr = attribution_mod.attribution(
            gt, candidate_recordid, emitted_recordid
        )

        pw = metrics["pairwise"]
        bc = metrics["bcubed"]
        row.update(
            status="ok",
            precision=pw["precision"],
            recall=pw["recall"],
            f1=pw["f1"],
            bcubed_f1=bc["f1"],
            blocking_recall=attr["blocking_recall"],
            threshold_loss=attr["threshold_loss"],
            final_recall=attr["final_recall"],
            n_gt_pairs=attr["n_gt_pairs"],
        )
        print(
            f"[panel] goldenmatch {name}: P/R/F1="
            f"{pw['precision']}/{pw['recall']}/{pw['f1']} "
            f"blocking_recall={attr['blocking_recall']} "
            f"threshold_loss={attr['threshold_loss']}"
        )
    except Exception as e:  # noqa: BLE001 - one dataset's failure must not kill the panel
        row["status"] = "error"
        row["error"] = f"{type(e).__name__}: {e}"
        print(f"[panel] goldenmatch {name}: ERROR {row['error']}")
        traceback.print_exc()
    return row


def _run_splink(name, records, out_dir, truth_path, threshold):
    """Run run_splink.py --dataset <name> as a subprocess; return a panel row."""
    row: dict = {"dataset": name, "engine": "splink", "status": "error"}
    ds_dir = out_dir / name
    splink_pred = ds_dir / "splink_pred.parquet"
    splink_json = ds_dir / "splink_result.json"
    try:
        cmd = [
            sys.executable,
            str(_HERE / "run_splink.py"),
            "--dataset", name,
            "--rows", str(records.height),
            "--out", str(splink_json),
            "--pred-out", str(splink_pred),
            "--threshold", str(threshold),
        ]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_SPLINK_TIMEOUT_S
        )
        if proc.stdout:
            print(proc.stdout.rstrip())
        # Read the runner's status JSON if it wrote one.
        runner_status = None
        runner_reason = None
        if splink_json.exists():
            try:
                blob = json.loads(splink_json.read_text())
                runner_status = blob.get("status")
                runner_reason = blob.get("reason") or blob.get("error")
            except Exception:  # noqa: BLE001
                pass

        if proc.returncode != 0:
            row["status"] = "error"
            row["error"] = (
                f"run_splink exited {proc.returncode}: "
                f"{(proc.stderr or '').strip()[-400:]}"
            )
            return row
        if runner_status == "skipped":
            row["status"] = "skipped"
            row["reason"] = runner_reason or "splink skipped"
            return row
        if runner_status != "ok":
            row["status"] = "error"
            row["error"] = runner_reason or f"splink status={runner_status}"
            return row

        # ok -> score the predictions with the SAME evaluator GM used.
        evaluate_mod = _import_sibling("evaluate")
        if not splink_pred.exists():
            row["status"] = "error"
            row["error"] = "splink reported ok but wrote no pred parquet"
            return row
        metrics = evaluate_mod.evaluate(splink_pred, truth_path)
        pw = metrics["pairwise"]
        bc = metrics["bcubed"]
        row.update(
            status="ok",
            precision=pw["precision"],
            recall=pw["recall"],
            f1=pw["f1"],
            bcubed_f1=bc["f1"],
        )
        print(
            f"[panel] splink {name}: P/R/F1="
            f"{pw['precision']}/{pw['recall']}/{pw['f1']}"
        )
    except subprocess.TimeoutExpired:
        row["status"] = "error"
        row["error"] = f"splink timed out after {_SPLINK_TIMEOUT_S}s"
        print(f"[panel] splink {name}: TIMEOUT")
    except Exception as e:  # noqa: BLE001
        row["status"] = "error"
        row["error"] = f"{type(e).__name__}: {e}"
        print(f"[panel] splink {name}: ERROR {row['error']}")
    return row


def _fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


def _render_md(rows: list[dict]) -> str:
    header = (
        "| Dataset | Engine | P | R | F1 | B3-F1 | blocking_recall "
        "| threshold_loss | status |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"
    )
    lines = [header]
    for r in rows:
        lines.append(
            "| {dataset} | {engine} | {p} | {rcl} | {f1} | {b3} | {br} | {tl} | {st} |".format(
                dataset=r.get("dataset", "-"),
                engine=r.get("engine", "-"),
                p=_fmt(r.get("precision")),
                rcl=_fmt(r.get("recall")),
                f1=_fmt(r.get("f1")),
                b3=_fmt(r.get("bcubed_f1")),
                br=_fmt(r.get("blocking_recall")),
                tl=_fmt(r.get("threshold_loss")),
                st=r.get("status", "-")
                + (f" ({r['reason']})" if r.get("status") == "skipped" and r.get("reason") else "")
                + (f" ({r['error']})" if r.get("status") == "error" and r.get("error") else ""),
            )
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--datasets",
        default="historical_50k,dblp_acm,febrl3,ncvr,synthetic_person",
        help="comma-separated dataset names",
    )
    ap.add_argument("--out-dir", default=".profile_tmp/prob_panel", type=Path)
    ap.add_argument("--threshold", type=float, default=0.85)
    ap.add_argument(
        "--engines",
        default="goldenmatch,splink",
        help="comma-separated engines: goldenmatch,splink",
    )
    args = ap.parse_args()

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    names = [n.strip() for n in args.datasets.split(",") if n.strip()]
    engines = [e.strip() for e in args.engines.split(",") if e.strip()]

    datasets = _import_sibling("datasets")
    attribution_mod = _import_sibling("attribution")
    evaluate_mod = _import_sibling("evaluate")
    mods = (datasets, attribution_mod, evaluate_mod)

    rows: list[dict] = []
    for name in names:
        print(f"\n=== dataset: {name} ===")
        try:
            records, truth = datasets.load_dataset(name)
        except datasets.DatasetUnavailable as e:
            reason = f"{type(e).__name__}: {e}"
            print(f"[panel] {name}: unavailable -> skipping all engines ({reason})")
            for eng in engines:
                rows.append(
                    {"dataset": name, "engine": eng, "status": "skipped", "reason": reason}
                )
            continue
        except Exception as e:  # noqa: BLE001 - load failure -> skip, never fatal
            reason = f"{type(e).__name__}: {e}"
            print(f"[panel] {name}: load error -> skipping all engines ({reason})")
            for eng in engines:
                rows.append(
                    {"dataset": name, "engine": eng, "status": "error", "error": reason}
                )
            continue

        import polars as pl

        ds_dir = out_dir / name
        ds_dir.mkdir(parents=True, exist_ok=True)
        truth_path = ds_dir / "truth.parquet"
        # Truth written in string record_id space to join the string-keyed preds.
        truth.with_columns(pl.col("record_id").cast(pl.Utf8)).write_parquet(truth_path)

        if "goldenmatch" in engines:
            rows.append(
                _run_goldenmatch(
                    name, records, truth, out_dir, truth_path, args.threshold, mods
                )
            )
        if "splink" in engines:
            rows.append(
                _run_splink(name, records, out_dir, truth_path, args.threshold)
            )

    # Output: panel.json + panel.md (also printed to stdout).
    (out_dir / "panel.json").write_text(json.dumps(rows, indent=2))
    md = _render_md(rows)
    (out_dir / "panel.md").write_text(md)
    print("\n" + md)


if __name__ == "__main__":
    main()
