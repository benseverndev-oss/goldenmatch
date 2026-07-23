#!/usr/bin/env python
"""Sweep the FS link threshold and report the P/R/F1 curve on a labeled dataset.

The FS decision rule normalizes each pair's weight to [0,1] and merges above a
link threshold that defaults to a FIXED 0.50 (compute_thresholds, non-calibrated
path). Two weight-based over-merge levers (field-dependence, discriminator NE)
were defeated by this min-max normalization; before changing the normalization we
need to know whether the THRESHOLD itself is badly placed — i.e. how much F1 the
default 0.50 leaves on the table, and where it sits on the P/R curve.

For each threshold this re-runs GM (setting ``link_threshold``), clusters, and
scores with the SAME evaluator the panel uses. Reports the curve + the default
(0.50) row + the argmax-F1 threshold. If the best threshold >> the default in F1,
threshold calibration is the lever; if 0.50 is already near-best, the over-merge
is intrinsic to the score distribution (weights), not the cutoff.

Diagnostic only; never fails a job (dataset/dep gaps -> note + exit 0).
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import datasets as ds_mod  # noqa: E402
import evaluate as ev_mod  # noqa: E402

_DEFAULT_THRESHOLDS = [0.30, 0.40, 0.50, 0.55, 0.60, 0.65,
                       0.70, 0.75, 0.80, 0.85, 0.90, 0.95]


def _pred_path(records, cfg, threshold, out_dir):
    """Run GM at ``threshold``, write a {record_id, pred_cluster_id} parquet."""
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq
    from goldenmatch import dedupe_df

    mk = cfg.matchkeys[0]
    try:
        mk.link_threshold = threshold
    except (TypeError, ValueError):
        cfg.matchkeys[0] = mk.model_copy(update={"link_threshold": threshold})
    rid = records.column("record_id").to_pylist()
    ded = dedupe_df(records, config=cfg)
    clusters = getattr(ded, "clusters", None) or {}
    rec_ids, pred_cids = [], []
    for cid, c in clusters.items():
        for m in (c["members"] if isinstance(c, dict) else c.members):
            rec_ids.append(str(rid[m]))
            pred_cids.append(cid)
    p = out_dir / f"pred_{threshold:.2f}.parquet"
    pq.write_table(pa.table({
        "record_id": pa.array(rec_ids, pa.string()),
        "pred_cluster_id": pa.array(np.asarray(pred_cids, dtype=np.int64)),
    }), p, compression="zstd")
    return p


def _sweep_one(dataset, thresholds) -> list[str]:
    lines = [f"## FS threshold sweep — {dataset}", ""]
    try:
        records, truth = ds_mod.load_dataset(dataset)
        from goldenmatch.core.autoconfig import auto_configure_probabilistic_df
    except Exception as e:
        lines.append(f"_unavailable ({type(e).__name__}: {e}); skipped._")
        return lines

    import pyarrow as pa
    import pyarrow.parquet as pq
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        truth_path = d / "truth.parquet"
        pq.write_table(pa.table({
            "record_id": pa.array([str(r) for r in truth.column("record_id").to_pylist()], pa.string()),
            "cluster_id": truth.column("cluster_id"),
        }), truth_path)

        cfg = auto_configure_probabilistic_df(records)
        rows = []
        for t in thresholds:
            try:
                pred = _pred_path(records, cfg, t, d)
                m = ev_mod.evaluate(pred, truth_path)
                # Panel headline F1 = pairwise (matches the OFF/ON panel numbers).
                pw = m.get("pairwise", m)
                rows.append((t, pw.get("precision", 0.0), pw.get("recall", 0.0), pw.get("f1", 0.0)))
            except Exception as e:
                lines.append(f"_threshold {t}: {type(e).__name__}: {e}_")

    if not rows:
        lines.append("_no thresholds scored._")
        return lines

    best = max(rows, key=lambda r: r[3])
    default = next((r for r in rows if abs(r[0] - 0.50) < 1e-9), None)
    lines.append("| link threshold | precision | recall | F1 | |")
    lines.append("| --- | --- | --- | --- | --- |")
    for t, p, r, f in rows:
        tag = ""
        if abs(t - 0.50) < 1e-9:
            tag = "**default**"
        if (t, p, r, f) == best:
            tag = (tag + " **← best F1**").strip()
        lines.append(f"| {t:.2f} | {p:.4f} | {r:.4f} | {f:.4f} | {tag} |")
    lines.append("")
    lines.append("### Verdict")
    lines.append("")
    if default:
        gain = best[3] - default[3]
        lines.append(
            f"- Default 0.50: F1 **{default[3]:.4f}** (P {default[1]:.4f} / R {default[2]:.4f}).")
        lines.append(
            f"- Best {best[0]:.2f}: F1 **{best[3]:.4f}** (P {best[1]:.4f} / R {best[2]:.4f}).")
        lines.append(f"- Threshold headroom: **{gain:+.4f} F1**.")
        verdict = ("STRONG: the default cutoff is badly placed — threshold "
                   "calibration is a real lever."
                   if gain >= 0.01 else
                   "WEAK: 0.50 is already near-optimal; the over-merge is intrinsic "
                   "to the score distribution (weights), not the cutoff.")
        lines.append(f"- **{verdict}**")

    return lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--datasets", default="historical_50k",
                    help="comma-separated dataset names")
    ap.add_argument("--thresholds", default=None,
                    help="comma-separated; default a 0.30..0.95 sweep")
    args = ap.parse_args()
    thresholds = ([float(t) for t in args.thresholds.split(",")]
                  if args.thresholds else _DEFAULT_THRESHOLDS)
    out = []
    for ds in [d.strip() for d in args.datasets.split(",") if d.strip()]:
        out.extend(_sweep_one(ds, thresholds))
        out.append("")
    _emit("\n".join(out) + "\n")
    return 0


def _emit(md: str) -> None:
    print(md)
    step = os.environ.get("GITHUB_STEP_SUMMARY")
    if step:
        with open(step, "a", encoding="utf-8") as fh:
            fh.write(md)


if __name__ == "__main__":
    raise SystemExit(main())
