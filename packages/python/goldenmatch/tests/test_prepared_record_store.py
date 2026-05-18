"""Unit tests for PreparedRecordStore (Component 1 of Distributed Plan v1).

Spec: docs/superpowers/specs/2026-05-15-distributed-plan-v1-design.md
§Component 1.
"""
from __future__ import annotations

from pathlib import Path

import polars as pl
from goldenmatch.distributed.record_store import (
    PreparedRecordStore,
    load_prepared_records,
    materialize_prepared_records,
)


def _sample_df() -> pl.DataFrame:
    return pl.DataFrame({
        "__row_id__": [0, 1, 2, 3],
        "name": ["alice", "bob", "charlie", "dana"],
        "email": ["a@x.com", "b@x.com", "c@x.com", "d@x.com"],
        "__mk_email_lower__": ["a@x.com", "b@x.com", "c@x.com", "d@x.com"],
    })


def test_store_init_creates_tempdir(tmp_path: Path):
    store = PreparedRecordStore(base_dir=tmp_path)
    assert store.path.exists()
    assert store.path.parent == tmp_path
    store.close()


def test_store_init_creates_tempfile_when_no_base_dir():
    """No base_dir -> mkstemp into system temp. Cleaned up on close()."""
    store = PreparedRecordStore()
    p = store.path
    assert p.exists()
    store.close()
    assert not p.exists()


def test_materialize_and_load_roundtrips_dataframe(tmp_path: Path):
    """Polars -> DuckDB -> Polars roundtrip preserves data + dtypes."""
    store = PreparedRecordStore(base_dir=tmp_path)
    try:
        df = _sample_df()
        signature = "sig-v1"
        materialize_prepared_records(store, df, signature=signature)
        loaded = load_prepared_records(store, signature=signature)
        # Order may differ post-DuckDB; compare as sets of row tuples.
        assert set(loaded.iter_rows()) == set(df.iter_rows())
        assert set(loaded.columns) == set(df.columns)
    finally:
        store.close()


def test_load_missing_signature_returns_none(tmp_path: Path):
    """Cache miss is signaled by None; callers prep + materialize."""
    store = PreparedRecordStore(base_dir=tmp_path)
    try:
        assert load_prepared_records(store, signature="missing") is None
    finally:
        store.close()


def test_signature_isolates_entries(tmp_path: Path):
    """Two different signatures address two different cached frames."""
    store = PreparedRecordStore(base_dir=tmp_path)
    try:
        df1 = _sample_df()
        df2 = pl.DataFrame({"__row_id__": [10], "x": ["other"]})
        materialize_prepared_records(store, df1, signature="sig-a")
        materialize_prepared_records(store, df2, signature="sig-b")
        loaded_a = load_prepared_records(store, signature="sig-a")
        loaded_b = load_prepared_records(store, signature="sig-b")
        assert loaded_a is not None
        assert loaded_b is not None
        assert set(loaded_a.columns) == {"__row_id__", "name", "email", "__mk_email_lower__"}
        assert set(loaded_b.columns) == {"__row_id__", "x"}
    finally:
        store.close()


def test_close_is_idempotent(tmp_path: Path):
    """Multiple close() calls don't raise — important for finally blocks
    in the controller's exception paths."""
    store = PreparedRecordStore(base_dir=tmp_path)
    store.close()
    store.close()  # no-op


def test_close_cleans_up_file(tmp_path: Path):
    """close() removes the underlying DuckDB file when cleanup=True."""
    store = PreparedRecordStore(base_dir=tmp_path)
    p = store.path
    assert p.exists()
    store.close()
    assert not p.exists()


def test_close_preserves_file_when_cleanup_false(tmp_path: Path):
    """cleanup=False keeps the file (useful for cross-call persistence)."""
    store = PreparedRecordStore(base_dir=tmp_path, cleanup=False)
    p = store.path
    materialize_prepared_records(store, _sample_df(), signature="sig-v1")
    store.close()
    assert p.exists()
    # Re-open and read back.
    store2 = PreparedRecordStore(path=p, cleanup=False)
    try:
        loaded = load_prepared_records(store2, signature="sig-v1")
        assert loaded is not None
        assert loaded.height == 4
    finally:
        store2.close()


def test_context_manager_closes_on_exit(tmp_path: Path):
    """PreparedRecordStore is a context manager."""
    with PreparedRecordStore(base_dir=tmp_path) as store:
        p = store.path
        assert p.exists()
    assert not p.exists()


# ---------------------------------------------------------------------------
# Component 3 prereq: read_only kwarg (Phase 1 of distributed-scoring plan)
# ---------------------------------------------------------------------------


def test_read_only_false_allows_write(tmp_path: Path):
    """Default read_only=False permits materialize_prepared_records."""
    store = PreparedRecordStore(base_dir=tmp_path, read_only=False)
    try:
        materialize_prepared_records(store, _sample_df(), signature="sig-ro")
        loaded = load_prepared_records(store, signature="sig-ro")
        assert loaded is not None
        assert loaded.height == 4
    finally:
        store.close()


def test_read_only_true_raises_on_write(tmp_path: Path):
    """read_only=True opens the store in DuckDB read-only mode; writes raise."""
    # First create a valid DuckDB file with a known-path store.
    p = tmp_path / "ro_test.duckdb"
    writer = PreparedRecordStore(path=p, cleanup=False)
    materialize_prepared_records(writer, _sample_df(), signature="sig-ro")
    writer.close()

    # Re-open in read-only mode; any write must raise.
    store = PreparedRecordStore(path=p, cleanup=False, read_only=True)
    try:
        import pytest
        with pytest.raises(Exception):
            materialize_prepared_records(store, _sample_df(), signature="sig-ro-new")
    finally:
        store.close()


def test_read_only_true_allows_read(tmp_path: Path):
    """read_only=True can read tables that were written before opening."""
    p = tmp_path / "ro_read_test.duckdb"
    writer = PreparedRecordStore(path=p, cleanup=False)
    materialize_prepared_records(writer, _sample_df(), signature="sig-ro-read")
    writer.close()

    store = PreparedRecordStore(path=p, cleanup=False, read_only=True)
    try:
        loaded = load_prepared_records(store, signature="sig-ro-read")
        assert loaded is not None
        assert set(loaded.iter_rows()) == set(_sample_df().iter_rows())
    finally:
        store.close()
