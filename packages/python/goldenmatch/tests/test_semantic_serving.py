"""Tests for the semantic-layer <-> Customer 360 drill-through (semantic/serving.py)."""
from __future__ import annotations

import pyarrow as pa
import pytest
from goldenmatch.identity import IdentityNode, IdentityStore, SourceRecord, new_entity_id
from goldenmatch.semantic import (
    ServingJoinCertificate,
    certify_serving_joins,
    entity_360,
    profile_from_crosswalk,
)
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


# --- certify_serving_joins (PR-C) ---------------------------------------------


def test_certify_serving_joins_clean(tmp_path):
    # 3 distinct source records across 2 entities -> unique record_id -> trustworthy
    path = str(tmp_path / "id.db")
    e1, e2 = new_entity_id(), new_entity_id()
    with IdentityStore(path=path) as s:
        s.upsert_identity(IdentityNode(entity_id=e1, dataset="crm", confidence=0.9))
        s.upsert_identity(IdentityNode(entity_id=e2, dataset="crm", confidence=0.9))
        s.upsert_record(SourceRecord("crm:1", "crm", "1", "h1", entity_id=e1, dataset="crm"))
        s.upsert_record(SourceRecord("crm:2", "crm", "2", "h2", entity_id=e1, dataset="crm"))
        s.upsert_record(SourceRecord("crm:3", "crm", "3", "h3", entity_id=e2, dataset="crm"))
        cert = certify_serving_joins(s, dataset="crm")
    assert isinstance(cert, ServingJoinCertificate)
    assert cert.n_entities == 2
    assert cert.n_records == 3
    assert cert.is_trustworthy is True
    assert cert.record_certificate.duplicate_key_groups == 0


def test_certify_serving_joins_empty_store_is_trustworthy(tmp_path):
    path = str(tmp_path / "id.db")
    with IdentityStore(path=path) as s:
        cert = certify_serving_joins(s)
    assert cert.n_entities == 0
    assert cert.n_records == 0
    assert cert.is_trustworthy is True


def test_certify_serving_joins_respects_max_entities(tmp_path):
    path = str(tmp_path / "id.db")
    with IdentityStore(path=path) as s:
        for i in range(5):
            eid = new_entity_id()
            s.upsert_identity(IdentityNode(entity_id=eid, dataset="crm", confidence=0.9))
            s.upsert_record(SourceRecord(f"crm:{i}", "crm", str(i), f"h{i}",
                                         entity_id=eid, dataset="crm"))
        cert = certify_serving_joins(s, dataset="crm", page_size=2, max_entities=3)
    assert cert.n_entities == 3          # capped
    assert cert.truncated is True
