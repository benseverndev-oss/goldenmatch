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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

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
    return _sample_and_corrupt(df, seed=seed, n_base_cap=n_base_cap)


# --- Synthetic NCVR-shaped generator (PII-free, committable) ----------------
# The real NCVR sample carries real voters' names + home addresses (PII), so it
# stays gitignored. This generator emits the SAME schema + value shapes from
# syllable wordlists — every person is fabricated, zero PII — so the NCVR lane
# can run in CI from a fresh clone with no secrets. Its F1 is its OWN committed
# baseline, NOT the real-data 0.9719.
_SYL = [
    "an", "ber", "cha", "dle", "el", "fer", "gan", "hol", "ix", "jon", "kel",
    "lor", "mor", "nor", "ol", "per", "quin", "ros", "sten", "tan", "ven",
    "wes", "yor", "zel", "bre", "cle", "dar", "fen", "gil", "han",
]
_STREETS = [
    "Oak St", "Main St", "Elm Ave", "Pine Rd", "Maple Dr", "Cedar Ln",
    "Birch Way", "Walnut St", "Ash Ct", "Holly Blvd", "Dogwood Dr", "Magnolia Ave",
]
_CITIES = [
    "RALEIGH", "DURHAM", "CHARLOTTE", "GREENSBORO", "ASHEVILLE", "CARY",
    "WILMINGTON", "CONCORD", "GASTONIA", "APEX", "HICKORY", "SALISBURY",
]


def _syl_name(rng: random.Random, n_syl: int = 2) -> str:
    return "".join(rng.choice(_SYL) for _ in range(n_syl)).capitalize()


def generate_synthetic_ncvr(n: int = 10_000, seed: int = 7) -> pl.DataFrame:
    """A PII-free, NCVR-SHAPED synthetic voter table (deterministic, committable).

    Same columns + value shapes as the real NC voter file (``_KEEP_COLS``), but
    fabricated from syllable wordlists so there is no real PII. Names are drawn
    from a large combinatorial space so coincidental collisions are rare (a
    realistic ER shape, unlike a tiny name pool).
    """
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        rows.append({
            "ncid": f"SY{i:08d}",
            "first_name": _syl_name(rng, 2),
            "last_name": _syl_name(rng, rng.choice([2, 2, 3])),
            "middle_name": _syl_name(rng, 1) if rng.random() < 0.6 else "",
            "res_street_address": f"{rng.randint(100, 9999)} {rng.choice(_STREETS)}",
            "res_city_desc": rng.choice(_CITIES),
            "state_cd": "NC",
            "zip_code": f"2{rng.randint(7000, 8999)}",
            "birth_year": str(rng.randint(1940, 2004)),
            "gender_code": rng.choice(["M", "F"]),
        })
    return pl.DataFrame(rows)


def build_ncvr_synthetic_df_and_gt(
    n: int = 10_000, seed: int = 42, n_base_cap: int = 5000,
) -> tuple[pl.DataFrame, set[tuple[str, str]]]:
    """Synthetic NCVR-shaped (combined_df, gt) — same sample+corrupt pipeline as
    the real file, but PII-free and committable. Use when the real sample is
    absent. Label results 'NCVR-synthetic' so they're never confused with the
    real-data number."""
    df = generate_synthetic_ncvr(n=n, seed=seed)
    return _sample_and_corrupt(df, seed=seed, n_base_cap=n_base_cap)


def _sample_and_corrupt(
    df: pl.DataFrame, seed: int, n_base_cap: int,
) -> tuple[pl.DataFrame, set[tuple[str, str]]]:
    """Shared base-sample + corruption-dup GT construction (real & synthetic)."""
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
