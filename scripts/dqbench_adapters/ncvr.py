"""NCVR ground-truth generator + evaluation.

NCVR (NC Voter) records are unique by `ncid` and the raw file does not
ship a duplicate-pair ground truth. Following the established
`tests/test_autoconfig_benchmarks.py::test_autoconfig_ncvr_meets_target`
pattern (which produced the 0.9719 F1 cited in the v1.8 CHANGELOG), we
build GT synthetically by:

  1. Sampling N base records.
  2. Corrupting M of them (typo/swap/drop/abbreviate/case) and giving
     them new `ncid` values of `<orig_ncid>_DUP`.
  3. Concatenating originals + duplicates into one frame.
  4. Recording the (orig_ncid, dup_ncid) pairs as ground truth.

Seed pinned at 42 for run-to-run determinism. The 10K-row sample lives
at `packages/python/goldenmatch/tests/benchmarks/datasets/NCVR/
ncvoter_sample_10k.txt` (gitignored).
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import polars as pl


@dataclass
class NCVRResult:
    found_pairs: int
    ground_truth_pairs: int
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float
    recall: float
    f1: float


_KEEP_COLS = [
    "ncid", "first_name", "last_name", "middle_name",
    "res_street_address", "res_city_desc", "state_cd",
    "zip_code", "birth_year", "gender_code",
]
_CORRUPT_FIELDS = [
    "first_name", "last_name", "middle_name",
    "res_street_address", "zip_code",
]


def _corrupt(val: str | None, rng: random.Random) -> str | None:
    if val is None or len(val) < 2:
        return val
    op = rng.choice(["typo", "swap", "drop", "abbreviate", "case"])
    if op == "typo":
        pos = rng.randint(0, len(val) - 1)
        repl = rng.choice("abcdefghijklmnopqrstuvwxyz")
        return val[:pos] + repl + val[pos + 1:]
    if op == "swap" and len(val) >= 3:
        pos = rng.randint(0, len(val) - 2)
        return val[:pos] + val[pos + 1] + val[pos] + val[pos + 2:]
    if op == "drop" and len(val) >= 3:
        pos = rng.randint(0, len(val) - 1)
        return val[:pos] + val[pos + 1:]
    if op == "abbreviate" and len(val) >= 3:
        return val[0] + "."
    if op == "case":
        return val.lower() if rng.random() < 0.5 else val.upper()
    return val


def build_ncvr_df_and_gt(
    ncvr_path: Path,
    seed: int = 42,
    n_base_cap: int = 5000,
) -> tuple[pl.DataFrame, set[tuple[str, str]]] | None:
    """Sample base + synthetic duplicates from the NCVR 10K file.

    Returns (combined_df, gt_pair_set) or None if the file is missing.
    """
    if not ncvr_path.exists():
        return None

    df = pl.read_csv(
        ncvr_path, separator="\t", encoding="utf8-lossy", ignore_errors=True,
    )
    df = df.filter(
        (pl.col("last_name").str.len_chars() > 1)
        & (pl.col("first_name").str.len_chars() > 1)
    )
    n_base = min(n_base_cap, df.height)
    n_dupes = n_base // 2

    df = df.sample(n=n_base, seed=seed)
    keep = [c for c in _KEEP_COLS if c in df.columns]
    df = df.select(keep)

    rng = random.Random(seed)
    rows = df.to_dicts()
    dup_indices = rng.sample(range(len(rows)), min(n_dupes, len(rows)))

    corrupted: list[dict] = []
    gt: set[tuple[str, str]] = set()
    for orig_idx in dup_indices:
        original = rows[orig_idx]
        corrupt = dict(original)
        corrupt["ncid"] = original["ncid"] + "_DUP"
        for field in _CORRUPT_FIELDS:
            if field in corrupt and rng.random() < 0.30:
                corrupt[field] = _corrupt(corrupt.get(field), rng)
        corrupted.append(corrupt)
        a, b = original["ncid"], corrupt["ncid"]
        gt.add((min(a, b), max(a, b)))

    combined = pl.DataFrame(rows + corrupted)
    return combined, gt


def evaluate_ncvr(
    df: pl.DataFrame,
    gt_pairs: set[tuple[str, str]],
    dedupe_df: Callable,
) -> NCVRResult:
    """Run dedupe_df, translate cluster members back to ncid, score F1."""
    result = dedupe_df(df)
    ncid_lookup = df["ncid"].to_list()
    found: set[tuple[str, str]] = set()
    if getattr(result, "clusters", None):
        for cluster in result.clusters.values():
            members = sorted(cluster.get("members", []))
            for i, ai in enumerate(members):
                for bi in members[i + 1:]:
                    if 0 <= ai < len(ncid_lookup) and 0 <= bi < len(ncid_lookup):
                        a, b = ncid_lookup[ai], ncid_lookup[bi]
                        found.add((min(a, b), max(a, b)))

    tp = len(found & gt_pairs)
    fp = len(found - gt_pairs)
    fn = len(gt_pairs - found)
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * p * r / (p + r)) if (p + r) else 0.0
    return NCVRResult(
        found_pairs=len(found),
        ground_truth_pairs=len(gt_pairs),
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        precision=p,
        recall=r,
        f1=f1,
    )
