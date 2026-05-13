"""Cluster->pair / match->pair converters for benchmark scoring.

Spec: docs/superpowers/specs/2026-05-06-autoconfig-introspective-controller-design.md
      §Testing tier 4 -- pair-conversion rules.
"""
from __future__ import annotations

from itertools import combinations

import polars as pl
from goldenmatch import DedupeResult, MatchResult


def pairs_from_dedupe_result(
    result: DedupeResult,
    *,
    id_column: str,
    source_df: pl.DataFrame | None = None,
) -> set[tuple]:
    """Transitive closure of in-cluster edges. Singleton clusters yield no pairs.

    If ``source_df`` is provided, members (which are __row_id__ ints) map back
    to ``source_df[id_column]`` values; otherwise members are used as-is.
    Pairs canonicalized as (min, max).
    """
    pairs: set[tuple] = set()
    id_lookup = source_df[id_column].to_list() if source_df is not None else None
    for cluster in result.clusters.values():
        members = cluster["members"]
        if len(members) < 2:
            continue
        if id_lookup is not None:
            members = [id_lookup[m] if 0 <= m < len(id_lookup) else m for m in members]
        for a, b in combinations(sorted(members, key=lambda x: str(x)), 2):
            pairs.add((a, b) if str(a) <= str(b) else (b, a))
    return pairs


def pairs_from_match_result(
    result: MatchResult,
    *,
    target_id_col: str,
    ref_id_col: str,
) -> set[tuple]:
    """Direct extraction from MatchResult.matched. No closure needed --
    each row is one (target_id, ref_id) pair. Pairs NOT canonicalized
    (target/ref are semantically distinct, e.g. DBLP vs ACM)."""
    if result.matched is None or result.matched.height == 0:
        return set()
    return {
        (row[target_id_col], row[ref_id_col])
        for row in result.matched.iter_rows(named=True)
    }
