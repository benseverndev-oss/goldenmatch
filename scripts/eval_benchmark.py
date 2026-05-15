"""Score a canonical ER benchmark via Olivier Binette's ``er-evaluation``.

Dispatches by ``--benchmark`` to one of:

- ``febrl3``    — synthetic person data, dedupe. Loaded via ``recordlinkage.datasets.load_febrl3(return_links=True)``.
- ``dblp-acm``  — bibliographic cross-source match. Loaded from local ``tests/benchmarks/datasets/DBLP-ACM/{DBLP2.csv,ACM.csv,DBLP-ACM_perfectMapping.csv}``.
- ``ncvr``      — North Carolina voter, dedupe. Loaded from local ``tests/benchmarks/datasets/NCVR/ncvoter_sample_10k.txt``.

Each path produces two pandas Series (predictions, reference) keyed by a
combined record id, then calls the same er-evaluation scoring path used
by ``scripts/eval_er_evaluation.py``. Emits JSON + Markdown summary.

Usage::

    python scripts/eval_benchmark.py --benchmark febrl3 \\
        --output .profile_tmp/eval_febrl3.json \\
        --summary-md "$GITHUB_STEP_SUMMARY"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


# ── Scoring (mirrors scripts/eval_er_evaluation.py, factored for reuse) ─────

def _safe_call(fn, *args, **kwargs) -> tuple[Any, str | None]:
    try:
        return fn(*args, **kwargs), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _resolve_metric(ee, candidates: list[str]):
    for name in candidates:
        if hasattr(ee, name):
            return getattr(ee, name), name
    return None, None


def score_predictions(
    pred: "pd.Series",
    ref: "pd.Series",
    label: str,
) -> dict[str, Any]:
    """Run er-evaluation metrics on aligned (predictions, reference) Series.

    Returns a dict matching the ``metrics`` shape of
    ``scripts/eval_er_evaluation.py`` so downstream tooling can consume
    either output identically.
    """
    import pandas as pd
    import er_evaluation as ee  # pyright: ignore[reportMissingImports]

    # Align on intersection of indices; record_ids must appear in both.
    aligned = pd.concat([pred.rename("prediction"), ref.rename("reference")],
                        axis=1, join="inner")
    pred_a = aligned["prediction"]
    ref_a = aligned["reference"]
    print(f"[{label}] aligned on {len(aligned):,} records")

    # er-evaluation 2.x estimators take (predictions, reference, weights).
    weights_fn, _ = _resolve_metric(ee, ["weights"])
    weights = None
    if weights_fn is not None:
        try:
            weights = weights_fn(ref_a, "uniform")
        except Exception:
            try:
                weights = weights_fn(ref_a, weights="uniform")
            except Exception:
                weights = None
    if weights is None:
        weights = pd.Series(1.0, index=ref_a.unique(), name="weights")
        weights.index.name = "cluster_id"

    metrics: dict[str, Any] = {}
    metric_specs = [
        ("pairwise_precision", ["pairwise_precision_estimator", "pairwise_precision"]),
        ("pairwise_recall",    ["pairwise_recall_estimator",    "pairwise_recall"]),
        ("b_cubed_precision",  ["b_cubed_precision_estimator",  "b_cubed_precision"]),
        ("b_cubed_recall",     ["b_cubed_recall_estimator",     "b_cubed_recall"]),
        ("cluster_precision",  ["cluster_precision_estimator",  "cluster_precision"]),
        ("cluster_recall",     ["cluster_recall_estimator",     "cluster_recall"]),
    ]
    for mname, candidates in metric_specs:
        fn, fname = _resolve_metric(ee, candidates)
        if fn is None:
            metrics[mname] = {"value": None, "error": "not available in er_evaluation"}
            continue
        value, err = _safe_call(fn, pred_a, ref_a, weights)
        if err is not None:
            value, err = _safe_call(fn, pred_a, ref_a)
        if err is not None:
            metrics[mname] = {"value": None, "error": err, "fn": fname}
            continue
        if isinstance(value, tuple) and len(value) == 2:
            metrics[mname] = {
                "value": float(value[0]),
                "std_error": float(value[1]) if value[1] is not None else None,
                "fn": fname,
            }
        else:
            metrics[mname] = {"value": float(value), "fn": fname}

    # Derive F1 from P + R (er-evaluation 2.3.0 ships no F1 estimator).
    for prefix in ("pairwise", "b_cubed", "cluster"):
        p = metrics.get(f"{prefix}_precision", {}).get("value")
        r = metrics.get(f"{prefix}_recall", {}).get("value")
        if p is not None and r is not None and (p + r) > 0:
            f1 = 2 * p * r / (p + r)
            f1_entry: dict[str, Any] = {
                "value": f1, "fn": "derived from precision + recall",
            }
            se_p = metrics[f"{prefix}_precision"].get("std_error")
            se_r = metrics[f"{prefix}_recall"].get("std_error")
            if se_p is not None and se_r is not None:
                dp = 2 * r * r / (p + r) ** 2
                dr = 2 * p * p / (p + r) ** 2
                f1_entry["std_error"] = (
                    (dp * se_p) ** 2 + (dr * se_r) ** 2
                ) ** 0.5
            metrics[f"{prefix}_f1"] = f1_entry
        else:
            metrics[f"{prefix}_f1"] = {
                "value": None,
                "error": "precision or recall unavailable",
            }
    return metrics


# ── Per-benchmark loaders ──────────────────────────────────────────────────

def _clusters_to_series(clusters: dict[int, dict], all_ids: list[int]) -> "pd.Series":
    """Map every member -> cluster_id. Unassigned ids get unique singleton ids."""
    import pandas as pd
    out: dict[int, int] = {}
    for cid, info in clusters.items():
        for rid in info.get("members", []):
            out[int(rid)] = int(cid)
    # Assign singletons for any record not in a multi-member cluster.
    next_cid = (max(out.values()) if out else 0) + 1
    for rid in all_ids:
        if rid not in out:
            out[rid] = next_cid
            next_cid += 1
    s = pd.Series(out, name="prediction")
    s.index.name = "record_id"
    return s


def _gt_pairs_to_series(
    pairs: set[tuple[Any, Any]],
    all_ids: list[Any],
) -> "pd.Series":
    """Collapse ground-truth pair set into clusters via union-find, emit Series.

    Records not appearing in any pair become singleton clusters with unique ids.
    """
    import pandas as pd

    parent: dict[Any, Any] = {rid: rid for rid in all_ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    for a, b in pairs:
        if a in parent and b in parent:
            union(a, b)

    # Canonicalize cluster ids: root -> contiguous int.
    root_to_cid: dict[Any, int] = {}
    next_cid = 1
    out: dict[Any, int] = {}
    for rid in all_ids:
        root = find(rid)
        if root not in root_to_cid:
            root_to_cid[root] = next_cid
            next_cid += 1
        out[rid] = root_to_cid[root]

    s = pd.Series(out, name="reference")
    s.index.name = "record_id"
    return s


def load_febrl3() -> tuple["pd.Series", "pd.Series", float, dict[str, Any]]:
    """Run gm.dedupe_df on Febrl3, return (predictions, reference, wall_s, extras)."""
    import polars as pl
    import pandas as pd
    import goldenmatch as gm
    from recordlinkage.datasets import load_febrl3 as _load
    df_pd, gt_pairs = _load(return_links=True)
    df_pd = df_pd.reset_index()
    df = pl.from_pandas(df_pd)
    print(f"[febrl3] loaded {df.height:,} rows, {len(gt_pairs):,} GT pairs")

    t0 = time.perf_counter()
    result = gm.dedupe_df(df)
    wall_s = time.perf_counter() - t0
    print(f"[febrl3] dedupe wall: {wall_s:.1f}s, {len(result.clusters)} clusters")

    # Both predictions and reference use the same __row_id__ space (0..n-1
    # after reset_index, since GM assigns __row_id__ by row order).
    all_ids = list(range(df.height))
    predictions = _clusters_to_series(result.clusters, all_ids)

    # gt_pairs is a pandas MultiIndex of (rec_id_1, rec_id_2). The original
    # Febrl3 row labels need to be mapped to GM's positional __row_id__.
    rec_id_col = "rec_id" if "rec_id" in df_pd.columns else df_pd.columns[0]
    rec_id_to_row: dict[Any, int] = {
        rid: i for i, rid in enumerate(df_pd[rec_id_col].tolist())
    }
    gt_pair_rows: set[tuple[int, int]] = set()
    for a, b in gt_pairs:
        if a in rec_id_to_row and b in rec_id_to_row:
            ra, rb = rec_id_to_row[a], rec_id_to_row[b]
            gt_pair_rows.add((min(ra, rb), max(ra, rb)))
    reference = _gt_pairs_to_series(gt_pair_rows, all_ids)

    extras = {
        "n_rows": df.height,
        "n_gt_pairs": len(gt_pairs),
        "n_predicted_clusters": len(result.clusters),
    }
    return predictions, reference, wall_s, extras


def load_dblp_acm(datasets_dir: Path) -> tuple["pd.Series", "pd.Series", float, dict[str, Any]]:
    """Run gm.match_df on DBLP-ACM, return predictions/reference/wall.

    Cross-source match: DBLP rows go into id space "a:{id}", ACM rows go
    into "b:{id}". Matched pairs collapse into clusters; everything else is
    a singleton.
    """
    import polars as pl
    import goldenmatch as gm

    a_path = datasets_dir / "DBLP-ACM" / "DBLP2.csv"
    b_path = datasets_dir / "DBLP-ACM" / "ACM.csv"
    gt_path = datasets_dir / "DBLP-ACM" / "DBLP-ACM_perfectMapping.csv"
    for p in (a_path, b_path, gt_path):
        if not p.exists():
            sys.exit(f"DBLP-ACM file missing: {p}")

    df_a = pl.read_csv(a_path, encoding="utf8-lossy")
    df_b = pl.read_csv(b_path, encoding="utf8-lossy")
    print(f"[dblp-acm] DBLP: {df_a.height:,} rows  ACM: {df_b.height:,} rows")

    # Common columns for matching across both sources.
    common = [c for c in df_a.columns if c in df_b.columns]
    df_a = df_a.select(common)
    df_b = df_b.select(common)

    t0 = time.perf_counter()
    result = gm.match_df(df_a, df_b)
    wall_s = time.perf_counter() - t0

    # Build combined id space and predictions.
    id_col = "id" if "id" in df_a.columns else df_a.columns[0]
    a_ids = [f"a:{x}" for x in df_a[id_col].to_list()]
    b_ids = [f"b:{x}" for x in df_b[id_col].to_list()]
    all_ids = a_ids + b_ids

    # Position-based: __row_id__ in target df = 0..n_a-1, in ref df = 0..n_b-1.
    # MatchResult.matched contains (target_row_id, ref_row_id) pairs.
    matched_pairs_pos: set[tuple[int, int]] = set()
    matched = getattr(result, "matched", None)
    if matched is None:
        matched = getattr(result, "matches", None)
    if hasattr(matched, "iter_rows"):
        # Polars DataFrame
        for row in matched.iter_rows(named=True):
            ta = row.get("target_id") if "target_id" in matched.columns else row.get("id_a")
            rb = row.get("reference_id") if "reference_id" in matched.columns else row.get("id_b")
            if ta is None or rb is None:
                continue
            matched_pairs_pos.add((int(ta), int(rb)))
    elif isinstance(matched, list):
        for item in matched:
            ta = item.get("target_id") if isinstance(item, dict) else item[0]
            rb = item.get("reference_id") if isinstance(item, dict) else item[1]
            matched_pairs_pos.add((int(ta), int(rb)))

    # Convert positional pairs to combined id space.
    matched_pairs_combined: set[tuple[str, str]] = set()
    for ta, rb in matched_pairs_pos:
        if 0 <= ta < len(a_ids) and 0 <= rb < len(b_ids):
            matched_pairs_combined.add((a_ids[ta], b_ids[rb]))

    # Build predictions from matched pairs (union-find over combined id space).
    predictions = _gt_pairs_to_series(matched_pairs_combined, all_ids)
    predictions = predictions.rename("prediction")

    # Ground truth: idDBLP,idACM pairs.
    import polars as pl
    gt = pl.read_csv(gt_path)
    gt_col_a = next((c for c in gt.columns if "DBLP" in c or "idA" in c), gt.columns[0])
    gt_col_b = next((c for c in gt.columns if "ACM" in c or "idB" in c), gt.columns[1])
    gt_pair_combined: set[tuple[str, str]] = set()
    for r in gt.iter_rows(named=True):
        a = f"a:{r[gt_col_a]}"
        b = f"b:{r[gt_col_b]}"
        gt_pair_combined.add((a, b))

    reference = _gt_pairs_to_series(gt_pair_combined, all_ids)

    print(f"[dblp-acm] dedupe wall: {wall_s:.1f}s, "
          f"{len(matched_pairs_combined):,} predicted matches, "
          f"{len(gt_pair_combined):,} GT pairs")

    extras = {
        "n_rows_a": df_a.height,
        "n_rows_b": df_b.height,
        "n_gt_pairs": len(gt_pair_combined),
        "n_predicted_pairs": len(matched_pairs_combined),
    }
    return predictions, reference, wall_s, extras


def load_ncvr(datasets_dir: Path) -> tuple["pd.Series", "pd.Series", float, dict[str, Any]]:
    """Run gm.dedupe_df on the NCVR 10K sample.

    Ground truth: per CLAUDE.md, NCVR's ``ncid`` column is the natural key
    that survives across registrations. Rows with the same ``ncid`` are
    the same voter (re-registrations after a move, etc.).
    """
    import polars as pl
    import goldenmatch as gm
    sample = datasets_dir / "NCVR" / "ncvoter_sample_10k.txt"
    if not sample.exists():
        sys.exit(f"NCVR sample missing: {sample}")
    df = pl.read_csv(sample, separator="\t", ignore_errors=True, infer_schema_length=0)
    print(f"[ncvr] loaded {df.height:,} rows")

    if "ncid" not in df.columns:
        sys.exit("NCVR sample missing 'ncid' column for ground truth")
    gt_clusters = {}
    for i, ncid in enumerate(df["ncid"].to_list()):
        gt_clusters.setdefault(ncid, []).append(i)
    gt_pairs: set[tuple[int, int]] = set()
    for members in gt_clusters.values():
        if len(members) < 2:
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                gt_pairs.add((members[i], members[j]))

    # Drop ncid so it doesn't leak as a matchkey.
    df_for_match = df.drop("ncid")
    t0 = time.perf_counter()
    result = gm.dedupe_df(df_for_match)
    wall_s = time.perf_counter() - t0
    print(f"[ncvr] dedupe wall: {wall_s:.1f}s, {len(result.clusters)} clusters, "
          f"{len(gt_pairs):,} GT pairs")

    all_ids = list(range(df.height))
    predictions = _clusters_to_series(result.clusters, all_ids)
    reference = _gt_pairs_to_series(gt_pairs, all_ids)

    extras = {
        "n_rows": df.height,
        "n_gt_pairs": len(gt_pairs),
        "n_predicted_clusters": len(result.clusters),
        "n_gt_clusters": len(gt_clusters),
    }
    return predictions, reference, wall_s, extras


# ── Main ───────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--benchmark", required=True,
                    choices=["febrl3", "dblp-acm", "ncvr"])
    ap.add_argument("--datasets-dir", type=Path,
                    default=Path("packages/python/goldenmatch/tests/benchmarks/datasets"))
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--summary-md", type=Path, default=None)
    args = ap.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)

    print(f"running benchmark: {args.benchmark}")
    if args.benchmark == "febrl3":
        pred, ref, wall_s, extras = load_febrl3()
    elif args.benchmark == "dblp-acm":
        pred, ref, wall_s, extras = load_dblp_acm(args.datasets_dir)
    elif args.benchmark == "ncvr":
        pred, ref, wall_s, extras = load_ncvr(args.datasets_dir)
    else:
        sys.exit(f"unknown benchmark: {args.benchmark}")

    import er_evaluation as ee  # pyright: ignore[reportMissingImports]
    ee_version = getattr(ee, "__version__", "unknown")
    print(f"er_evaluation version: {ee_version}")

    metrics = score_predictions(pred, ref, args.benchmark)

    report = {
        "benchmark": args.benchmark,
        "dedupe_wall_seconds": round(wall_s, 2),
        "er_evaluation_version": ee_version,
        "extras": extras,
        "metrics": metrics,
    }
    args.output.write_text(json.dumps(report, indent=2))
    print(f"wrote {args.output}")

    for name, m in metrics.items():
        if isinstance(m, dict) and m.get("value") is not None:
            se = m.get("std_error")
            tail = f"  (SE={se:.4f})" if se is not None else ""
            print(f"  {name}: {m['value']:.4f}{tail}")
        elif isinstance(m, dict) and m.get("error"):
            print(f"  {name}: SKIPPED ({m['error']})")

    if args.summary_md is not None:
        with args.summary_md.open("a", encoding="utf-8") as f:
            f.write(f"# eval-benchmark: `{args.benchmark}`\n\n")
            f.write(f"- dedupe wall: **{wall_s:.1f}s**\n")
            f.write(f"- er-evaluation: `{ee_version}`\n")
            for k, v in extras.items():
                f.write(f"- {k}: {v:,}\n" if isinstance(v, int) else f"- {k}: {v}\n")
            f.write("\n## Metrics\n\n| metric | value | std error |\n|---|---|---|\n")
            for name, m in metrics.items():
                if not isinstance(m, dict):
                    continue
                if m.get("value") is None:
                    f.write(f"| `{name}` | n/a ({m.get('error', '?')}) | - |\n")
                else:
                    se = m.get("std_error")
                    se_str = f"{se:.4f}" if se is not None else "-"
                    f.write(f"| `{name}` | {m['value']:.4f} | {se_str} |\n")
            f.write("\n")


if __name__ == "__main__":
    main()
