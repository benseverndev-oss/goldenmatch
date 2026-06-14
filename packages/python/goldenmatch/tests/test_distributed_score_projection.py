"""#957: column projection before the block-shuffle must be output-invariant.

The block-shuffle `_explode` used to move a copy of the FULL record per
co-location key; `_project_to_scoring_columns` now drops columns scoring never
reads BEFORE the shuffle. These tests pin that the projection keeps exactly the
right columns and never changes the scored pair set.

Polars-only (no Ray): `goldenmatch.distributed.scoring` defers all ray imports,
and `_score_colocated_groups` scores via the in-memory kernel.
"""
from __future__ import annotations

import polars as pl


def _cfg():
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    return GoldenMatchConfig(
        blocking=BlockingConfig(
            strategy="static", keys=[BlockingKeyConfig(fields=["last_name"])],
        ),
        matchkeys=[
            MatchkeyConfig(
                name="fn", type="weighted", threshold=0.80,
                fields=[MatchkeyField(
                    field="first_name", scorer="jaro_winkler", weight=1.0,
                )],
            )
        ],
    )


def test_project_keeps_referenced_and_synthetic_drops_wide():
    """Keep __row_id__ + config-referenced cols + synthetic __-cols; drop only
    unreferenced raw user columns."""
    from goldenmatch.distributed.scoring import _project_to_scoring_columns

    df = pl.DataFrame({
        "__row_id__":     [0, 1],
        "first_name":     ["alice", "alyce"],   # matchkey field (referenced)
        "last_name":      ["surA", "surA"],     # blocking field (referenced)
        "__domain_key__": ["d", "d"],           # synthetic upstream col
        "free_text_blob": ["x" * 200, "y" * 200],  # wide, unreferenced -> dropped
        "notes":          ["n1", "n2"],         # unreferenced -> dropped
    })

    out = _project_to_scoring_columns(df, _cfg())

    assert set(out.columns) == {
        "__row_id__", "first_name", "last_name", "__domain_key__"
    }
    assert "free_text_blob" not in out.columns
    assert "notes" not in out.columns
    # rows untouched
    assert out.height == 2


def test_project_noop_when_nothing_droppable():
    """All columns referenced/synthetic -> the same frame back (no copy churn)."""
    from goldenmatch.distributed.scoring import _project_to_scoring_columns

    df = pl.DataFrame({
        "__row_id__": [0, 1],
        "first_name": ["alice", "alyce"],
        "last_name":  ["surA", "surA"],
    })
    out = _project_to_scoring_columns(df, _cfg())
    assert set(out.columns) == set(df.columns)


def test_projection_is_score_invariant():
    """Scoring the co-located partition yields the SAME pairs with or without
    the unreferenced wide column -- the whole justification for projecting."""
    from goldenmatch.distributed.scoring import (
        _project_to_scoring_columns,
        _score_colocated_groups,
    )

    cfg = _cfg()
    # Shape as it enters _score_colocated_groups: __keyid__/__block_key__ (it
    # drops them), __row_id__, the fields, + a wide unreferenced column.
    full = pl.DataFrame({
        "__row_id__":     [0, 1],
        "__keyid__":      ["k", "k"],
        "__block_key__":  ["surA", "surA"],
        "first_name":     ["alice", "alyce"],
        "last_name":      ["surA", "surA"],
        "free_text_blob": ["x" * 200, "y" * 200],
    })
    projected = _project_to_scoring_columns(full, cfg)

    pairs_full = sorted(_score_colocated_groups(full, cfg))
    pairs_proj = sorted(_score_colocated_groups(projected, cfg))

    assert pairs_full == pairs_proj
