"""Dataset registry: synthetic anchors (always available) + real labeled
datasets (skip-when-absent).

Each Dataset's loader returns (df, gt_pairs) | None, where gt_pairs is a
set[(i, j)] in ROW-INDEX space (i<j).  A real loader returns None when its
data isn't present locally; the harness records it as ``skipped``, never
crashes.

Mirrors ``scripts.autoconfig_quality.datasets`` exactly — same Dataset
dataclass, same REGISTRY list structure, same loader signatures — so
someone who knows autoconfig_quality recognises this immediately.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import polars as pl

# Re-use the shared anchor generators from autoconfig_quality rather than
# re-implementing them.  The anchors are the same labeled ER shapes.
from scripts.autoconfig_quality.anchors import crm_df, gen_labeled, make_healthcare_df


@dataclass(frozen=True)
class Dataset:
    name: str
    kind: Literal["anchor", "real"]
    loader: Callable[[], tuple[pl.DataFrame, set] | None]
    full_scan: bool = False  # True -> oracle ignores --row-cap, runs the whole df


def effective_row_cap(dataset: Dataset, cli_row_cap: int | None) -> int | None:
    """A full_scan dataset ignores the CLI cap (None = no truncation)."""
    return None if dataset.full_scan else cli_row_cap


# ── helpers ───────────────────────────────────────────────────────────────────

def _pairs_to_row_index(
    df: pl.DataFrame, id_col: str, str_pairs: set[tuple[str, str]]
) -> set[tuple[int, int]]:
    """Map id-string pairs to canonical (min, max) row-index pairs.

    Drops pairs whose endpoints are missing from the frame or identical.
    Imported by tests directly.
    """
    pos = {str(v): i for i, v in enumerate(df[id_col].to_list())}
    out: set[tuple[int, int]] = set()
    for a, b in str_pairs:
        ia, ib = pos.get(str(a)), pos.get(str(b))
        if ia is not None and ib is not None and ia != ib:
            out.add((min(ia, ib), max(ia, ib)))
    return out


# ── anchors (always available, deterministic) ──────────────────────────────────

def _sparse_zip() -> tuple[pl.DataFrame, set]:
    # n=30k is the scale where the zip5 sampling-artifact + blocking-coupling
    # regression manifests (zips saturate to ~0.32 cardinality). No true dups
    # -> blocking-shape anchor, F1 not applicable (gt is empty).
    df = make_healthcare_df(30_000, seed=715, zip_present=0.5).drop("matching_id")
    return df, set()


def _shared_email() -> tuple[pl.DataFrame, set]:
    return crm_df(), set()  # config-shape anchor (demote-phone / keep-shared-email)


def _person() -> tuple[pl.DataFrame, set]:
    return gen_labeled(n_entities=400, seed=7)  # has row-index ground truth


# ── synthetic (always available) ──────────────────────────────────────────────

def _synthetic() -> tuple[pl.DataFrame, set]:
    """Small synthetic person dataset (PII-free, committable, runs in CI).

    Uses the same gen_labeled generator as the anchor but with a different
    seed and entity count so it's a distinct dataset in the registry.
    """
    return gen_labeled(n_entities=200, seed=42)


# ── real labeled datasets (skip-when-absent) ────────────────────────────────

_DATASETS_ROOT = (
    Path(__file__).resolve().parents[2]
    / "packages/python/goldenmatch/tests/benchmarks/datasets"
)


def _febrl3() -> tuple[pl.DataFrame, set] | None:
    """FEBRL3 (recordlinkage-bundled). rec_id-pair truth -> row-index via df['id'].
    Returns None when recordlinkage isn't installed (skip-when-absent)."""
    from scripts.dqbench_adapters.febrl3 import load_febrl3_df_and_gt
    loaded = load_febrl3_df_and_gt()
    if loaded is None:
        return None
    df, rec_pairs = loaded
    return df, _pairs_to_row_index(df, "id", rec_pairs)


_NCVR_REAL_PATH = _DATASETS_ROOT / "NCVR" / "ncvoter_sample_10k.txt"


def _ncvr_synthetic() -> tuple[pl.DataFrame, set]:
    """PII-free NCVR-shaped corpus (seed 42, committable, runs in CI). Its F1 is its
    OWN baseline, never the real-data number."""
    from scripts.dqbench_adapters.ncvr import build_ncvr_synthetic_df_and_gt
    df, ncid_pairs = build_ncvr_synthetic_df_and_gt(seed=42)
    return df, _pairs_to_row_index(df, "ncid", ncid_pairs)


def _ncvr_real() -> tuple[pl.DataFrame, set] | None:
    """Real NCVR sample (gitignored PII, local-only). None when the file is absent."""
    from scripts.dqbench_adapters.ncvr import build_ncvr_df_and_gt
    loaded = build_ncvr_df_and_gt(_NCVR_REAL_PATH, seed=42)
    if loaded is None:
        return None
    df, ncid_pairs = loaded
    return df, _pairs_to_row_index(df, "ncid", ncid_pairs)


_VENDORED = Path(__file__).resolve().parent / "vendored"


def _historical_50k() -> tuple[pl.DataFrame, set] | None:
    """Splink historical_50k from the committed parquet (vendored from
    autoconfig_quality's vendored/ dir). Returns None when the parquet is absent."""
    # Prefer a local copy in suggest_quality/vendored/; fall back to the one
    # vendored by autoconfig_quality so we don't duplicate the 10 MB file.
    _aq_vendored = (
        Path(__file__).resolve().parents[1]
        / "autoconfig_quality" / "vendored" / "historical_50k.parquet"
    )
    p = _VENDORED / "historical_50k.parquet"
    if not p.exists():
        p = _aq_vendored
    if not p.exists():
        return None
    df = pl.read_parquet(p)
    clusters = df["cluster"].to_list()
    by_cluster: dict[object, list[int]] = {}
    for row, cid in enumerate(clusters):
        by_cluster.setdefault(cid, []).append(row)
    gt: set[tuple[int, int]] = set()
    for members in by_cluster.values():
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                gt.add((members[i], members[j]))
    match_df = df.drop([c for c in ("cluster", "unique_id") if c in df.columns])
    return match_df, gt


def _dblp_acm() -> tuple[pl.DataFrame, set] | None:
    """DBLP-ACM bibliographic record-linkage. Concatenate the two tables, build
    row-index GT from the perfect mapping. Returns None when the data is absent."""
    d = _DATASETS_ROOT / "DBLP-ACM"
    dblp_p, acm_p = d / "DBLP2.csv", d / "ACM.csv"
    map_p = d / "DBLP-ACM_perfectMapping.csv"
    if not (dblp_p.exists() and acm_p.exists() and map_p.exists()):
        return None
    try:
        dblp = pl.read_csv(dblp_p, encoding="utf8-lossy", ignore_errors=True)
        acm = pl.read_csv(acm_p, encoding="utf8-lossy", ignore_errors=True)
        mapping = pl.read_csv(map_p, encoding="utf8-lossy", ignore_errors=True)
        df = pl.concat([dblp, acm], how="diagonal_relaxed")
        cols = mapping.columns  # standard headers: idDBLP, idACM
        a_col, b_col = cols[0], cols[1]
        str_pairs = {
            (str(a), str(b))
            for a, b in zip(mapping[a_col].to_list(), mapping[b_col].to_list())
        }
        return df, _pairs_to_row_index(df, "id", str_pairs)
    except Exception:
        return None  # any malformed piece -> skip, never crash the run


REGISTRY: list[Dataset] = [
    # Anchors — always load, deterministic, fast.
    Dataset("anchor_sparse_zip",   "anchor", _sparse_zip),
    Dataset("anchor_shared_email", "anchor", _shared_email),
    Dataset("anchor_person_match", "anchor", _person),
    # Synthetic — always load, committable, CI-safe.
    Dataset("synthetic",           "real",   _synthetic),
    # Real labeled datasets — skip-when-absent.
    Dataset("dblp_acm",            "real",   _dblp_acm),
    Dataset("febrl3",              "real",   _febrl3),
    Dataset("ncvr_synthetic",      "real",   _ncvr_synthetic),
    Dataset("ncvr_real",           "real",   _ncvr_real),
    Dataset("historical_50k",      "real",   _historical_50k, full_scan=True),
    # Future: add new labeled datasets with the same skip-when-absent pattern.
]
