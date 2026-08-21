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

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import polars as pl


def _as_polars(frame):
    """Normalise a result frame to polars, whichever lane produced it.

    `match_df` returns a `pyarrow.Table` on the arrow lane and a
    `pl.DataFrame` on the classic one. This module reads `.height` and
    `.iter_rows(named=True)`, which are polars-only, so an arrow result
    raised `AttributeError: 'pyarrow.lib.Table' object has no attribute
    'height'` and took the whole scheduled `benchmarks` lane red (#2457).

    Converted here rather than by importing `goldenmatch.core.frame`: this
    module deliberately takes `match_df` / `dedupe_df` by injection so it
    stays free of goldenmatch import cost, and reaching into the package for
    a two-line coercion would undo that.
    """
    if frame is None:
        return None
    if hasattr(frame, "height"):  # already polars
        return frame
    if hasattr(frame, "num_rows"):  # pyarrow.Table
        return pl.from_arrow(frame)
    return frame


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
    matched = _as_polars(getattr(result, "matched", None))
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


# --------------------------------------------------------------------------- #
# Two-source PRODUCT matching (Abt-Buy, Amazon-Google) via zero-config dedupe.
#
# Unlike DBLP-ACM (identical schemas, run through match_df), product sources have
# heterogeneous schemas and the perf path is a UNIFIED dedupe: union both sources
# into one frame with source-prefixed record_ids + a cluster_id ground truth
# (connected components of the perfect mapping), run `dedupe_df`, and score the
# recovered within-cluster pairs. Mirrors bench_er_headtohead's unified loader +
# evaluate.py pairwise scoring, in one committed helper the runner can call.
# --------------------------------------------------------------------------- #
def _connected_components(all_ids: list[str], pairs: set[tuple[str, str]]) -> dict[str, str]:
    parent = {r: r for r in all_ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pairs:
        if a in parent and b in parent:
            parent[find(a)] = find(b)
    return {r: find(r) for r in all_ids}


def _within_cluster_pairs(assign: dict[str, str]) -> set[tuple[str, str]]:
    from collections import defaultdict
    groups: dict[str, list[str]] = defaultdict(list)
    for rid, cid in assign.items():
        groups[cid].append(rid)
    pairs: set[tuple[str, str]] = set()
    for members in groups.values():
        if len(members) < 2:
            continue
        members = sorted(members)
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                pairs.add((members[i], members[j]))
    return pairs


def run_two_source_dedupe_zeroconfig(
    datasets_dir: Path,
    dedupe_df: Callable,
    *,
    subdir: str,
    file_a: str,
    file_b: str,
    gt_file: str,
    gt_cols: tuple[str, str],
    src_a: str,
    src_b: str,
    rename: dict[str, str] | None = None,
) -> LeipzigResult | None:
    """Zero-config dedupe of two product sources; pairwise-F1 vs the mapping.

    Returns ``None`` if the vendor CSVs are absent. ``rename`` harmonises a
    differently-named field across the two sources (e.g. Google ``name`` ->
    ``title``) so both share a comparable text column.
    """
    base = datasets_dir / subdir
    a_path, b_path, gt_path = base / file_a, base / file_b, base / gt_file
    if not (a_path.exists() and b_path.exists() and gt_path.exists()):
        return None

    a = pl.read_csv(a_path, encoding="utf8-lossy", ignore_errors=True)
    b = pl.read_csv(b_path, encoding="utf8-lossy", ignore_errors=True)
    if rename:
        a = a.rename({k: v for k, v in rename.items() if k in a.columns})
        b = b.rename({k: v for k, v in rename.items() if k in b.columns})

    shared = [c for c in a.columns if c in b.columns and c != "id"]

    def _prep(df: pl.DataFrame, src: str) -> pl.DataFrame:
        return (
            df.select(["id"] + shared)
            .with_columns((pl.lit(f"{src}:") + pl.col("id").cast(pl.Utf8)).alias("record_id"))
            .drop("id")
        )

    records = pl.concat([_prep(a, src_a), _prep(b, src_b)])
    all_ids = records["record_id"].to_list()

    gt = pl.read_csv(gt_path, encoding="utf8-lossy", ignore_errors=True)
    ca, cb = gt_cols
    gt_pairs = {
        (f"{src_a}:{str(row[ca]).strip()}", f"{src_b}:{str(row[cb]).strip()}")
        for row in gt.to_dicts()
    }
    truth = _within_cluster_pairs(_connected_components(all_ids, gt_pairs))

    ded = dedupe_df(records)
    pred_assign: dict[str, str] = {}
    for cid, cluster in (getattr(ded, "clusters", None) or {}).items():
        members = cluster["members"] if isinstance(cluster, dict) else cluster.members
        for row_id in members:
            pred_assign[all_ids[row_id]] = str(cid)
    # Records never emitted in a cluster are their own singleton (no pairs).
    predicted = _within_cluster_pairs(pred_assign)

    tp = len(predicted & truth)
    fp = len(predicted - truth)
    fn = len(truth - predicted)
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * p * r / (p + r)) if (p + r) else 0.0
    return LeipzigResult(
        found_pairs=len(predicted),
        ground_truth_pairs=len(truth),
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        precision=p,
        recall=r,
        f1=f1,
    )
