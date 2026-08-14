#!/usr/bin/env python3
"""Cross-dataset sweep of the FS link threshold: is one constant defensible?

## Why

Under posterior calibration `core.probabilistic.compute_thresholds` returns a
hardcoded `(0.99, 0.50)`. Its docstring says 0.99 is the measured-best cut **on
DBLP-ACM**, and with no `mk.link_threshold` and no `calibrated_link_threshold`
that constant is what every dataset gets -- reported honestly as
`fs_link_thresholds: {source: "fallback"}`.

Measured on a 20K person fixture (`diagnose_fs_recall.py`) that cut costs
F1 0.8772 -> 0.5406 with precision pinned at 1.0000 the whole way down: the
model separates cleanly and the knife is simply in the wrong place.

The open question this answers: is the fix a BETTER CONSTANT, or does the
optimum move enough between datasets that only a dataset-derived cut will do?

## Methodology, and why it is split

0.99 was chosen by looking at one dataset. Sweeping every dataset and taking
the argmax would repeat that mistake with more steps -- it would report the
best constant *for the datasets I looked at*, which is not evidence it
generalises.

So the panel is split the way `datasets.py` already splits it:

* **tune**    -- historical_50k, dblp_acm, febrl3, ncvr, synthetic_person.
  The full grid runs here, and the recommended constant is chosen here.
* **holdout** -- febrl4, dblp_scholar, amazon_google, marked in `datasets.py`
  as "never in the FS-lever tuning panel". Only TWO points run here: the
  incumbent 0.99 and whatever the tuning panel recommended. Sweeping the
  holdout would destroy the only unbiased estimate available.

The verdict is decided on the holdout delta, not the tuning-panel argmax.

## Cost

One full `dedupe_df` per (dataset, threshold) -- not a score-once-then-recluster
shortcut, because clustering can auto-split and a shortcut would report a
pipeline that does not exist. That is why the grid is small and the holdout is
two points.

Usage:
    python scripts/bench_er_headtohead/sweep_fs_link_threshold.py \\
        --out fs-threshold-sweep.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

TUNE = ["historical_50k", "dblp_acm", "febrl3", "ncvr", "synthetic_person"]
HOLDOUT = ["febrl4", "dblp_scholar", "amazon_google"]
GRID = [0.50, 0.70, 0.80, 0.85, 0.90, 0.95, 0.99]
INCUMBENT = 0.99
_BASIC = {"jaro_winkler", "levenshtein", "token_sort", "exact"}


def _write_truth(truth, path: Path):
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    idx = truth.schema.get_field_index("record_id")
    t = truth.set_column(idx, "record_id", pc.cast(truth.column("record_id"), pa.string()))
    pq.write_table(t, path, compression="zstd")


def run_one(records, truth_path: Path, out_dir: Path, thr: float) -> dict:
    """One full dedupe at `thr`, scored by the shared evaluator."""
    import evaluate as evaluate_mod
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq
    from goldenmatch import dedupe_df
    from goldenmatch.core.autoconfig import auto_configure_probabilistic_df

    rid = records.column("record_id").to_pylist()
    cfg = auto_configure_probabilistic_df(records)
    for mk in cfg.get_matchkeys():
        # Same basic-scorer rewrite the FS bench lanes apply, so this sweep
        # measures the model those lanes measure and not a different one.
        for f in getattr(mk, "fields", None) or []:
            if f.scorer and f.scorer not in _BASIC:
                f.scorer = "jaro_winkler"
        mk.link_threshold = thr

    t0 = time.perf_counter()
    ded = dedupe_df(records, config=cfg)
    wall = time.perf_counter() - t0

    rec_ids, pred_cids = [], []
    for cid, c in (getattr(ded, "clusters", None) or {}).items():
        members = c["members"] if isinstance(c, dict) else c.members
        for m in members:
            rec_ids.append(str(rid[m]))
            pred_cids.append(cid)

    pred_path = out_dir / f"pred_{thr}.parquet"
    pq.write_table(
        pa.table({
            "record_id": pa.array(rec_ids, pa.string()),
            "pred_cluster_id": pa.array(np.asarray(pred_cids, dtype=np.int64)),
        }),
        pred_path, compression="zstd",
    )
    m = evaluate_mod.evaluate(pred_path, truth_path)
    pw = m.get("pairwise") or {}
    return {
        "threshold": thr, "wall_seconds": round(wall, 2),
        "precision": pw.get("precision"), "recall": pw.get("recall"),
        "f1": pw.get("f1"),
    }


def sweep(name: str, thresholds: list[float], out_dir: Path) -> list[dict]:
    import datasets as datasets_mod

    records, truth = datasets_mod.load_dataset(name)
    d = out_dir / name
    d.mkdir(parents=True, exist_ok=True)
    truth_path = d / "truth.parquet"
    _write_truth(truth, truth_path)

    rows = []
    for thr in thresholds:
        try:
            r = run_one(records, truth_path, d, thr)
        except Exception as exc:  # noqa: BLE001 - one bad point must not lose the rest
            r = {"threshold": thr, "error": str(exc)[:200]}
        r["dataset"] = name
        rows.append(r)
        if "error" in r:
            note = f"ERROR {r['error']}"
        else:
            note = (f"F1={r['f1']:.4f} P={r['precision']:.4f} "
                    f"R={r['recall']:.4f} ({r['wall_seconds']}s)")
        print(f"[sweep] {name} thr={thr}: {note}", flush=True)
    return rows


def best_constant(rows: list[dict]) -> tuple[float, float]:
    """The threshold maximising MEAN F1 across the tuning datasets.

    Mean, not sum-of-argmax: a constant has to serve every dataset at once, so
    the thing to maximise is what a single knob delivers on average. Datasets
    that errored contribute nothing rather than a zero, which would let one
    failure pick the constant.
    """
    by_thr: dict[float, list[float]] = {}
    for r in rows:
        if r.get("f1") is not None:
            by_thr.setdefault(r["threshold"], []).append(float(r["f1"]))
    if not by_thr:
        return INCUMBENT, 0.0
    scored = {t: sum(v) / len(v) for t, v in by_thr.items()}
    best = max(scored, key=lambda t: scored[t])
    return best, scored[best]


def render_markdown(report: dict) -> str:
    """The job-summary table.

    Rendered here rather than in a YAML heredoc: a heredoc terminator has to sit
    at column 0, which is easy to get wrong inside an indented `run:` block and
    fails at job time rather than at lint time. Formatting in the script also
    means it can be exercised without a runner.
    """
    out = ["## FS link-threshold sweep", ""]
    out.append(
        f"Incumbent **{report.get('incumbent')}** mean F1 "
        f"`{report.get('incumbent_mean_f1')}`  ·  recommended "
        f"**{report.get('recommended')}** mean F1 "
        f"`{report.get('recommended_mean_f1')}`"
    )
    out.append("")
    for section in ("tune", "holdout"):
        rows = report.get(section) or []
        if not rows:
            continue
        out += [f"### {section}", "", "| dataset | thr | P | R | F1 |",
                "|---|---|---|---|---|"]
        for r in rows:
            if r.get("f1") is None:
                out.append(f"| {r.get('dataset')} | {r.get('threshold', '-')} "
                           f"| - | - | ERROR |")
            else:
                out.append(f"| {r['dataset']} | {r['threshold']} "
                           f"| {r['precision']:.4f} | {r['recall']:.4f} "
                           f"| {r['f1']:.4f} |")
        out.append("")
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tune", nargs="*", default=TUNE)
    ap.add_argument("--holdout", nargs="*", default=HOLDOUT)
    ap.add_argument("--grid", nargs="*", type=float, default=GRID)
    ap.add_argument("--out", default="fs-threshold-sweep.json")
    ap.add_argument("--work", default="fs_sweep_work")
    ap.add_argument("--summary-md", default="",
                    help="also render a markdown table here (CI job summary)")
    args = ap.parse_args()

    os.environ.setdefault("GOLDENMATCH_FS_CALIBRATED", "posterior")
    os.environ.setdefault("GOLDENMATCH_FS_NATIVE", "1")

    out_dir = Path(args.work)
    out_dir.mkdir(parents=True, exist_ok=True)
    report: dict = {"grid": args.grid, "incumbent": INCUMBENT,
                    "tune": [], "holdout": []}

    for name in args.tune:
        try:
            report["tune"].extend(sweep(name, args.grid, out_dir))
        except Exception as exc:  # noqa: BLE001
            print(f"[sweep] {name}: LOAD FAILED {exc}", flush=True)
            report["tune"].append({"dataset": name, "error": str(exc)[:200]})

    rec, mean_f1 = best_constant(report["tune"])
    report["recommended"] = rec
    report["recommended_mean_f1"] = round(mean_f1, 4)
    inc_rows = [r for r in report["tune"]
                if r.get("threshold") == INCUMBENT and r.get("f1") is not None]
    report["incumbent_mean_f1"] = (
        round(sum(r["f1"] for r in inc_rows) / len(inc_rows), 4) if inc_rows else None
    )
    print(f"\n[sweep] tuning panel recommends {rec} "
          f"(mean F1 {report['recommended_mean_f1']}) vs incumbent "
          f"{INCUMBENT} (mean F1 {report['incumbent_mean_f1']})\n", flush=True)

    # Holdout: TWO points only. Sweeping here would spend the only unbiased
    # estimate available and leave nothing to check the recommendation against.
    hold_grid = sorted({INCUMBENT, rec})
    for name in args.holdout:
        try:
            report["holdout"].extend(sweep(name, hold_grid, out_dir))
        except Exception as exc:  # noqa: BLE001
            print(f"[sweep] {name}: LOAD FAILED {exc}", flush=True)
            report["holdout"].append({"dataset": name, "error": str(exc)[:200]})

    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"[sweep] wrote {args.out}", flush=True)
    if args.summary_md:
        Path(args.summary_md).write_text(render_markdown(report), encoding="utf-8")
        print(f"[sweep] wrote {args.summary_md}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
