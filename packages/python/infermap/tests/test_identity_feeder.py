"""Tests for the InferMap -> IdentityGraph bridge."""
from __future__ import annotations

import pytest

# Skip the whole module when goldenmatch isn't available (e.g. infermap-only
# editable install). The cross-package feature requires goldenmatch>=1.15.
goldenmatch = pytest.importorskip("goldenmatch", reason="bridge needs goldenmatch>=1.15")
from goldenmatch.identity import (  # noqa: E402
    IdentityNode,
    IdentityStore,
    SourceRecord,
    new_entity_id,
)
from infermap.identity import write_aliases_from_mapping  # noqa: E402
from infermap.types import FieldMapping, MapResult, ScorerResult  # noqa: E402


def _mapping(*pairs, confidence=0.95):
    return MapResult(
        mappings=[
            FieldMapping(
                source=src, target=tgt, confidence=confidence,
                breakdown={"test": ScorerResult(score=confidence, reasoning="t")},
                reasoning="test fixture",
            )
            for src, tgt in pairs
        ],
    )


@pytest.fixture()
def seeded_store(tmp_path):
    db = str(tmp_path / "id.db")
    eid_alice = new_entity_id()
    eid_bob = new_entity_id()
    with IdentityStore(path=db) as s:
        s.upsert_identity(IdentityNode(entity_id=eid_alice, dataset="t"))
        s.upsert_identity(IdentityNode(entity_id=eid_bob, dataset="t"))
        s.upsert_record(SourceRecord("crm:1", "crm", "1", "h1",
                                     entity_id=eid_alice, dataset="t"))
        s.upsert_record(SourceRecord("crm:2", "crm", "2", "h2",
                                     entity_id=eid_bob, dataset="t"))
    return db, eid_alice, eid_bob


def _record_entity_resolver(store):
    def _r(record):
        rid = f"crm:{record['cust_id']}"
        return store.find_entity_by_record(rid)
    return _r


def test_writes_alias_per_record_per_kind(seeded_store):
    db, eid_alice, eid_bob = seeded_store
    records = [
        {"cust_id": "1", "email_addr": "alice@x.com"},
        {"cust_id": "2", "email_addr": "bob@y.com"},
    ]
    mapping = _mapping(("cust_id", "customer_id"), ("email_addr", "email"))
    with IdentityStore(path=db) as store:
        result = write_aliases_from_mapping(
            mapping, records, store, _record_entity_resolver(store),
            source_name="crm", dataset="t",
        )
    assert result.aliases_written == 4
    assert result.records_processed == 2
    assert result.mappings_used == 2
    # Resolve via the store
    with IdentityStore(path=db) as store:
        assert store.resolve_alias("crm:1", kind="customer_id") == eid_alice
        assert store.resolve_alias("crm:bob@y.com", kind="email") == eid_bob


def test_low_confidence_dropped(seeded_store):
    db, _, _ = seeded_store
    records = [{"cust_id": "1", "email_addr": "alice@x.com"}]
    mapping = _mapping(
        ("cust_id", "customer_id"),
        ("email_addr", "email"),
        confidence=0.50,
    )
    with IdentityStore(path=db) as store:
        result = write_aliases_from_mapping(
            mapping, records, store, _record_entity_resolver(store),
            source_name="crm", dataset="t",
        )
    assert result.aliases_written == 0
    assert result.mappings_used == 0
    assert result.skipped_low_confidence == 2


def test_non_alias_kind_target_skipped(seeded_store):
    """Mapping to a non-alias target column (e.g. `address`) shouldn't
    create alias rows -- only ID-shaped targets do."""
    db, _, _ = seeded_store
    records = [{"cust_id": "1", "street": "123 Main"}]
    mapping = _mapping(("cust_id", "customer_id"), ("street", "address"))
    with IdentityStore(path=db) as store:
        result = write_aliases_from_mapping(
            mapping, records, store, _record_entity_resolver(store),
            source_name="crm",
        )
    assert result.aliases_written == 1  # only customer_id
    assert result.mappings_used == 1


def test_missing_value_skipped(seeded_store):
    db, _, _ = seeded_store
    records = [
        {"cust_id": "1", "email_addr": "alice@x.com"},
        {"cust_id": "2"},  # no email_addr
    ]
    mapping = _mapping(("cust_id", "customer_id"), ("email_addr", "email"))
    with IdentityStore(path=db) as store:
        result = write_aliases_from_mapping(
            mapping, records, store, _record_entity_resolver(store),
            source_name="crm",
        )
    assert result.aliases_written == 3   # alice has both, bob has only cust_id
    assert result.skipped_no_value == 1


def test_missing_entity_skipped(seeded_store):
    """Records whose entity_id_resolver returns None are skipped, not failed."""
    db, _, _ = seeded_store
    records = [
        {"cust_id": "1", "email_addr": "alice@x.com"},
        {"cust_id": "999", "email_addr": "ghost@unknown.com"},  # no identity
    ]
    mapping = _mapping(("cust_id", "customer_id"), ("email_addr", "email"))
    with IdentityStore(path=db) as store:
        result = write_aliases_from_mapping(
            mapping, records, store, _record_entity_resolver(store),
            source_name="crm",
        )
    assert result.records_processed == 2
    assert result.aliases_written == 2  # only alice's
    assert result.skipped_no_entity == 1


def test_custom_alias_kinds(seeded_store):
    """Callers can pass domain-specific alias kinds (e.g. healthcare NPI)."""
    db, eid_alice, _ = seeded_store
    records = [{"cust_id": "1", "provider_npi": "1234567890"}]
    mapping = _mapping(
        ("cust_id", "customer_id"),
        ("provider_npi", "npi"),
    )
    with IdentityStore(path=db) as store:
        result = write_aliases_from_mapping(
            mapping, records, store, _record_entity_resolver(store),
            source_name="crm",
            alias_kinds=frozenset({"customer_id", "npi"}),
        )
    assert result.aliases_written == 2
    with IdentityStore(path=db) as store:
        assert store.resolve_alias("crm:1234567890", kind="npi") == eid_alice


def test_no_goldenmatch_raises_clean_error(monkeypatch):
    """When goldenmatch isn't importable, we raise a clear ImportError."""
    import builtins as _builtins
    real = _builtins.__import__

    def fake(name, *args, **kwargs):
        if name == "goldenmatch.identity":
            raise ImportError("simulated: goldenmatch not installed")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(_builtins, "__import__", fake)
    with pytest.raises(ImportError, match="goldenmatch>=1.15.0"):
        write_aliases_from_mapping(
            _mapping(("cust_id", "customer_id")),
            [{"cust_id": "1"}],
            store=object(),
            entity_id_resolver=lambda r: "fake-id",
            source_name="crm",
        )
