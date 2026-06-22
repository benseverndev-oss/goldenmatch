"""Streaming / micro-batch incremental identity resolution (#1109).

`resolve_record_incremental` resolves ONE new record at a time -- matching it
against the existing frame and then create/absorb/merge into the durable
identity graph -- without re-running the batch pipeline. `match_record_to_entity`
is the read-only "which entity would this match" companion.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.identity import (
    IdentityStore,
    match_record_to_entity,
    resolve_clusters,
    resolve_record_incremental,
)


@pytest.fixture()
def store(tmp_path):
    s = IdentityStore(path=str(tmp_path / "identity.db"))
    yield s
    s.close()


def _df(rows):
    out = []
    for i, r in enumerate(rows):
        rec = {"__row_id__": i, "__source__": "src"}
        rec.update(r)
        out.append(rec)
    return pl.DataFrame(out)


# A weighted matchkey on `name` -- match_one needs a threshold-bearing matchkey.
MK = MatchkeyConfig(
    name="mk",
    type="weighted",
    threshold=0.8,
    fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
)


@pytest.fixture()
def base(store):
    """Two records already resolved into the graph: Alice (src:1), Bob (src:2)."""
    df = _df([{"id": "1", "name": "Alice"}, {"id": "2", "name": "Bob"}])
    resolve_clusters(
        {0: {"members": [0], "size": 1, "pair_scores": {}, "confidence": 1.0},
         1: {"members": [1], "size": 1, "pair_scores": {}, "confidence": 1.0}},
        df, [], "mk", store, run_name="batch", source_pk_col="id",
    )
    return df


def test_creates_new_entity_when_nothing_matches(store, base):
    before = store.count_identities()
    eid = resolve_record_incremental(
        {"id": "9", "name": "Zoe", "__source__": "src"}, base, [MK], store,
        run_name="stream", source_pk_col="id",
    )
    assert eid is not None
    assert store.count_identities() == before + 1
    assert store.find_entity_by_record("src:9") == eid


def test_absorbs_into_existing_entity_on_match(store, base):
    alice = store.find_entity_by_record("src:1")
    before = store.count_identities()
    eid = resolve_record_incremental(
        {"id": "3", "name": "Alice", "__source__": "src"}, base, [MK], store,
        run_name="stream", source_pk_col="id",
    )
    # Resolved INTO Alice's entity, no new identity minted.
    assert eid == alice
    assert store.count_identities() == before
    assert store.find_entity_by_record("src:3") == alice


def test_match_record_to_entity_is_read_only(store, base):
    alice = store.find_entity_by_record("src:1")
    before = store.count_identities()
    hits = match_record_to_entity(
        {"id": "5", "name": "Alice"}, base, [MK], store, source_pk_col="id",
    )
    assert alice in hits
    assert hits[alice] >= 0.8
    # Nothing was written.
    assert store.count_identities() == before
    assert store.find_entity_by_record("src:5") is None


def test_match_record_to_entity_empty_when_no_match(store, base):
    assert match_record_to_entity(
        {"id": "5", "name": "Zoe"}, base, [MK], store, source_pk_col="id",
    ) == {}


def test_idempotent_reingest_same_record(store, base):
    before = store.count_identities()
    e1 = resolve_record_incremental(
        {"id": "7", "name": "Carol"}, base, [MK], store,
        run_name="s1", source_pk_col="id",
    )
    after_first = store.count_identities()
    e2 = resolve_record_incremental(
        {"id": "7", "name": "Carol"}, base, [MK], store,
        run_name="s1", source_pk_col="id",
    )
    assert e1 == e2
    assert after_first == before + 1
    # Re-ingesting the same record_id absorbs into its own entity, no new mint.
    assert store.count_identities() == after_first


def test_bootstraps_on_empty_base(store):
    """First record into an empty frame creates the first identity."""
    empty = pl.DataFrame(
        {"__row_id__": [], "__source__": [], "id": [], "name": []},
        schema={"__row_id__": pl.Int64, "__source__": pl.Utf8,
                "id": pl.Utf8, "name": pl.Utf8},
    )
    eid = resolve_record_incremental(
        {"id": "1", "name": "Alice", "__source__": "src"}, empty, [MK], store,
        run_name="stream", source_pk_col="id",
    )
    assert eid is not None
    assert store.count_identities() == 1
    assert store.find_entity_by_record("src:1") == eid


def test_stream_processor_resolves_to_entity(store, base):
    """StreamProcessor.resolve_to_entity wires the streaming path into the
    identity graph: it absorbs a match AND accumulates the record."""
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        IdentityConfig,
    )
    from goldenmatch.core.streaming import StreamProcessor

    cfg = GoldenMatchConfig(
        matchkeys=[MK],
        blocking=BlockingConfig(
            strategy="static", keys=[BlockingKeyConfig(fields=["name"])]
        ),
        identity=IdentityConfig(source_pk_column="id"),
    )
    alice = store.find_entity_by_record("src:1")
    sp = StreamProcessor(base, cfg)
    before_rows = sp.data.height

    eid = sp.resolve_to_entity(
        {"id": "3", "name": "Alice", "__source__": "src"}, store, run_name="stream",
    )
    assert eid == alice
    # The record was accumulated so later records can match it.
    assert sp.data.height == before_rows + 1
    assert sp.stats.records_processed == 1


def test_no_pk_content_hash_record_resolves(store):
    """Without a source PK, the content-hash record_id round-trips correctly
    (the dtype-aligned mini-frame keeps the matched row's payload hash stable)."""
    df = _df([{"name": "Alice"}])
    resolve_clusters(
        {0: {"members": [0], "size": 1, "pair_scores": {}, "confidence": 1.0}},
        df, [], "mk", store, run_name="batch",
    )
    assert store.count_identities() == 1
    eid = resolve_record_incremental(
        {"name": "Alice"}, df, [MK], store, run_name="stream",
    )
    # Same content -> matched and absorbed into the single existing identity
    # (no second identity minted -> the no-PK record_id round-tripped).
    assert eid is not None
    assert store.count_identities() == 1
