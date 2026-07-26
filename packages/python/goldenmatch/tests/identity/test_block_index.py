"""Persisted blocking index (C2 slice 1) -- store table + write/query API.

The control-plane-owned block-key index (manifesto §4(ii)) is the foundation for
incremental resolution against persisted identities without re-blocking the
corpus. This slice adds only the store index + its ``index_record_block_keys`` /
``candidates_by_block_keys`` API; wiring it into the incremental resolve path is
the next slice. These tests lock the index's write/query semantics.
"""
from __future__ import annotations

import pytest
from goldenmatch.identity import IdentityStore


@pytest.fixture()
def store(tmp_path):
    s = IdentityStore(path=str(tmp_path / "identity.db"))
    yield s
    s.close()


def test_table_exists_on_fresh_store(store):
    """The v6 schema ships the index table on a fresh DB."""
    row = store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='identity_record_block_keys'"
    ).fetchone()
    assert row is not None
    assert store._conn.execute("PRAGMA user_version").fetchone()[0] >= 6


def test_index_and_query_roundtrip(store):
    # r1 and r2 share block ("name", "smith"); r3 is alone.
    store.index_record_block_keys("r1", "e1", [("name", "smith"), ("zip", "10001")])
    store.index_record_block_keys("r2", "e2", [("name", "smith"), ("zip", "90210")])
    store.index_record_block_keys("r3", "e3", [("name", "jones"), ("zip", "10001")])

    # Query by the block a NEW record would fall in.
    assert store.candidates_by_block_keys([("name", "smith")]) == {"r1", "r2"}
    assert store.candidates_by_block_keys([("zip", "10001")]) == {"r1", "r3"}
    # Union across passes.
    assert store.candidates_by_block_keys(
        [("name", "jones"), ("zip", "90210")]
    ) == {"r3", "r2"}


def test_pass_sig_disambiguates_colliding_keys(store):
    """The same block_key STRING under different passes must NOT cross-match
    (soundex/substring/numeric keys share a value namespace)."""
    store.index_record_block_keys("r1", "e1", [("name_soundex", "S530")])
    store.index_record_block_keys("r2", "e2", [("zip_prefix", "S530")])
    assert store.candidates_by_block_keys([("name_soundex", "S530")]) == {"r1"}
    assert store.candidates_by_block_keys([("zip_prefix", "S530")]) == {"r2"}


def test_reindex_refreshes_entity_id_without_duplicating(store):
    store.index_record_block_keys("r1", "e1", [("name", "smith")])
    # r1 gets merged into e2 -> re-index with the new entity.
    store.index_record_block_keys("r1", "e2", [("name", "smith")])
    rows = store._conn.execute(
        "SELECT record_id, entity_id FROM identity_record_block_keys "
        "WHERE record_id='r1'"
    ).fetchall()
    assert len(rows) == 1  # no duplicate
    assert rows[0]["entity_id"] == "e2"
    # Still findable.
    assert store.candidates_by_block_keys([("name", "smith")]) == {"r1"}


def test_null_and_empty(store):
    # Null block keys are skipped; empty inputs are no-ops.
    store.index_record_block_keys("r1", "e1", [("name", None), ("zip", "10001")])
    store.index_record_block_keys("r2", "e2", [])
    assert store.candidates_by_block_keys([]) == set()
    assert store.candidates_by_block_keys([("name", "missing")]) == set()
    assert store.candidates_by_block_keys([("zip", "10001")]) == {"r1"}


def test_default_pass_sig(store):
    """pass_sig defaults to '' -- a single-pass index still round-trips."""
    store.index_record_block_keys("r1", "e1", [("", "smith")])
    assert store.candidates_by_block_keys([("", "smith")]) == {"r1"}


def test_null_entity_id_allowed(store):
    """A record can be indexed before it is resolved (entity_id NULL)."""
    store.index_record_block_keys("r1", None, [("name", "smith")])
    assert store.candidates_by_block_keys([("name", "smith")]) == {"r1"}


def test_chunking_over_many_keys(store):
    """Query keys beyond the SQLite host-parameter chunk still resolve."""
    keys = [("p", f"k{i}") for i in range(1000)]
    for i, (ps, bk) in enumerate(keys):
        store.index_record_block_keys(f"r{i}", f"e{i}", [(ps, bk)])
    got = store.candidates_by_block_keys(keys)
    assert got == {f"r{i}" for i in range(1000)}


def test_index_batches_inside_bulk_writes(store):
    """Population runs inside the resolve write transaction (WAL-bounded)."""
    with store.bulk_writes():
        store.index_record_block_keys("r1", "e1", [("name", "smith")])
        assert store._conn.in_transaction
    assert not store._conn.in_transaction
    assert store.candidates_by_block_keys([("name", "smith")]) == {"r1"}


def test_mongo_backend_rejected():
    store = IdentityStore.__new__(IdentityStore)
    store._backend = "mongo"
    with pytest.raises(NotImplementedError, match="block-key index"):
        store.index_record_block_keys("r1", "e1", [("name", "smith")])
    with pytest.raises(NotImplementedError, match="block-key index"):
        store.candidates_by_block_keys([("name", "smith")])
