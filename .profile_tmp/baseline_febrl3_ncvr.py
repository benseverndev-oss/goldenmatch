"""Measure v1.7.1 zero-config F1 on Febrl3 + NCVR.

Used to set v1 acceptance targets for the auto-config controller. Run once
before implementation begins; results inform §Testing tier 4 of the spec.
"""
from __future__ import annotations
import sys, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from pathlib import Path
import polars as pl
import goldenmatch as gm


def evaluate(name: str, found: set, gt: set) -> None:
    tp = len(found & gt)
    fp = len(found - gt)
    fn = len(gt - found)
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    print(f"{name}: P={p:.4f} R={r:.4f} F1={f1:.4f}  (TP={tp} FP={fp} FN={fn})")


def expand_clusters_to_pairs(clusters: dict) -> set:
    """Transitive closure of in-cluster edges. Singletons yield no pairs."""
    pairs = set()
    for c in clusters.values():
        members = sorted(c["members"])
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                pairs.add((members[i], members[j]))
    return pairs


def main() -> None:
    print(f"goldenmatch v{gm.__version__}")
    print(f"polars v{pl.__version__}")

    # ---- Febrl3 ----
    print("\n=== Febrl3 ===")
    try:
        from recordlinkage.datasets import load_febrl3
    except ImportError:
        print("recordlinkage not installed; skipping Febrl3")
    else:
        try:
            df_pd, gt_pairs = load_febrl3(return_links=True)
            df_pd = df_pd.reset_index().rename(columns={"rec_id": "id"})
            df = pl.from_pandas(df_pd)
            print(f"Febrl3: {df.height} rows, cols={df.columns}")
            t0 = time.time()
            result = gm.dedupe_df(df)
            print(f"  dedupe_df completed in {time.time() - t0:.2f}s")
            found = expand_clusters_to_pairs(result.clusters)
            # Map row_id -> original id; ground truth uses original ids
            row_to_id = df["id"].to_list()
            found_ids = set()
            for a, b in found:
                if 0 <= a < len(row_to_id) and 0 <= b < len(row_to_id):
                    pa, pb = row_to_id[a], row_to_id[b]
                    found_ids.add((min(pa, pb), max(pa, pb)))
            gt_set = set()
            for a, b in gt_pairs:
                pa, pb = (a, b) if isinstance(a, str) else (str(a), str(b))
                gt_set.add((min(pa, pb), max(pa, pb)))
            evaluate("Febrl3 (zero-config dedupe)", found_ids, gt_set)
        except Exception as e:
            print(f"Febrl3 failed: {type(e).__name__}: {e}")

    # ---- NCVR ----
    print("\n=== NCVR sample ===")
    ncvr_path = Path("packages/python/goldenmatch/tests/benchmarks/datasets/NCVR/ncvoter_sample_10k.txt")
    if not ncvr_path.exists():
        print(f"NCVR sample not at {ncvr_path}; skipping")
    else:
        try:
            df = pl.read_csv(ncvr_path, separator="\t", encoding="utf8-lossy", ignore_errors=True)
            print(f"NCVR: {df.height} rows, {len(df.columns)} cols")
            print("  → ground-truth pair source: TBD (no canonical loader in repo)")
            print("  → skipping F1 computation; record-only baseline")
            # Run dedupe just to confirm zero-config doesn't crash on this shape
            t0 = time.time()
            try:
                result = gm.dedupe_df(df)
                print(f"  dedupe_df completed in {time.time() - t0:.2f}s, "
                      f"{len(result.clusters)} clusters, "
                      f"{sum(1 for c in result.clusters.values() if c['size'] >= 2)} multi-member")
            except Exception as e:
                print(f"  dedupe_df failed: {type(e).__name__}: {e}")
        except Exception as e:
            print(f"NCVR load failed: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
