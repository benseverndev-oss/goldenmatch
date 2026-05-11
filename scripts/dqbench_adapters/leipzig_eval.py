"""Shared evaluation helpers for Leipzig benchmark datasets (DBLP-ACM).

Factors the ID-joined pair-evaluation logic out of the package's
`tests/benchmarks/run_leipzig.py` so `scripts/run_benchmarks.py` can
reuse it without depending on test fixtures or adding the
`packages/python/goldenmatch` path to `sys.path`.

The key correctness invariant: emitted pairs are positional row indices
in the concatenated frame, but the ground-truth CSV maps source IDs
(`idDBLP`, `idACM`). The runner script's previous int-cast positional
join silently dropped every DBLP ID (those are strings like
`conf/vldb/...`) and reported F1=0. This helper does the ID join.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import polars as pl


@dataclass
class LeipzigResult:
    found_pairs: int
    ground_truth_pairs: int
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float
    recall: float
    f1: float


def load_ground_truth(
    mapping_path: Path, id_col_a: str, id_col_b: str
) -> set[tuple[str, str]]:
    """Load the perfectMapping CSV into a set of (id_a, id_b) string pairs."""
    df = pl.read_csv(mapping_path, encoding="utf8-lossy")
    pairs: set[tuple[str, str]] = set()
    for row in df.to_dicts():
        a = str(row[id_col_a]).strip()
        b = str(row[id_col_b]).strip()
        pairs.add((a, b))
    return pairs


def evaluate_emitted_pairs(
    emitted_row_pairs: set[tuple[int, int]],
    row_to_source: dict[int, str],
    row_to_id: dict[int, str],
    ground_truth: set[tuple[str, str]],
    source_a_label: str,
) -> LeipzigResult:
    """Map emitted row-id pairs back to source IDs and compute F1.

    `source_a_label` identifies which side of the cross-source pair
    becomes the first element of the canonical `(id_a, id_b)` tuple
    used in the ground-truth mapping (e.g. `source_a` or `DBLP`).
    """
    found: set[tuple[str, str]] = set()
    for a, b in emitted_row_pairs:
        src_a = row_to_source.get(a)
        src_b = row_to_source.get(b)
        if src_a is None or src_b is None or src_a == src_b:
            continue
        id_a = row_to_id.get(a)
        id_b = row_to_id.get(b)
        if id_a is None or id_b is None:
            continue
        if src_a == source_a_label:
            found.add((id_a, id_b))
        else:
            found.add((id_b, id_a))

    tp = len(found & ground_truth)
    fp = len(found - ground_truth)
    fn = len(ground_truth - found)
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * p * r / (p + r)) if (p + r) else 0.0
    return LeipzigResult(
        found_pairs=len(found),
        ground_truth_pairs=len(ground_truth),
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        precision=p,
        recall=r,
        f1=f1,
    )


def run_dblp_acm_zeroconfig(
    datasets_dir: Path,
    match_df: Callable,
) -> LeipzigResult | None:
    """Run zero-config cross-source matching on DBLP vs ACM and score F1.

    The 0.9641 F1 in the v1.8 CHANGELOG was measured by passing the
    DBLP and ACM frames separately into `goldenmatch.match_df` (NOT
    concatenated through `dedupe_df`). The reference harness is
    `.profile_tmp/measure_dblp_acm_controller.py`. We mirror its
    row-id → source-id mapping logic here.

    `match_df` is injected (not imported) so this module stays free of
    goldenmatch import cost when only the helpers are used.
    """
    dblp_path = datasets_dir / "DBLP-ACM" / "DBLP2.csv"
    acm_path = datasets_dir / "DBLP-ACM" / "ACM.csv"
    gt_path = datasets_dir / "DBLP-ACM" / "DBLP-ACM_perfectMapping.csv"
    if not (dblp_path.exists() and acm_path.exists() and gt_path.exists()):
        return None

    # utf8-lossy required for Leipzig CSVs (per goldenmatch CLAUDE.md gotcha).
    dblp = pl.read_csv(dblp_path, encoding="utf8-lossy", ignore_errors=True)
    acm = pl.read_csv(acm_path, encoding="utf8-lossy", ignore_errors=True)

    result = match_df(dblp, acm)

    dblp_ids = dblp["id"].cast(pl.Utf8).to_list()
    acm_ids = acm["id"].cast(pl.Utf8).to_list()
    n_dblp = len(dblp_ids)

    found: set[tuple[str, str]] = set()
    matched = getattr(result, "matched", None)
    if matched is not None and matched.height > 0:
        # match_df stamps target_row_id (from the first arg) and
        # ref_row_id (from the second arg). They're positional indices
        # in the SOURCE frames passed in — NOT the concatenated frame.
        for row in matched.iter_rows(named=True):
            tgt_rid = row["__target_row_id__"]
            ref_rid = row["__ref_row_id__"]
            if tgt_rid < n_dblp:
                d_idx, a_idx = tgt_rid, ref_rid - n_dblp
            else:
                d_idx, a_idx = ref_rid, tgt_rid - n_dblp
            if 0 <= d_idx < n_dblp and 0 <= a_idx < len(acm_ids):
                found.add((str(dblp_ids[d_idx]), str(acm_ids[a_idx])))

    gt = load_ground_truth(gt_path, "idDBLP", "idACM")
    tp = len(found & gt)
    fp = len(found - gt)
    fn = len(gt - found)
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * p * r / (p + r)) if (p + r) else 0.0
    return LeipzigResult(
        found_pairs=len(found),
        ground_truth_pairs=len(gt),
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        precision=p,
        recall=r,
        f1=f1,
    )
