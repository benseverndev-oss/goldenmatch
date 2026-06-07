"""Exact matchkeys must not match on empty/blank values.

Two records both missing a field (e.g. a blanked phone -> "") are NOT a shared
identity claim. Without this, every blank-valued record joins on "" and
Union-Find transitively explodes the clusters -- the DQbench ER T3 precision
collapse (0.149 precision; recovered to 0.630 / F1 0.257 -> 0.747 by this fix),
diagnosed 2026-06-06 via scripts/dump_dqbench_er_tiers.py.
"""
from __future__ import annotations

import goldenmatch as gm
import polars as pl
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)


def _multi_member(res) -> list[list[int]]:
    clusters = res.clusters
    items = clusters.values() if isinstance(clusters, dict) else clusters
    out = []
    for c in items:
        members = c["members"] if isinstance(c, dict) else c
        if len(members) > 1:
            out.append(sorted(members))
    return out


def test_exact_matchkey_does_not_merge_blank_values():
    # 5 distinct people; three have a blanked phone. An exact_phone matchkey
    # must NOT merge the blank-phone records into one cluster.
    df = pl.DataFrame({
        "name": ["Alice", "Bob", "Carol", "Dave", "Erin"],
        "phone": ["1110000", "", "", "", "2220000"],
        "zip": ["27001"] * 5,
    })
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(name="exact_phone", type="exact",
                                  fields=[MatchkeyField(field="phone")])],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])]),
    )
    res = gm.dedupe_df(df, config=cfg)
    assert _multi_member(res) == [], "blank-phone records were wrongly merged"


def test_exact_matchkey_still_merges_real_shared_values():
    # Sanity: two records sharing a REAL phone value still match.
    df = pl.DataFrame({
        "name": ["Alice", "Alicia", "Bob"],
        "phone": ["5551234", "5551234", ""],
        "zip": ["27001"] * 3,
    })
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(name="exact_phone", type="exact",
                                  fields=[MatchkeyField(field="phone")])],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])]),
    )
    res = gm.dedupe_df(df, config=cfg)
    assert _multi_member(res) == [[0, 1]], "real shared phone should still merge (only)"
