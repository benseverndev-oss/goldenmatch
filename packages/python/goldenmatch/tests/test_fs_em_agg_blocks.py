"""Aggregate row-id-array EM blocks (`GOLDENMATCH_FS_EM_AGG_BLOCKS`, default ON).

EM training reads only ``__row_id__`` + each block's ``blocking_fields``
(``probabilistic._sample_blocked_pairs_with_fields``), so the EM-only blocks need
no per-block frames. ``blocker.build_em_blocks_agg`` builds them as compact
row-id arrays via one ``group_by().agg()`` per pass, eliminating the FS EM
``build_blocks`` memory peak (per-block-object floor + per-pass transient) --
whole-pipeline peak 2126->549 MB at 100k person, byte-identical output.

Locks:
  1. Gate resolution (default ON; ``0``/``false``/``off`` -> frame path).
  2. ``build_em_blocks_agg`` membership == ``build_blocks`` (same block keys +
     members) on static AND multi_pass, so EM's sample is unchanged.
  3. ``RowIdBlock`` satisfies the EM sampler interface (materialize / row_id /
     blocking_fields).
  4. Non-field strategies raise NotImplementedError (caller falls back).
  5. End-to-end output parity: agg on vs off produce identical clusters.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
)
from goldenmatch.core.blocker import build_blocks, build_em_blocks_agg
from goldenmatch.core.pipeline import _fs_em_agg_blocks_enabled

from tests.test_probabilistic import _make_dedupe_df, _make_probabilistic_mk


def _frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "__row_id__": list(range(1, 11)),
            "first_name": ["Bob", "Bobby", "Al", "Alan", "Cy", "Cyd", "Di", "Dee", "Ed", "Ed"],
            "surname": ["Ng", "Ng", "Fo", "Fo", "Xi", "Xi", "Yu", "Yu", "Zo", "Zo"],
            "zip": ["11", "11", "22", "22", "33", "33", "44", "44", "55", "55"],
        }
    )


def _members(blocks) -> set:
    """Set of (blocking_fields, frozenset(row_ids)) over blocks with >=2 rows."""
    out = set()
    for b in blocks:
        ids = b.materialize().column("__row_id__").to_list()
        if len(ids) >= 2:
            out.add((tuple(b.blocking_fields), frozenset(ids)))
    return out


def test_gate_default_on_and_off(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_FS_EM_AGG_BLOCKS", raising=False)
    assert _fs_em_agg_blocks_enabled() is True
    for off in ("0", "false", "no", "off", "OFF"):
        monkeypatch.setenv("GOLDENMATCH_FS_EM_AGG_BLOCKS", off)
        assert _fs_em_agg_blocks_enabled() is False
    for on in ("1", "true", "on", "whatever"):
        monkeypatch.setenv("GOLDENMATCH_FS_EM_AGG_BLOCKS", on)
        assert _fs_em_agg_blocks_enabled() is True


def test_agg_membership_matches_build_blocks_static():
    cfg = BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])])  # static
    agg = build_em_blocks_agg(_frame(), cfg)
    ref = build_blocks(_frame(), cfg)
    assert all(isinstance(b, type(agg[0])) for b in agg)  # RowIdBlock
    assert _members(agg) == _members(ref)


def test_agg_membership_matches_build_blocks_multipass():
    cfg = BlockingConfig(
        strategy="multi_pass",
        passes=[
            BlockingKeyConfig(fields=["zip"]),
            BlockingKeyConfig(fields=["surname"]),
        ],
    )
    agg = build_em_blocks_agg(_frame(), cfg)
    ref = build_blocks(_frame(), cfg)
    assert _members(agg) == _members(ref)


def test_rowid_block_interface():
    cfg = BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])])
    blocks = build_em_blocks_agg(_frame(), cfg)
    b = blocks[0]
    assert b.block_key is not None
    assert isinstance(b.blocking_fields, tuple)
    ids = b.materialize().column("__row_id__").to_list()
    assert b.n_rows() == len(ids) >= 2
    assert all(isinstance(i, int) for i in ids)


def test_non_field_strategy_raises():
    cfg = BlockingConfig(strategy="sorted_neighborhood", keys=[BlockingKeyConfig(fields=["zip"])])
    with pytest.raises(NotImplementedError):
        build_em_blocks_agg(_frame(), cfg)


def _partitions(result) -> list[tuple[int, ...]]:
    return sorted(
        tuple(sorted(c["members"]))
        for c in result.clusters.values()
        if len(c.get("members", [])) > 1
    )


def _cfg(**kwargs) -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[_make_probabilistic_mk()],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])]),
        **kwargs,
    )


def test_agg_output_parity_vs_frame_path(monkeypatch):
    """Row-id-array blocks must yield identical clusters to the frame path
    (EM reads only __row_id__; membership matches build_blocks)."""
    from goldenmatch import dedupe_df

    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    df = _make_dedupe_df().drop("__row_id__")

    monkeypatch.setenv("GOLDENMATCH_FS_EM_AGG_BLOCKS", "1")
    agg = dedupe_df(df, config=_cfg(backend="bucket"))
    monkeypatch.setenv("GOLDENMATCH_FS_EM_AGG_BLOCKS", "0")
    frame = dedupe_df(df, config=_cfg(backend="bucket"))

    assert _partitions(agg) == _partitions(frame)
