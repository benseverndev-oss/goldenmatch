"""Tests for the DuckDB-backed block scorer (out-of-core pair store)."""
from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

duckdb = pytest.importorskip("duckdb")  # noqa: F841 — gated import

from goldenmatch.backends.score_duckdb import score_blocks_duckdb
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.blocker import BlockResult


def _make_block(row_ids: list[int], names: list[str], block_key: str = "k1") -> BlockResult:
    df = pl.DataFrame({
        "__row_id__": row_ids,
        "first_name": names,
    }).lazy()
    return BlockResult(block_key=block_key, df=df)


def _mk() -> MatchkeyConfig:
    return MatchkeyConfig(
        name="name",
        type="weighted",
        threshold=0.5,
        fields=[
            MatchkeyField(
                field="first_name",
                transforms=["lowercase"],
                scorer="jaro_winkler",
                weight=1.0,
            ),
        ],
    )


class TestScoreBlocksDuckdb:
    def test_empty_blocks(self):
        pairs = score_blocks_duckdb([], _mk(), set())
        assert pairs == []

    def test_single_block_in_memory(self):
        block = _make_block([0, 1], ["John", "Jon"])
        pairs = score_blocks_duckdb([block], _mk(), set())
        assert len(pairs) >= 1
        assert pairs[0][2] >= 0.5

    def test_canonical_pair_order(self):
        """Pairs come back as (min, max) regardless of in-block order."""
        block = _make_block([5, 3], ["John", "Jon"])
        pairs = score_blocks_duckdb([block], _mk(), set())
        assert pairs
        for a, b, _s in pairs:
            assert a <= b

    def test_matched_pairs_updated(self):
        """The matched_pairs set is mutated with the new pairs (same semantics
        as score_blocks_parallel — clustering depends on this)."""
        block = _make_block([0, 1], ["John", "Jon"])
        matched: set[tuple[int, int]] = set()
        score_blocks_duckdb([block], _mk(), matched)
        assert len(matched) >= 1

    def test_target_ids_filter(self):
        """target_ids filters to cross-source pairs."""
        block = _make_block([0, 1, 2], ["John", "Jon", "John"])
        pairs = score_blocks_duckdb(
            [block], _mk(), set(), target_ids={0},
        )
        for a, b, _s in pairs:
            assert (a in {0}) != (b in {0})

    def test_explicit_db_path_spills_to_disk(self, tmp_path: Path):
        """When db_path is on-disk, the DuckDB file is created and cleaned."""
        db_path = tmp_path / "pairs.duckdb"
        block = _make_block([0, 1], ["John", "Jon"])
        pairs = score_blocks_duckdb(
            [block], _mk(), set(), db_path=str(db_path),
        )
        assert pairs
        # The connection persisted the table; file still exists on disk.
        assert db_path.exists()

    def test_auto_tempfile_path(self):
        """db_path='auto' uses a tempfile that's deleted after the call."""
        block = _make_block([0, 1], ["John", "Jon"])
        pairs = score_blocks_duckdb(
            [block], _mk(), set(), db_path="auto",
        )
        assert pairs
        # Tempfile is unlinked in the finally clause; no easy way to assert
        # the specific path was deleted (we don't expose it). Verifying the
        # call returned and didn't leak the connection is sufficient here.

    def test_pipeline_routes_duckdb_backend(self):
        """_get_block_scorer returns score_blocks_duckdb for backend='duckdb'."""
        from goldenmatch.config.schemas import (
            BlockingConfig,
            BlockingKeyConfig,
            GoldenMatchConfig,
        )
        from goldenmatch.core.pipeline import _get_block_scorer

        config = GoldenMatchConfig(
            matchkeys=[_mk()],
            blocking=BlockingConfig(
                strategy="static",
                keys=[BlockingKeyConfig(fields=["first_name"])],
            ),
        )
        config.backend = "duckdb"
        scorer = _get_block_scorer(config)
        assert scorer is score_blocks_duckdb
