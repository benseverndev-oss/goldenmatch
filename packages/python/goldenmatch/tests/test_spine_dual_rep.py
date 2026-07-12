"""D2s-a pins: exact-match + static-blocking entries accept a seam Frame.

The spine descent (plan 2026-07-12, D2s series) moves the arrow->polars
boundary below the collect; these fixtures pin that the two hot lazy-consumer
entries produce identical output for a pl.LazyFrame, a PolarsFrame, and an
ArrowFrame carrying the same rows.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.blocker import build_blocks
from goldenmatch.core.frame import ArrowFrame, PolarsFrame
from goldenmatch.core.scorer import _find_exact_match_ids, find_exact_matches


def _df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "__row_id__": pl.Series([0, 1, 2, 3, 4, 5], dtype=pl.Int64),
            "name": ["ann", "ann", "bob", None, "nan", "cat"],
            "__mk_k__": ["a1", "a1", "b2", None, "", "c3"],
        }
    )


def _entries():
    df = _df()
    return {
        "lazy": df.lazy(),
        "polars_frame": PolarsFrame(df),
        "arrow_frame": ArrowFrame(df.to_arrow()),
    }


@pytest.mark.parametrize("rep", ["lazy", "polars_frame", "arrow_frame"])
def test_find_exact_match_ids_dual_rep(rep):
    mk = MatchkeyConfig(name="k", type="exact", fields=[MatchkeyField(field="name")])
    ids_a, ids_b = _find_exact_match_ids(_entries()[rep], mk)
    assert sorted(zip(ids_a.tolist(), ids_b.tolist())) == [(0, 1)]


@pytest.mark.parametrize("rep", ["lazy", "polars_frame", "arrow_frame"])
def test_find_exact_matches_dual_rep(rep):
    mk = MatchkeyConfig(name="k", type="exact", fields=[MatchkeyField(field="name")])
    assert find_exact_matches(_entries()[rep], mk) == [(0, 1, 1.0)]


@pytest.mark.parametrize("rep", ["lazy", "polars_frame", "arrow_frame"])
def test_build_static_blocks_dual_rep(rep):
    # "nan" is a sentinel-filtered key; null drops; ann pair survives.
    cfg = BlockingConfig(keys=[BlockingKeyConfig(fields=["name"])])
    blocks = build_blocks(_entries()[rep], cfg)
    keyed = {b.block_key: sorted(b.materialize().column("__row_id__").to_list()) for b in blocks}
    assert keyed == {"ann": [0, 1]}
