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
    dedupe_df: Callable,
) -> LeipzigResult | None:
    """Run zero-config dedupe across DBLP+ACM, evaluate against perfectMapping.

    `dedupe_df` is injected (not imported here) so this module stays
    free of goldenmatch import cost when only the helpers are used.
    """
    dblp_path = datasets_dir / "DBLP-ACM" / "DBLP2.csv"
    acm_path = datasets_dir / "DBLP-ACM" / "ACM.csv"
    gt_path = datasets_dir / "DBLP-ACM" / "DBLP-ACM_perfectMapping.csv"
    if not (dblp_path.exists() and acm_path.exists() and gt_path.exists()):
        return None

    # latin-1/utf8-lossy is required (per goldenmatch CLAUDE.md gotcha).
    df_a = pl.read_csv(dblp_path, encoding="utf8-lossy", ignore_errors=True)
    df_b = pl.read_csv(acm_path, encoding="utf8-lossy", ignore_errors=True)

    # Tag source, cast id to string (DBLP ids are non-numeric).
    df_a = df_a.with_columns(
        pl.lit("DBLP").alias("__source__"),
        pl.col("id").cast(pl.Utf8),
    )
    df_b = df_b.with_columns(
        pl.lit("ACM").alias("__source__"),
        pl.col("id").cast(pl.Utf8),
    )

    common = sorted(set(df_a.columns) & set(df_b.columns))
    combined = pl.concat(
        [df_a.select(common), df_b.select(common)], how="vertical_relaxed"
    )
    # Row index is the positional id used inside the dedupe pipeline.
    combined = combined.with_row_index("__row_id__").with_columns(
        pl.col("__row_id__").cast(pl.Int64)
    )

    # Build positional → (source, source_id) lookups before dedupe.
    row_to_source: dict[int, str] = {}
    row_to_id: dict[int, str] = {}
    for row in combined.select("__row_id__", "__source__", "id").to_dicts():
        row_to_source[row["__row_id__"]] = row["__source__"]
        row_to_id[row["__row_id__"]] = str(row["id"]).strip()

    # Drop helper columns before passing to dedupe_df — the auto-config
    # controller will re-stamp __row_id__ internally based on row order,
    # which matches what we just captured.
    pipeline_input = combined.drop("__source__")
    result = dedupe_df(pipeline_input)

    emitted: set[tuple[int, int]] = set()
    if getattr(result, "clusters", None):
        for cluster in result.clusters.values():
            members = sorted(cluster.get("members", []))
            for i, a in enumerate(members):
                for b in members[i + 1:]:
                    emitted.add((a, b))

    gt = load_ground_truth(gt_path, "idDBLP", "idACM")
    return evaluate_emitted_pairs(
        emitted_row_pairs=emitted,
        row_to_source=row_to_source,
        row_to_id=row_to_id,
        ground_truth=gt,
        source_a_label="DBLP",
    )
