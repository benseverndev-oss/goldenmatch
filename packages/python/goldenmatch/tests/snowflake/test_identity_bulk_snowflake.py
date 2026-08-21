"""Bulk staged-MERGE path, and its equivalence to the singleton path."""
from __future__ import annotations

from datetime import datetime

import pytest

fakesnow = pytest.importorskip("fakesnow")
pl = pytest.importorskip("polars")

from tests.snowflake.test_identity_store_snowflake import store  # noqa: F401,E402


def _nodes_df(ids):
    now = datetime(2026, 8, 20, 12, 0, 0)
    return pl.DataFrame(
        [
            {
                "entity_id": eid, "status": "active", "merged_into": None,
                "golden_record": None, "confidence": 0.9, "dataset": "c",
                "created_at": now, "updated_at": now,
            }
            for eid in ids
        ],
        schema={
            "entity_id": pl.Utf8, "status": pl.Utf8, "merged_into": pl.Utf8,
            "golden_record": pl.Utf8, "confidence": pl.Float64,
            "dataset": pl.Utf8, "created_at": pl.Datetime,
            "updated_at": pl.Datetime,
        },
    )


def _edges_df(rows):
    return pl.DataFrame(
        rows,
        schema={
            "entity_id": pl.Utf8, "record_a_id": pl.Utf8, "record_b_id": pl.Utf8,
            "kind": pl.Utf8, "score": pl.Float64, "matchkey_name": pl.Utf8,
            "controller_snapshot": pl.Utf8, "run_name": pl.Utf8, "dataset": pl.Utf8,
            "actor": pl.Utf8, "trust": pl.Float64, "recorded_at": pl.Datetime,
        },
    )


def test_supports_bulk_is_true(store) -> None:  # noqa: F811
    assert store.supports_bulk is True


def test_bulk_upsert_identities_inserts(store) -> None:  # noqa: F811
    from goldenmatch.identity.store import new_entity_id

    ids = [new_entity_id() for _ in range(3)]
    store.bulk_upsert_identities(_nodes_df(ids))
    assert store.count_identities() == 3


def test_bulk_upsert_identities_is_idempotent(store) -> None:  # noqa: F811
    from goldenmatch.identity.store import new_entity_id

    ids = [new_entity_id() for _ in range(3)]
    df = _nodes_df(ids)
    store.bulk_upsert_identities(df)
    store.bulk_upsert_identities(df)
    assert store.count_identities() == 3


def test_bulk_and_singleton_agree(store) -> None:  # noqa: F811
    """The two write paths must produce identical rows."""
    from goldenmatch.identity.model import IdentityNode
    from goldenmatch.identity.store import new_entity_id

    bulk_id, single_id = new_entity_id(), new_entity_id()
    store.bulk_upsert_identities(_nodes_df([bulk_id]))
    now = datetime(2026, 8, 20, 12, 0, 0)
    store.upsert_identity(IdentityNode(
        entity_id=single_id, status="active", confidence=0.9, dataset="c",
        created_at=now, updated_at=now,
    ))
    a, b = store.get_identity(bulk_id), store.get_identity(single_id)
    assert a is not None and b is not None
    for field_name in ("status", "confidence", "dataset", "merged_into"):
        assert getattr(a, field_name) == getattr(b, field_name), field_name


def test_bulk_on_empty_frame_is_a_noop(store) -> None:  # noqa: F811
    store.bulk_upsert_identities(_nodes_df([]))
    assert store.count_identities() == 0


def test_bulk_add_edges_is_idempotent_no_duplicates(store) -> None:  # noqa: F811
    """``stage_and_merge(update_cols=None)`` is the insert-if-absent path that
    replaces the UNIQUE constraint Snowflake does not enforce. Calling
    ``bulk_add_edges`` twice with the same rows (a resolve replay) must not
    duplicate the edge -- this is the entire replay-idempotency mechanism for
    the batched write path, and had no test anywhere before this one."""
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    now = datetime(2026, 8, 20, 12, 0, 0)
    rows = [
        {
            "entity_id": eid, "record_a_id": "recA", "record_b_id": "recB",
            "kind": "same_as", "score": 0.95, "matchkey_name": "mk1",
            "controller_snapshot": None, "run_name": "run-1", "dataset": "c",
            "actor": None, "trust": None, "recorded_at": now,
        },
    ]
    df = _edges_df(rows)
    store.bulk_add_edges(df)
    store.bulk_add_edges(df)
    edges = store.edges_for_entity(eid)
    assert len(edges) == 1
    assert edges[0].record_a_id == "recA"
    assert edges[0].record_b_id == "recB"


def test_bulk_add_edges_and_add_edge_agree(store) -> None:  # noqa: F811
    """bulk_add_edges (insert-if-absent) must match add_edge's own MERGE
    semantics: two calls with the same key never duplicate."""
    from goldenmatch.identity.model import EvidenceEdge
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    now = datetime(2026, 8, 20, 12, 0, 0)
    edge = EvidenceEdge(
        entity_id=eid, record_a_id="r1", record_b_id="r2", kind="same_as",
        score=0.8, run_name="run-1", dataset="c", recorded_at=now,
    )
    store.add_edge(edge, return_id=False)
    df = _edges_df([
        {
            "entity_id": eid, "record_a_id": "r1", "record_b_id": "r2",
            "kind": "same_as", "score": 0.8, "matchkey_name": None,
            "controller_snapshot": None, "run_name": "run-1", "dataset": "c",
            "actor": None, "trust": None, "recorded_at": now,
        },
    ])
    store.bulk_add_edges(df)
    edges = store.edges_for_entity(eid)
    assert len(edges) == 1
