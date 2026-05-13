"""Febrl3 ground-truth loader + evaluation.

Promoted from `.profile_tmp/baseline_febrl3_ncvr.py` (gitignored). The
0.9443 F1 number in `packages/python/goldenmatch/CHANGELOG.md` v1.8.0
was measured by this exact GT-mapping logic.

Key invariant: emitted pairs are positional row indices in the polars
DataFrame, while `recordlinkage.datasets.load_febrl3(return_links=True)`
returns rec_id pairs (`rec-XXX-org`, `rec-XXX-dup-N`). We translate
positional → rec_id before set-comparing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import polars as pl


@dataclass
class Febrl3Result:
    found_pairs: int
    ground_truth_pairs: int
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float
    recall: float
    f1: float


def load_febrl3_df_and_gt() -> tuple[pl.DataFrame, set[tuple[str, str]]] | None:
    """Load the Febrl3 dataset and its ground-truth pair set.

    Returns `None` when `recordlinkage` isn't installed so callers can
    skip cleanly without a hard import.
    """
    try:
        from recordlinkage.datasets import load_febrl3
    except ImportError:
        return None

    df_pd, links = load_febrl3(return_links=True)
    df_pd = df_pd.reset_index().rename(columns={"rec_id": "id"})
    df = pl.from_pandas(df_pd)

    gt: set[tuple[str, str]] = set()
    for a, b in links:
        pa, pb = (a, b) if isinstance(a, str) else (str(a), str(b))
        gt.add((min(pa, pb), max(pa, pb)))
    return df, gt


def evaluate_febrl3(
    df: pl.DataFrame,
    gt_pairs: set[tuple[str, str]],
    dedupe_df: Callable,
) -> Febrl3Result:
    """Run dedupe_df on the loaded Febrl3 frame; score against rec_id GT."""
    result = dedupe_df(df)

    # Emitted pairs are positional indices into `df`; map back to rec_id.
    row_to_id = df["id"].to_list()
    found: set[tuple[str, str]] = set()
    if getattr(result, "clusters", None):
        for cluster in result.clusters.values():
            members = sorted(cluster.get("members", []))
            for i, a in enumerate(members):
                for b in members[i + 1:]:
                    if 0 <= a < len(row_to_id) and 0 <= b < len(row_to_id):
                        pa, pb = row_to_id[a], row_to_id[b]
                        found.add((min(pa, pb), max(pa, pb)))

    tp = len(found & gt_pairs)
    fp = len(found - gt_pairs)
    fn = len(gt_pairs - found)
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * p * r / (p + r)) if (p + r) else 0.0
    return Febrl3Result(
        found_pairs=len(found),
        ground_truth_pairs=len(gt_pairs),
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        precision=p,
        recall=r,
        f1=f1,
    )
