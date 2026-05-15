"""Score gm.dedupe_df output against ground truth via Olivier Binette's
``er-evaluation`` package (https://github.com/OlivierBinette/er-evaluation).

Why a separate scorer
---------------------

The existing scale-audit script computes pair-based F1 via
``goldenmatch.core.evaluate.evaluate_clusters``. ``er-evaluation`` provides
an independent implementation (entity-centric, with bootstrapped uncertainty
estimates) — a useful sanity check that our numbers aren't artifacts of our
own scoring code. Also surfaces additional metrics (B-cubed, cluster-level
error analysis) the internal scorer doesn't expose.

Usage::

    python scripts/eval_er_evaluation.py \\
        --fixture .profile_tmp/scale_fixtures/synthetic_bench.csv \\
        --ground-truth .profile_tmp/scale_fixtures/synthetic_bench.ground_truth.csv \\
        --output .profile_tmp/eval_er_evaluation.json \\
        --summary-md "$GITHUB_STEP_SUMMARY"

The script keeps the ER pipeline (gm.dedupe_df) and the scorer
(er_evaluation) explicitly separated; ``er_evaluation`` is consumed strictly
at evaluation time (AGPL-3.0 — we don't redistribute it).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


def _build_prediction_series(clusters: dict[int, dict]) -> "pd.Series":
    """Map row_id -> cluster_id as a pandas Series.

    ``clusters`` is GoldenMatch's standard shape: ``dict[cluster_id, {"members": [row_id, ...], ...}]``.
    er-evaluation wants a Series indexed by record id with values = cluster id.
    """
    import pandas as pd
    rows: list[tuple[int, int]] = []
    for cid, info in clusters.items():
        for rid in info.get("members", []):
            rows.append((int(rid), int(cid)))
    if not rows:
        return pd.Series(dtype="int64", name="prediction")
    s = pd.Series({rid: cid for rid, cid in rows}, name="prediction")
    s.index.name = "record_id"
    return s


def _build_reference_series(gt_path: Path) -> "pd.Series":
    """Load ground truth ``id,cluster_id`` CSV into a pandas Series.

    Mirrors ``scripts/scale_audit_5m.py::_pairs_from_ground_truth``: the
    ground-truth CSV's ``id`` column is 1-based row order, while
    GoldenMatch's internal ``__row_id__`` is 0-based. Subtract 1 to align.
    """
    import pandas as pd
    df = pd.read_csv(gt_path)
    df["record_id"] = df["id"].astype(int) - 1
    s = pd.Series(df["cluster_id"].astype(int).values, index=df["record_id"], name="reference")
    s.index.name = "record_id"
    return s


def _safe_call(name: str, fn, *args, **kwargs) -> tuple[Any, str | None]:
    """Call a metric fn; return (value, None) on success, (None, err) on failure.

    er-evaluation's API has shifted between releases (estimator vs direct
    forms, different argument names). We try; if it doesn't exist or raises,
    we log and move on rather than aborting the whole eval.
    """
    try:
        return fn(*args, **kwargs), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _resolve_metric(ee, candidates: list[str]):
    """Return the first attribute name from ``candidates`` that exists on the
    er_evaluation module, or None if none do.
    """
    for name in candidates:
        if hasattr(ee, name):
            return getattr(ee, name), name
    return None, None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", type=Path, required=True,
                    help="CSV produced by scripts/scale_audit_5m_generate.py")
    ap.add_argument("--ground-truth", type=Path, required=True,
                    help="Ground-truth CSV with id,cluster_id columns")
    ap.add_argument("--output", type=Path, required=True,
                    help="Where to write the metrics JSON")
    ap.add_argument("--summary-md", type=Path, default=None,
                    help="Optional path to append a Markdown summary "
                         "(typically $GITHUB_STEP_SUMMARY)")
    args = ap.parse_args()

    if not args.fixture.exists():
        sys.exit(f"fixture not found: {args.fixture}")
    if not args.ground_truth.exists():
        sys.exit(f"ground truth not found: {args.ground_truth}")

    args.output.parent.mkdir(parents=True, exist_ok=True)

    # Import after argparse so --help works without the heavy deps.
    import polars as pl
    import pandas as pd
    import goldenmatch as gm
    import er_evaluation as ee  # pyright: ignore[reportMissingImports]
    ee_version = getattr(ee, "__version__", "unknown")

    print(f"er_evaluation version: {ee_version}")
    print(f"Loading fixture {args.fixture}...")
    df = pl.read_csv(args.fixture, ignore_errors=True, infer_schema_length=0)
    if "cluster_id" in df.columns:
        df = df.drop("cluster_id")
    print(f"  {df.height:,} rows x {df.width} cols")

    print("Running gm.dedupe_df(df) zero-config...")
    t0 = time.perf_counter()
    result = gm.dedupe_df(df)
    wall_s = time.perf_counter() - t0
    print(f"  dedupe wall: {wall_s:.1f}s, {len(result.clusters)} clusters")

    print("Building predicted-cluster Series (record_id -> cluster_id)...")
    predictions = _build_prediction_series(result.clusters)
    print(f"  {len(predictions):,} records assigned to clusters")

    print(f"Loading ground truth from {args.ground_truth}...")
    reference = _build_reference_series(args.ground_truth)
    print(f"  {len(reference):,} ground-truth records")

    # Align indices: only score records that appear in BOTH predictions and
    # reference. (Internal __row_id__ is 0..n-1, ground truth is also 0..n-1
    # after the -1 shift; they should match exactly, but cheap to guard.)
    aligned = pd.concat([predictions, reference], axis=1, join="inner")
    pred = aligned["prediction"]
    ref = aligned["reference"]
    print(f"  aligned on {len(aligned):,} records")

    # Try the canonical metric names first, falling back to alternates the
    # package has used over its release history.
    metrics: dict[str, Any] = {}

    pairwise_p, name_p = _resolve_metric(ee, [
        "pairwise_precision_estimator", "pairwise_precision",
    ])
    pairwise_r, name_r = _resolve_metric(ee, [
        "pairwise_recall_estimator", "pairwise_recall",
    ])
    pairwise_f, name_f = _resolve_metric(ee, [
        "pairwise_f1_estimator", "pairwise_f1",
    ])
    bcubed_p, name_bp = _resolve_metric(ee, [
        "b_cubed_precision_estimator", "b_cubed_precision",
    ])
    bcubed_r, name_br = _resolve_metric(ee, [
        "b_cubed_recall_estimator", "b_cubed_recall",
    ])
    bcubed_f, name_bf = _resolve_metric(ee, [
        "b_cubed_f1_estimator", "b_cubed_f1",
    ])
    cluster_p, name_cp = _resolve_metric(ee, [
        "cluster_precision_estimator", "cluster_precision",
    ])
    cluster_r, name_cr = _resolve_metric(ee, [
        "cluster_recall_estimator", "cluster_recall",
    ])

    for label, fn, fname in [
        ("pairwise_precision", pairwise_p, name_p),
        ("pairwise_recall",    pairwise_r, name_r),
        ("pairwise_f1",        pairwise_f, name_f),
        ("b_cubed_precision",  bcubed_p,   name_bp),
        ("b_cubed_recall",     bcubed_r,   name_br),
        ("b_cubed_f1",         bcubed_f,   name_bf),
        ("cluster_precision",  cluster_p,  name_cp),
        ("cluster_recall",     cluster_r,  name_cr),
    ]:
        if fn is None:
            metrics[label] = {"value": None, "error": "not available in er_evaluation"}
            continue
        # Most er-evaluation estimators take (predictions, reference) and
        # return either a scalar or a (point_estimate, std_error) tuple.
        value, err = _safe_call(label, fn, pred, ref)
        if err is not None:
            metrics[label] = {"value": None, "error": err, "fn": fname}
            continue
        if isinstance(value, tuple) and len(value) == 2:
            metrics[label] = {
                "value": float(value[0]),
                "std_error": float(value[1]) if value[1] is not None else None,
                "fn": fname,
            }
        else:
            metrics[label] = {"value": float(value), "fn": fname}

    # Summary statistics
    summary_fn, _ = _resolve_metric(ee, ["summary_statistics"])
    if summary_fn is not None:
        summary_value, err = _safe_call("summary_statistics", summary_fn, pred)
        if err is None and summary_value is not None:
            # Convert pandas Series/DataFrame to dict for JSON serialization
            try:
                if hasattr(summary_value, "to_dict"):
                    metrics["summary_statistics"] = summary_value.to_dict()
                else:
                    metrics["summary_statistics"] = dict(summary_value)
            except Exception:
                metrics["summary_statistics"] = str(summary_value)

    report = {
        "fixture": str(args.fixture),
        "ground_truth": str(args.ground_truth),
        "n_rows": df.height,
        "n_predicted_clusters": len(result.clusters),
        "n_aligned_records": int(len(aligned)),
        "dedupe_wall_seconds": round(wall_s, 2),
        "er_evaluation_version": ee_version,
        "metrics": metrics,
    }
    args.output.write_text(json.dumps(report, indent=2))
    print(f"\nwrote {args.output}")
    for label, m in metrics.items():
        if isinstance(m, dict) and "value" in m and m["value"] is not None:
            se = m.get("std_error")
            tail = f"  (SE={se:.4f})" if se is not None else ""
            print(f"  {label}: {m['value']:.4f}{tail}")
        elif isinstance(m, dict) and m.get("error"):
            print(f"  {label}: SKIPPED ({m['error']})")

    if args.summary_md is not None:
        with args.summary_md.open("a", encoding="utf-8") as f:
            f.write(f"# eval-er-evaluation\n\n")
            f.write(f"- fixture: `{args.fixture}` ({df.height:,} rows)\n")
            f.write(f"- dedupe wall: **{wall_s:.1f}s**\n")
            f.write(f"- predicted clusters: {len(result.clusters):,}\n")
            f.write(f"- er-evaluation version: `{ee_version}`\n\n")
            f.write("## Metrics\n\n")
            f.write("| metric | value | std error |\n")
            f.write("|---|---|---|\n")
            for label, m in metrics.items():
                if not isinstance(m, dict):
                    continue
                if m.get("value") is None:
                    f.write(f"| `{label}` | n/a ({m.get('error', '?')}) | - |\n")
                else:
                    se = m.get("std_error")
                    se_str = f"{se:.4f}" if se is not None else "-"
                    f.write(f"| `{label}` | {m['value']:.4f} | {se_str} |\n")
            f.write("\n")


if __name__ == "__main__":
    main()
