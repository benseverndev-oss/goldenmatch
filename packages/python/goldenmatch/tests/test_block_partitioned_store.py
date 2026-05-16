"""Unit tests for block-partitioned helpers in PreparedRecordStore.

Component 2 of Distributed Plan v1. The Component 1 primitive
(PR #280) handled one-prepared-df-per-signature. Component 2 adds
per-block partitions so per-block scoring can stream from disk.
"""
from __future__ import annotations

from pathlib import Path

import polars as pl
from goldenmatch.distributed.record_store import (
    PreparedRecordStore,
    iter_blocks,
    list_blocks,
    load_block,
    materialize_blocks,
)


def _sample_df() -> pl.DataFrame:
    # 6 rows, 3 blocks of 2 each.
    return pl.DataFrame({
        "__row_id__": [0, 1, 2, 3, 4, 5],
        "name": ["alice", "alyce", "bob", "robert", "carol", "caryl"],
        "email": [f"u{i}@x.com" for i in range(6)],
    })


def _block_assignments() -> dict[int, str]:
    # __row_id__ -> block_key
    return {0: "A1", 1: "A1", 2: "B2", 3: "B2", 4: "C3", 5: "C3"}


def test_materialize_blocks_writes_one_table_per_block(tmp_path: Path):
    with PreparedRecordStore(base_dir=tmp_path) as store:
        df = _sample_df()
        materialize_blocks(store, df, block_assignments=_block_assignments(), signature="sig-v1")
        keys = list_blocks(store, signature="sig-v1")
        assert sorted(keys) == ["A1", "B2", "C3"]


def test_load_block_roundtrips_only_member_rows(tmp_path: Path):
    with PreparedRecordStore(base_dir=tmp_path) as store:
        df = _sample_df()
        materialize_blocks(store, df, block_assignments=_block_assignments(), signature="sig-v1")
        a1 = load_block(store, signature="sig-v1", block_key="A1")
        assert a1 is not None
        assert sorted(a1["__row_id__"].to_list()) == [0, 1]
        assert set(a1.columns) == set(df.columns)


def test_load_missing_block_returns_none(tmp_path: Path):
    with PreparedRecordStore(base_dir=tmp_path) as store:
        materialize_blocks(store, _sample_df(), block_assignments=_block_assignments(), signature="sig-v1")
        assert load_block(store, signature="sig-v1", block_key="ZZ") is None
        assert load_block(store, signature="missing-sig", block_key="A1") is None


def test_signature_isolates_block_namespaces(tmp_path: Path):
    """Two signatures with overlapping block keys must not collide."""
    with PreparedRecordStore(base_dir=tmp_path) as store:
        df1 = _sample_df()
        df2 = pl.DataFrame({"__row_id__": [10, 11], "name": ["x", "y"], "email": ["x@", "y@"]})
        materialize_blocks(store, df1, block_assignments=_block_assignments(), signature="sig-a")
        materialize_blocks(store, df2, block_assignments={10: "A1", 11: "A1"}, signature="sig-b")
        a_a1 = load_block(store, signature="sig-a", block_key="A1")
        b_a1 = load_block(store, signature="sig-b", block_key="A1")
        assert a_a1 is not None and b_a1 is not None
        assert sorted(a_a1["__row_id__"].to_list()) == [0, 1]
        assert sorted(b_a1["__row_id__"].to_list()) == [10, 11]


def test_iter_blocks_yields_all_partitions(tmp_path: Path):
    with PreparedRecordStore(base_dir=tmp_path) as store:
        materialize_blocks(store, _sample_df(), block_assignments=_block_assignments(), signature="sig-v1")
        yielded = list(iter_blocks(store, signature="sig-v1"))
        keys = sorted(k for k, _ in yielded)
        assert keys == ["A1", "B2", "C3"]
        assert all(df.height == 2 for _, df in yielded)


def test_iter_blocks_empty_signature_yields_nothing(tmp_path: Path):
    with PreparedRecordStore(base_dir=tmp_path) as store:
        assert list(iter_blocks(store, signature="never-materialized")) == []


def test_unsafe_block_keys_handled(tmp_path: Path):
    """Block keys with special chars / unicode must not break DuckDB table naming."""
    with PreparedRecordStore(base_dir=tmp_path) as store:
        df = pl.DataFrame({"__row_id__": [0, 1], "name": ["a", "b"], "email": ["a@", "b@"]})
        # Keys with quotes / unicode / spaces.
        assignments = {0: "block with spaces", 1: "block'\"unicode-Ω"}
        materialize_blocks(store, df, block_assignments=assignments, signature="sig-v1")
        keys = list_blocks(store, signature="sig-v1")
        assert sorted(keys) == sorted(["block with spaces", "block'\"unicode-Ω"])
        loaded = load_block(store, signature="sig-v1", block_key="block with spaces")
        assert loaded is not None
        assert loaded["__row_id__"].to_list() == [0]
