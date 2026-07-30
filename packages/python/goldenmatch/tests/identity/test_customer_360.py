"""Customer 360 unified serving read (D1).

Design: ``context-network/architecture/customer-360-data-connection.md``.
Decision: ``context-network/decisions/0049-customer-360-identity-store-spine.md``
(IdentityStore is the spine; the relationship overlay is read from the store's
own ``identity_relationships`` edges).
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest
from goldenmatch.identity import (
    IdentityNode,
    IdentityStore,
    SourceRecord,
    customer_360,
    customer_360_page,
)
from goldenmatch.identity.model import EventKind, IdentityEvent


@pytest.fixture()
def store(tmp_path):
    s = IdentityStore(backend="sqlite", path=str(tmp_path / "id.db"))
    yield s
    s.close()


def _seed(store):
    """One entity, two sources. The golden record's ``email`` comes only from
    crm; ``name`` agrees across both; ``phone`` is a conflict (crm holds the
    golden value, web holds an overridden one). ``web`` is more recent."""
    store.upsert_identity(IdentityNode(
        entity_id="E1", dataset="d", confidence=0.8,
        golden_record={"name": "Ada Lovelace", "email": "ada@calc.io",
                       "phone": "555-0001"},
    ))
    store.emit_event(IdentityEvent(
        entity_id="E1", kind=EventKind.CREATED.value, actor="pipeline",
        run_name="r1", payload={"reason": "initial resolve"},
    ))
    store.upsert_record(SourceRecord(
        "crm:1", "crm", "1", "h1", entity_id="E1", dataset="d",
        payload={"name": "Ada Lovelace", "email": "ada@calc.io",
                 "phone": "555-0001"},
        last_seen_at=datetime(2026, 1, 1),
    ))
    store.upsert_record(SourceRecord(
        "web:9", "web", "9", "h2", entity_id="E1", dataset="d",
        payload={"name": "Ada Lovelace", "phone": "555-9999"},
        last_seen_at=datetime(2026, 6, 1),
    ))


def test_missing_entity_returns_none(store):
    assert customer_360(store, "nope") is None
    assert customer_360_page(store, "nope") is None


def test_composes_full_view(store):
    _seed(store)
    c = customer_360(store, "E1")
    assert c is not None
    assert c.entity_id == "E1"
    assert c.profile.record_count == 2
    assert c.golden_record == {
        "name": "Ada Lovelace", "email": "ada@calc.io", "phone": "555-0001"}
    assert {r["source"] for r in c.source_records} == {"crm", "web"}
    assert len(c.timeline) == 1
    assert c.timeline[0]["kind"] == EventKind.CREATED.value
    assert c.timeline[0]["actor"] == "pipeline"
    assert c.timeline[0]["reason"] == "initial resolve"


def test_field_provenance_agreeing_field(store):
    _seed(store)
    c = customer_360(store, "E1")
    prov = {fp.field: fp for fp in c.field_provenance}
    # name agrees across both sources -> two contributors, no conflict.
    name = prov["name"]
    assert {ctb.source for ctb in name.contributors} == {"crm", "web"}
    assert name.conflicting_values == []


def test_field_provenance_winner_is_most_recent(store):
    _seed(store)
    c = customer_360(store, "E1")
    name = {fp.field: fp for fp in c.field_provenance}["name"]
    # web (2026-06-01) is more recent than crm (2026-01-01), so it wins the
    # cell even though both agree.
    assert name.winning_source == "web"
    assert name.winning_record_id == "web:9"


def test_field_provenance_single_source_field(store):
    _seed(store)
    c = customer_360(store, "E1")
    email = {fp.field: fp for fp in c.field_provenance}["email"]
    # email is only in the crm record.
    assert email.winning_source == "crm"
    assert [ctb.source for ctb in email.contributors] == ["crm"]
    assert email.conflicting_values == []


def test_field_provenance_captures_conflict(store):
    _seed(store)
    c = customer_360(store, "E1")
    phone = {fp.field: fp for fp in c.field_provenance}["phone"]
    # golden phone comes from crm; web's differing value is surfaced, not dropped.
    assert phone.winning_source == "crm"
    assert phone.value == "555-0001"
    assert len(phone.conflicting_values) == 1
    assert phone.conflicting_values[0]["value"] == "555-9999"
    assert phone.conflicting_values[0]["source"] == "web"


def test_relationships_overlay(store):
    _seed(store)
    # A second entity + a relationship edge in the store's own overlay.
    store.upsert_identity(IdentityNode(entity_id="E2", dataset="d"))
    store.reconcile_relationships(
        "d", "shares_address",
        [("E1", "E2", "shares_address", "address", "1 Analytical Way", "d")],
    )
    c = customer_360(store, "E1")
    assert len(c.relationships) == 1
    rel = c.relationships[0]
    assert rel["other_entity_id"] == "E2"
    assert rel["kind"] == "shares_address"

    # ...and it can be skipped.
    c2 = customer_360(store, "E1", include_relationships=False)
    assert c2.relationships == []


def test_golden_none_yields_empty_provenance(store):
    store.upsert_identity(IdentityNode(entity_id="S1", dataset="d"))
    store.upsert_record(SourceRecord(
        "crm:7", "crm", "7", "h7", entity_id="S1", dataset="d",
        payload={"name": "Solo"},
    ))
    c = customer_360(store, "S1")
    assert c.golden_record is None
    assert c.field_provenance == []
    assert len(c.source_records) == 1


def test_timeline_limit(store):
    _seed(store)
    store.emit_event(IdentityEvent(
        entity_id="E1", kind=EventKind.ABSORBED_RECORD.value))
    assert len(customer_360(store, "E1", timeline_limit=1).timeline) == 1
    assert len(customer_360(store, "E1").timeline) == 2


def test_page_is_json_serializable(store):
    _seed(store)
    page = customer_360_page(store, "E1")
    # Round-trips through JSON with no un-serializable objects (datetimes are
    # isoformat strings, dataclasses are dicts).
    dumped = json.dumps(page)
    restored = json.loads(dumped)
    assert restored["entity_id"] == "E1"
    assert restored["golden_record"]["email"] == "ada@calc.io"
    assert restored["field_provenance"][0]["field"] in {"name", "email", "phone"}
    assert restored["timeline"][0]["kind"] == EventKind.CREATED.value
