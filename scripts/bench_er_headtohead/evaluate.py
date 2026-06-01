#!/usr/bin/env python
"""Engine-agnostic ER accuracy evaluator for the head-to-head bench.

Scores a predicted clustering against ground truth using ONE implementation, so
both engines are judged by identical code. Inputs are two parquets:
    predictions: {record_id, pred_cluster_id}   (every record assigned)
    truth:       {record_id, cluster_id}

Computes, via a DuckDB contingency table (no pair materialization -> bounded
memory at 25M/100M):

  * Pairwise: precision / recall / F1 + confusion matrix {tp, fp, fn, tn}.
        TP = sum_ij C(n_ij, 2)              (n_ij = records in pred i AND true j)
        TP+FP = sum_i C(a_i, 2)             (a_i = size of pred cluster i)
        TP+FN = sum_j C(b_j, 2)             (b_j = size of true cluster j)
  * B-cubed: precision / recall / F1.
        B3_precision = (1/N) sum_ij n_ij^2 / a_i
        B3_recall    = (1/N) sum_ij n_ij^2 / b_j

The contingency table has one row per co-occurring (pred, true) pair; DuckDB
streams the group-bys, so peak memory is governed by distinct cluster pairs, not
the N^2 pair space.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def _f1(p: float, r: float) -> float:
    return (2 * p * r / (p + r)) if (p + r) else 0.0


def evaluate(pred: Path, truth: Path) -> dict:
    import duckdb

    con = duckdb.connect()
    con.execute(
        f"""
        CREATE TEMP TABLE cont AS
        SELECT p.pred_cluster_id AS pc, t.cluster_id AS tc, count(*) AS n
        FROM read_parquet('{pred}') p
        JOIN read_parquet('{truth}') t ON p.record_id = t.record_id
        GROUP BY 1, 2;
        CREATE TEMP TABLE psize AS SELECT pc, sum(n) AS a FROM cont GROUP BY pc;
        CREATE TEMP TABLE tsize AS SELECT tc, sum(n) AS b FROM cont GROUP BY tc;
        """
    )
    (n_records, tp, pred_pairs, true_pairs, bc_p_num, bc_r_num, n_pred, n_true) = con.execute(
        """
        SELECT
          (SELECT sum(n)::DOUBLE FROM cont),
          (SELECT sum(n*(n-1)/2.0) FROM cont),
          (SELECT sum(a*(a-1)/2.0) FROM psize),
          (SELECT sum(b*(b-1)/2.0) FROM tsize),
          (SELECT sum((c.n*c.n*1.0)/s.a) FROM cont c JOIN psize s USING (pc)),
          (SELECT sum((c.n*c.n*1.0)/s.b) FROM cont c JOIN tsize s USING (tc)),
          (SELECT count(*) FROM psize),
          (SELECT count(*) FROM tsize)
        """
    ).fetchone()
    con.close()

    n_records = float(n_records or 0)
    tp = float(tp or 0.0)
    pred_pairs = float(pred_pairs or 0.0)
    true_pairs = float(true_pairs or 0.0)
    fp = pred_pairs - tp
    fn = true_pairs - tp
    total_pairs = n_records * (n_records - 1) / 2.0
    tn = total_pairs - tp - fp - fn

    pw_precision = tp / pred_pairs if pred_pairs else 0.0
    pw_recall = tp / true_pairs if true_pairs else 0.0
    bc_precision = (bc_p_num / n_records) if n_records else 0.0
    bc_recall = (bc_r_num / n_records) if n_records else 0.0

    return {
        "n_records_evaluated": int(n_records),
        "pred_clusters": int(n_pred or 0),
        "true_clusters": int(n_true or 0),
        "pairwise": {
            "precision": round(pw_precision, 4),
            "recall": round(pw_recall, 4),
            "f1": round(_f1(pw_precision, pw_recall), 4),
            "accuracy": round((tp + tn) / total_pairs, 6) if total_pairs else 0.0,
            "confusion": {"tp": int(tp), "fp": int(fp), "fn": int(fn), "tn": int(tn)},
        },
        "bcubed": {
            "precision": round(bc_precision, 4),
            "recall": round(bc_recall, 4),
            "f1": round(_f1(bc_precision, bc_recall), 4),
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", type=Path, required=True)
    ap.add_argument("--truth", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    metrics = evaluate(args.pred, args.truth)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_suffix(args.out.suffix + ".tmp")
    tmp.write_text(json.dumps(metrics, indent=2))
    os.replace(tmp, args.out)
    pw, bc = metrics["pairwise"], metrics["bcubed"]
    print(
        f"[evaluate] pairwise P/R/F1={pw['precision']}/{pw['recall']}/{pw['f1']} "
        f"| B3 P/R/F1={bc['precision']}/{bc['recall']}/{bc['f1']} "
        f"| TP/FP/FN={pw['confusion']['tp']}/{pw['confusion']['fp']}/{pw['confusion']['fn']}"
    )


if __name__ == "__main__":
    main()
