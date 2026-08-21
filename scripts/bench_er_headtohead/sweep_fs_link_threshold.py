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

* **tune**    -- historical_50k, dblp_acm, febrl3, synthetic_person. The full
  grid runs here, and the recommended constant is chosen here. NCVR is NOT in
  this list: its loader refuses by design (the raw sample's `ncid` is unique
  per row, so there is no true-entity grouping and it will not fabricate one).
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

# NCVR omitted deliberately -- see the module docstring; its loader refuses.
TUNE = ["historical_50k", "dblp_acm", "febrl3", "synthetic_person"]
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


def run_one(records, truth_path: Path, out_dir: Path, thr: float | None) -> dict:
    """One full dedupe at `thr`, scored by the shared evaluator.

    ``thr=None`` is the CALIBRATED arm: `link_threshold` is left unset so the
    engine derives the cut per dataset (`GOLDENMATCH_FS_CALIBRATE_THRESHOLD=1`),
    and the cut it actually chose is read back off the result and reported. That
    read-back is the point -- a calibrated run that silently fell back to the
    fixed default would otherwise look like a calibration result.
    """
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
        if thr is not None:
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

    pred_path = out_dir / f"pred_{'calibrated' if thr is None else thr}.parquet"
    pq.write_table(
        pa.table({
            "record_id": pa.array(rec_ids, pa.string()),
            "pred_cluster_id": pa.array(np.asarray(pred_cids, dtype=np.int64)),
        }),
        pred_path, compression="zstd",
    )
    m = evaluate_mod.evaluate(pred_path, truth_path)
    pw = m.get("pairwise") or {}
    row = {
        "threshold": "calibrated" if thr is None else thr,
        "wall_seconds": round(wall, 2),
        "precision": pw.get("precision"), "recall": pw.get("recall"),
        "f1": pw.get("f1"),
    }
    if thr is None:
        # What the engine CHOSE, and whether it chose at all. `source` is the
        # engine's own report: "calibrated" means this dataset picked a cut,
        # "fallback" means nothing about it did and the number below is the
        # fixed default wearing a calibration label.
        stats = (getattr(ded, "stats", None) or {}).get("fs_link_thresholds") or {}
        first = next(iter(stats.values()), {}) if isinstance(stats, dict) else {}
        row["chosen_threshold"] = first.get("link_threshold")
        row["threshold_source"] = first.get("source")
    return row


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
            if r.get("chosen_threshold") is not None:
                note += (f"  [chose {r['chosen_threshold']} "
                         f"src={r.get('threshold_source')}]")
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
        # Skip the calibrated rows: "calibrated" is not a constant, and letting
        # it into this argmax would report a per-dataset policy as if it were
        # one number every dataset could be given.
        if r.get("f1") is not None and isinstance(r.get("threshold"), (int, float)):
            by_thr.setdefault(float(r["threshold"]), []).append(float(r["f1"]))
    if not by_thr:
        return INCUMBENT, 0.0
    scored = {t: sum(v) / len(v) for t, v in by_thr.items()}
    best = max(scored, key=lambda t: scored[t])
    return best, scored[best]


def calibrated_verdict(rows: list[dict]) -> list[dict]:
    """Per dataset: the calibrated cut vs the BEST FIXED cut that dataset could
    have had.

    This is the honest bar, and it is deliberately harsh. The best fixed cut per
    dataset is an ORACLE -- it is chosen with knowledge of that dataset's own
    ground truth, which no real deployment has. If calibration merely ties it,
    calibration wins in practice, because the oracle is not available and the
    shipped alternative is one global constant that (measured, run 31837064581)
    is wrong for most datasets.

    So the number to read is `delta_vs_oracle`: at or near zero means
    calibration recovers a per-dataset optimum without being told the answer.
    """
    by_ds: dict[str, dict] = {}
    for r in rows:
        if r.get("f1") is None:
            continue
        d = by_ds.setdefault(r["dataset"], {"fixed": {}, "cal": None})
        if isinstance(r.get("threshold"), (int, float)):
            d["fixed"][float(r["threshold"])] = float(r["f1"])
        else:
            d["cal"] = r
    out = []
    for ds, d in sorted(by_ds.items()):
        if not d["fixed"] or d["cal"] is None:
            continue
        best_thr = max(d["fixed"], key=lambda t: d["fixed"][t])
        out.append({
            "dataset": ds,
            "oracle_threshold": best_thr,
            "oracle_f1": round(d["fixed"][best_thr], 4),
            "calibrated_f1": round(float(d["cal"]["f1"]), 4),
            "chosen_threshold": d["cal"].get("chosen_threshold"),
            "threshold_source": d["cal"].get("threshold_source"),
            "incumbent_f1": (round(d["fixed"][INCUMBENT], 4)
                             if INCUMBENT in d["fixed"] else None),
            "delta_vs_oracle": round(float(d["cal"]["f1"]) - d["fixed"][best_thr], 4),
        })
    return out


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
    for section in ("verdict_tune", "verdict_holdout"):
        vs = report.get(section) or []
        if not vs:
            continue
        out += [f"### {section.replace('verdict_', '')}: calibrated vs the "
                f"best fixed cut that dataset could have had", "",
                "| dataset | oracle thr | oracle F1 | calibrated F1 | chose | src | delta |",
                "|---|---|---|---|---|---|---|"]
        for v in vs:
            out.append(
                f"| {v['dataset']} | {v['oracle_threshold']} | {v['oracle_f1']} "
                f"| {v['calibrated_f1']} | {v['chosen_threshold']} "
                f"| {v['threshold_source']} | {v['delta_vs_oracle']:+.4f} |"
            )
        out.append("")

    for section in ("tune", "holdout"):
        rows = report.get(section) or []
        if not rows:
            continue
        out += [f"### {section} (full grid)", "", "| dataset | thr | P | R | F1 |",
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
    # Floors, not preferences. Below these the run cannot answer the question
    # it was dispatched to answer.
    ap.add_argument("--calibrated", action="store_true", default=True,
                    help="also run the per-dataset calibrated cut (default on)")
    ap.add_argument("--no-calibrated", dest="calibrated", action="store_false")
    ap.add_argument("--min-tune", type=int, default=3)
    ap.add_argument("--min-holdout", type=int, default=2)
    args = ap.parse_args()

    os.environ.setdefault("GOLDENMATCH_FS_CALIBRATED", "posterior")
    os.environ.setdefault("GOLDENMATCH_FS_NATIVE", "1")

    out_dir = Path(args.work)
    out_dir.mkdir(parents=True, exist_ok=True)
    report: dict = {"grid": args.grid, "incumbent": INCUMBENT,
                    "tune": [], "holdout": []}

    # `None` = the calibrated arm. It runs on EVERY dataset, tune and holdout
    # alike, because the question is not "which constant" any more -- it is
    # "does a per-dataset cut beat the best constant that dataset could have
    # had". That comparison needs the calibrated point beside the full grid.
    grid = list(args.grid) + ([None] if args.calibrated else [])
    for name in args.tune:
        try:
            report["tune"].extend(sweep(name, grid, out_dir))
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
    hold_grid = sorted({INCUMBENT, rec}) + ([None] if args.calibrated else [])
    for name in args.holdout:
        try:
            report["holdout"].extend(sweep(name, hold_grid, out_dir))
        except Exception as exc:  # noqa: BLE001
            print(f"[sweep] {name}: LOAD FAILED {exc}", flush=True)
            report["holdout"].append({"dataset": name, "error": str(exc)[:200]})

    report["verdict_tune"] = calibrated_verdict(report["tune"])
    report["verdict_holdout"] = calibrated_verdict(report["holdout"])
    for section in ("verdict_tune", "verdict_holdout"):
        for v in report[section]:
            print(f"[verdict] {v['dataset']:<16} oracle {v['oracle_threshold']} "
                  f"F1={v['oracle_f1']}  calibrated F1={v['calibrated_f1']} "
                  f"(chose {v['chosen_threshold']}, {v['threshold_source']})  "
                  f"delta={v['delta_vs_oracle']:+.4f}", flush=True)

    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"[sweep] wrote {args.out}", flush=True)
    if args.summary_md:
        Path(args.summary_md).write_text(render_markdown(report), encoding="utf-8")
        print(f"[sweep] wrote {args.summary_md}", flush=True)

    # A recommendation nothing checked is worse than no recommendation: the
    # FIRST run of this sweep lost six of eight datasets to missing loaders and
    # still printed "recommended 0.5", fitted on two datasets that disagreed
    # with each other, with an EMPTY holdout. The artifact looked like a result.
    #
    # So the exit code now reflects whether the methodology held, not whether
    # the process crashed. `--min-tune` / `--min-holdout` are the floor; the
    # numbers stay in the artifact either way for diagnosis.
    ok_tune = {r["dataset"] for r in report["tune"] if r.get("f1") is not None}
    ok_hold = {r["dataset"] for r in report["holdout"] if r.get("f1") is not None}
    report["usable_tune_datasets"] = sorted(ok_tune)
    report["usable_holdout_datasets"] = sorted(ok_hold)
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")

    if len(ok_tune) < args.min_tune or len(ok_hold) < args.min_holdout:
        print(
            f"[sweep] REFUSING the recommendation: {len(ok_tune)} usable tuning "
            f"dataset(s) (need {args.min_tune}) and {len(ok_hold)} usable "
            f"holdout dataset(s) (need {args.min_holdout}). A constant chosen "
            f"on too few datasets, or with nothing held out to check it, is "
            f"how the incumbent 0.99 happened in the first place.",
            flush=True,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
