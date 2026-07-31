"""Tests for the semantic-layer <-> Customer 360 drill-through (semantic/serving.py)."""
from __future__ import annotations

import pyarrow as pa
import pytest
from goldenmatch.identity import IdentityNode, IdentityStore, SourceRecord, new_entity_id
from goldenmatch.semantic import entity_360, profile_from_crosswalk
from goldenmatch.semantic.crosswalk import ResolvedCrosswalk


@pytest.fixture()
def seeded(tmp_path):
    """A durable store with one entity (eid) carrying record crm:1, plus a
    crosswalk mapping source_pk '1' -> that entity."""
    path = str(tmp_path / "identity.db")
    eid = new_entity_id()
    with IdentityStore(path=path) as s:
        s.upsert_identity(IdentityNode(entity_id=eid, dataset="crm", confidence=0.9))
        s.upsert_record(SourceRecord("crm:1", "crm", "1", "h1", entity_id=eid, dataset="crm",
                                     payload={"name": "Robert Smith", "city": "Boston"}))
    table = pa.table({
        "source": ["crm"],
        "source_pk": ["1"],
        "resolved_entity_id": [eid],
    })
    xw = ResolvedCrosswalk(
        table=table, source="crm", source_pk_column="customer_id",
        n_records=1, n_entities=1, store_path=path,
    )
    return path, eid, xw


def test_entity_360_direct(seeded):
    path, eid, _ = seeded
    page = entity_360(path, eid)
    assert page is not None
    assert page["entity_id"] == eid
    assert page["record_count"] == 1
    assert entity_360(path, "nonexistent") is None


def test_profile_from_crosswalk_drills_through(seeded):
    _, eid, xw = seeded
    # a metric row keyed on source_pk '1' drills straight to the customer view
    page = profile_from_crosswalk(xw, "1")
    assert page is not None
    assert page["entity_id"] == eid
    assert page["record_count"] == 1


def test_profile_from_crosswalk_accepts_int_source_pk(seeded):
    _, eid, xw = seeded
    # source_pk is stored as a string; an int lookup is coerced
    page = profile_from_crosswalk(xw, 1)
    assert page is not None and page["entity_id"] == eid


def test_profile_from_crosswalk_unknown_pk_returns_none(seeded):
    _, _, xw = seeded
    assert profile_from_crosswalk(xw, "999") is None


def test_profile_from_crosswalk_requires_a_store(seeded):
    _, eid, xw = seeded
    # an ephemeral crosswalk (no store_path) has nothing to drill into
    ephemeral = ResolvedCrosswalk(
        table=xw.table, source="crm", source_pk_column="customer_id",
        n_records=1, n_entities=1, store_path=None,
    )
    with pytest.raises(ValueError, match="no identity store"):
        profile_from_crosswalk(ephemeral, "1")
    # ...but an explicit store_path override works
    page = profile_from_crosswalk(ephemeral, "1", store_path=xw.store_path)
    assert page is not None and page["entity_id"] == eid
