"""Dataset registry: synthetic failure-shape anchors (always available) + real
labeled datasets (skip-when-absent).

Each Dataset's loader returns (df, gt_pairs) | None, where gt_pairs is a
set[(i, j)] in ROW-INDEX space (i<j) — the same space cluster members live in,
so the F1 tier needs no remap. A real loader returns None when its data isn't
present locally (the harness records it as `skipped`, never crashes).

The committed baseline scorecard is the single source of pinned expectations for
anchors (the gate compares current signals against the baseline), so Dataset
carries no separate `expected` block.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import polars as pl

from scripts.autoconfig_quality.anchors import crm_df, gen_labeled, make_healthcare_df


@dataclass(frozen=True)
class Dataset:
    name: str
    kind: Literal["anchor", "real"]
    loader: Callable[[], tuple[pl.DataFrame, set] | None]


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


# ── real labeled datasets (skip-when-absent) ───────────────────────────────────
_DATASETS_ROOT = Path(__file__).resolve().parents[2] / "packages/python/goldenmatch/tests/benchmarks/datasets"


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
        # row-index lookup keyed by the source 'id' column (present in both tables)
        pos = {str(v): i for i, v in enumerate(df["id"].to_list())}
        gt: set[tuple[int, int]] = set()
        cols = mapping.columns  # standard headers: idDBLP, idACM
        a_col, b_col = cols[0], cols[1]
        for a, b in zip(mapping[a_col].to_list(), mapping[b_col].to_list()):
            ia, ib = pos.get(str(a)), pos.get(str(b))
            if ia is not None and ib is not None and ia != ib:
                gt.add((min(ia, ib), max(ia, ib)))
        return df, gt
    except Exception:
        return None  # any malformed piece -> skip, never crash the run


REGISTRY: list[Dataset] = [
    Dataset("anchor_sparse_zip", "anchor", _sparse_zip),
    Dataset("anchor_shared_email", "anchor", _shared_email),
    Dataset("anchor_person_match", "anchor", _person),
    Dataset("dblp_acm", "real", _dblp_acm),
    # FEBRL3 / NCVR / historical_50k / DQbench tiers: add with the same
    # skip-when-absent loader pattern as their data lands.
]
