"""Block-key computation + population for the persisted index (C2 slice 2).

Locks the stateless-compute half of the bidirectional seam: a record's
``(pass_sig, block_key)`` pairs (computed with the SAME expression the batch
blocker uses) + backfill population of the store index, so incremental
resolution can later query candidates without re-blocking the corpus.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
from goldenmatch.identity import IdentityStore
from goldenmatch.identity.block_index import (
    backfill_block_index,
    compute_frame_block_keys,
    compute_record_block_keys,
)


@pytest.fixture()
def store(tmp_path):
    s = IdentityStore(path=str(tmp_path / "identity.db"))
    yield s
    s.close()


def _static(fields, transforms=None):
    return BlockingConfig(
        strategy="static",
        keys=[BlockingKeyConfig(fields=fields, transforms=transforms or [])],
    )


def _multi_pass(*passes):
    return BlockingConfig(
        strategy="multi_pass",
        passes=[BlockingKeyConfig(fields=f, transforms=t or []) for f, t in passes],
    )


def test_compute_record_block_keys_static():
    blocking = _static(["last"], ["lowercase"])
    keys = compute_record_block_keys({"last": "Smith"}, blocking)
    assert keys == [("last::lowercase", "smith")]


def test_compute_record_block_keys_multi_pass():
    blocking = _multi_pass((["last"], ["lowercase"]), (["zip"], []))
    keys = compute_record_block_keys({"last": "Smith", "zip": "10001"}, blocking)
    assert ("last::lowercase", "smith") in keys
    assert ("zip::", "10001") in keys
    assert len(keys) == 2


def test_pass_sig_disambiguates_different_fields():
    """Two passes over DIFFERENT fields that happen to produce the same key
    string must carry distinct pass_sigs."""
    blocking = _multi_pass((["a"], []), (["b"], []))
    keys = compute_record_block_keys({"a": "X", "b": "X"}, blocking)
    sigs = {sig for sig, _ in keys}
    assert sigs == {"a::", "b::"}


def test_null_block_key_dropped():
    blocking = _static(["last"])
    assert compute_record_block_keys({"last": None}, blocking) == []


def test_missing_field_pass_skipped():
    """A pass whose field isn't present is skipped, not an error."""
    blocking = _multi_pass((["last"], []), (["phone"], []))
    keys = compute_record_block_keys({"last": "Smith"}, blocking)  # no phone col
    assert keys == [("last::", "Smith")]


def test_frame_keys_match_batch_blocker():
    """The per-record block key equals what the batch blocker computes for the
    same row (reuses _build_block_key_expr -> parity by construction)."""
    from goldenmatch.core.blocker import _build_block_key_expr

    df = pl.DataFrame({
        "__row_id__": [0, 1, 2],
        "last": ["Smith", "smith", "Jones"],
    })
    kc = BlockingKeyConfig(fields=["last"], transforms=["lowercase"])
    batch = df.select(_build_block_key_expr(kc)).to_series().to_list()
    computed = compute_frame_block_keys(df, _static(["last"], ["lowercase"]))
    for rid, expected in enumerate(batch):
        assert computed[rid] == [("last::lowercase", expected)]


def test_backfill_populates_and_queries(store):
    df = pl.DataFrame({
        "__row_id__": [0, 1, 2],
        "id": ["u0", "u1", "u2"],
        "last": ["Smith", "Smith", "Jones"],
    })
    blocking = _static(["last"])
    n = backfill_block_index(store, df, blocking, source="src", source_pk_col="id")
    assert n == 3

    # A new record blocking on "Smith" finds the two persisted Smiths.
    keys = compute_record_block_keys({"last": "Smith"}, blocking)
    cands = store.candidates_by_block_keys(keys)
    assert cands == {"src:u0", "src:u1"}
    # Jones finds only the Jones.
    assert store.candidates_by_block_keys(
        compute_record_block_keys({"last": "Jones"}, blocking)
    ) == {"src:u2"}


def test_backfill_carries_entity_id(store):
    from datetime import datetime

    from goldenmatch.identity.model import IdentityNode, SourceRecord

    # Pre-write the identity node (source_records.entity_id FKs to it), then a
    # source record pointing at it.
    store.upsert_identity(IdentityNode(
        entity_id="e-alice", status="active",
        created_at=datetime.now(), updated_at=datetime.now(),
    ))
    store.upsert_record(SourceRecord(
        record_id="src:u0", source="src", source_pk="u0",
        record_hash="h", entity_id="e-alice",
        first_seen_at=datetime.now(), last_seen_at=datetime.now(),
    ))
    df = pl.DataFrame({"__row_id__": [0], "id": ["u0"], "last": ["Smith"]})
    backfill_block_index(store, df, _static(["last"]), source="src", source_pk_col="id")
    row = store._conn.execute(
        "SELECT entity_id FROM identity_record_block_keys WHERE record_id='src:u0'"
    ).fetchone()
    assert row["entity_id"] == "e-alice"


def test_backfill_requires_row_id(store):
    with pytest.raises(ValueError, match="__row_id__"):
        backfill_block_index(store, pl.DataFrame({"last": ["x"]}), _static(["last"]))
