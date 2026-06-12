"""Phase 0 — validate the recall certificate on REAL GoldenMatch pipeline output.

The research validated the FP-aware capture-recapture recall estimator on
SYNTHETIC field-group matchers. Phase 0 answers the only question that gates
productization: do GoldenMatch's REAL matchers (its production scorer / blocker /
clustering) produce capture histories decorrelated enough — and FPs singleton
enough — that the estimator tracks true recall? Uses the existing benchmark
datasets + ground truth (so we can check the estimate), but builds the K
"systems" from the REAL pipeline, not synthetic matchers:

    system_k = dedupe_df(df, fuzzy={field_k: tau})   # real GoldenMatch on field k

Each run uses GoldenMatch's actual scorer/blocking/clustering; different fields
miss different true pairs -> decorrelated captures. The capture history of a pair
= which of the K single-field GoldenMatch runs clustered it together. We then run
the FP-aware estimator and compare its label-free recall estimate to the TRUE
recall (union vs gold).

Scope: validates the POINT estimate on real output (the gating risk). The
audit-calibrated SAFE bound needs the sub-threshold candidate stratum, which the
high-level dedupe_df API does not expose — that needs the blocker provenance
plumbing (productization Phase 2), out of scope here.

Run (needs goldenmatch installed + recordlinkage for Febrl3):
    python scripts/research/phase0_goldenmatch_recall.py --dataset febrl3 --k 6
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
for p in (str(_SCRIPTS), str(_SCRIPTS / "research")):
    if p not in sys.path:
        sys.path.insert(0, p)

from recall_certificate import chao2, fp_robust_recall  # noqa: E402


def _clusters_to_id_pairs(result, ids) -> set:
    """DedupeResult.clusters (members = row indices) -> set of (id,id) pairs."""
    out = set()
    clusters = getattr(result, "clusters", None) or {}
    for cl in clusters.values():
        members = cl.get("members", []) if isinstance(cl, dict) else getattr(cl, "members", [])
        ms = sorted(m for m in members if 0 <= m < len(ids))
        for a in range(len(ms)):
            for b in range(a + 1, len(ms)):
                pa, pb = str(ids[ms[a]]), str(ids[ms[b]])
                out.add((min(pa, pb), max(pa, pb)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", choices=["febrl3"], default="febrl3")
    ap.add_argument("--k", type=int, default=6, help="# real single-field systems")
    ap.add_argument("--tau", type=float, default=0.8, help="per-field fuzzy threshold")
    ap.add_argument("--limit", type=int, default=0, help="subsample rows (0 = full)")
    args = ap.parse_args()

    try:
        import goldenmatch as gm
        from dqbench_adapters.febrl3 import load_febrl3_df_and_gt
    except Exception as e:
        print(f"  unavailable ({e}) — needs goldenmatch + recordlinkage.")
        return 0

    loaded = load_febrl3_df_and_gt()
    if loaded is None:
        print("  recordlinkage not installed — skipping.")
        return 0
    df, gt = loaded
    if args.limit and args.limit < df.height:
        df = df.head(args.limit)
        keep = set(df["id"].to_list())
        gt = {(a, b) for (a, b) in gt if a in keep and b in keep}
    ids = df["id"].to_list()

    # candidate fuzzy fields: string-ish columns, skip id
    fields = [c for c in df.columns if c != "id"][: args.k]
    print(f"\n  dataset={args.dataset}  N={df.height}  gt_pairs={len(gt)}")
    print(f"  building {len(fields)} REAL GoldenMatch single-field systems: {fields}\n")

    pred_sets = []
    used = []
    for f in fields:
        try:
            res = gm.dedupe_df(df, fuzzy={f: args.tau}, confidence_required=False)
        except Exception as e:
            print(f"    [{f}] skipped ({type(e).__name__})")
            continue
        pairs = _clusters_to_id_pairs(res, ids)
        if pairs:
            pred_sets.append(pairs)
            used.append(f)
            print(f"    [{f:<14}] matched {len(pairs):>6} pairs  "
                  f"recall={len(pairs & gt)/len(gt):.3f} prec={len(pairs & gt)/len(pairs):.3f}")
    K = len(pred_sets)
    if K < 3:
        print(f"\n  only {K} usable systems — need >=3 for capture-recapture. Stop.")
        return 0

    union = set().union(*pred_sets)
    counts = {p: 0 for p in union}
    for ps in pred_sets:
        for p in ps:
            counts[p] += 1

    # decorrelation diagnostic (on true pairs)
    import numpy as np
    ov = []
    for a in range(K):
        for b in range(a + 1, K):
            ta, tb = pred_sets[a] & gt, pred_sets[b] & gt
            if ta or tb:
                ov.append(len(ta & tb) / len(ta | tb))
    overlap = float(np.mean(ov)) if ov else 0.0

    N_chao = chao2(counts, K)
    fpr = fp_robust_recall(counts, K)
    D = len(union)
    tp = len(union & gt)
    true_recall = tp / len(gt) if gt else 0.0
    precision = tp / D if D else 0.0
    naive_recall = D / N_chao if N_chao > 0 else 0.0   # naive (FP-contaminated)

    print(f"\n  union: found(D)={D}  true-in-union(tp)={tp}  precision={precision:.3f}")
    print(f"  capture overlap (true pairs, decorrelation): {overlap:.2f}")
    print(f"  capture-count histogram: "
          + " ".join(f"{k}:{sum(1 for v in counts.values() if v==k)}" for k in range(1, K + 1)))
    print(f"\n  RECALL of the K-system union:")
    print(f"    naive Chao2 (FP-contaminated): {naive_recall:.3f}")
    if fpr['recall'] == fpr['recall']:
        print(f"    FP-aware (ignores singleton cell): {fpr['recall']:.3f}  (p={fpr['p']:.3f})")
    else:
        print(f"    FP-aware: n/a (need f2 and f3 cells)")
    print(f"    TRUE recall (gold):                {true_recall:.3f}")

    print("\n  PHASE-0 VERDICT:")
    if fpr['recall'] == fpr['recall']:
        err = abs(fpr['recall'] - true_recall)
        print(f"   - FP-aware |err| vs true = {err:.3f}")
        ok = err < 0.10
        print(f"   - estimator tracks true recall on REAL GoldenMatch output: "
              f"{'YES — productize' if ok else 'NO — real passes too correlated / FPs not singletons'}")
        print(f"   - decorrelation (overlap {overlap:.2f}): "
              f"{'OK' if overlap < 0.85 else 'HIGH (correlated passes — vary blocking/scorer more)'}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
