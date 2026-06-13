import json  # noqa: F401  (used by later tasks)

from goldenmatch.identity import IdentityStore
from goldenmatch.identity.migrate_ids import (
    MigrationReport,
    _legacy_match,
    _recompute_h1_id,
    migrate_record_ids,
)
from goldenmatch.identity.model import EvidenceEdge, IdentityNode, SourceRecord
from goldenmatch.identity.resolve import _hash_payload


def test_legacy_match_detects_hash_scheme():
    assert _legacy_match("acme:hash:0123456789ab") == "acme"
    assert _legacy_match("acme:h1:0123456789ab") is None          # already migrated
    assert _legacy_match("acme:CUST-1") is None                   # natural PK
    assert _legacy_match("multi:part:hash:0123456789ab") == "multi:part"  # source may contain ':'


def test_recompute_h1_id_roundtrips_payload():
    payload = {"name": "Ann", "city": "NYC"}
    new_id = _recompute_h1_id("acme", payload)
    assert new_id.startswith("acme:h1:")
    assert len(new_id.split(":")[-1]) == 12


def test_recompute_h1_id_returns_none_when_unfingerprintable(monkeypatch):
    import goldenmatch.identity.migrate_ids as m
    def boom(_):
        raise ValueError("nope")
    monkeypatch.setattr(m, "record_fingerprint", boom)
    assert _recompute_h1_id("acme", {"x": 1}) is None


def test_migration_report_defaults():
    r = MigrationReport()
    assert (r.scanned, r.rewritten, r.merged, r.clashed_distinct_entity,
            r.kept_unfingerprintable, r.edges_repointed, r.dry_run) == (0, 0, 0, 0, 0, 0, False)


def _legacy_id(source: str, payload: dict) -> str:
    return f"{source}:hash:{_hash_payload(payload)[:12]}"


def _seed_legacy_record(store, source, payload, entity_id):
    """Insert an identity node + a :hash:-scheme source record under it."""
    store.upsert_identity(IdentityNode(entity_id=entity_id))
    rid = _legacy_id(source, payload)
    store.upsert_record(SourceRecord(
        record_id=rid, source=source, source_pk=rid[len(source) + 1:],
        record_hash=_hash_payload(payload), entity_id=entity_id, payload=payload,
    ))
    return rid


def test_migrate_renames_hash_to_h1(tmp_path):
    store = IdentityStore(backend="sqlite", path=str(tmp_path / "id.db"))
    pa, pb = {"name": "Ann"}, {"name": "Bob"}
    ra = _seed_legacy_record(store, "acme", pa, "ent-1")
    rb = _seed_legacy_record(store, "acme", pb, "ent-1")
    store.add_edge(EvidenceEdge(entity_id="ent-1", record_a_id=ra, record_b_id=rb,
                                kind="same_as", run_name="r1"))

    rpt = migrate_record_ids(store)

    assert rpt.scanned == 2 and rpt.rewritten == 2 and rpt.merged == 0
    new_a = _recompute_h1_id("acme", pa)
    assert store.find_entity_by_record(new_a) == "ent-1"
    assert store.get_record(ra) is None
    edges = store._fetchall("SELECT record_a_id, record_b_id FROM evidence_edges", ())
    a, b = edges[0]["record_a_id"], edges[0]["record_b_id"]
    assert a.startswith("acme:h1:") and b.startswith("acme:h1:") and a <= b


def test_migrate_is_idempotent(tmp_path):
    store = IdentityStore(backend="sqlite", path=str(tmp_path / "id.db"))
    _seed_legacy_record(store, "acme", {"name": "Ann"}, "ent-1")
    migrate_record_ids(store)
    rpt2 = migrate_record_ids(store)
    assert rpt2.rewritten == 0 and rpt2.merged == 0


def test_clash_same_entity_merges(tmp_path):
    store = IdentityStore(backend="sqlite", path=str(tmp_path / "id.db"))
    payload = {"name": "Ann"}
    legacy = _seed_legacy_record(store, "acme", payload, "ent-1")
    h1 = _recompute_h1_id("acme", payload)
    store.upsert_record(SourceRecord(record_id=h1, source="acme",
        source_pk=h1[len("acme") + 1:], record_hash=_hash_payload(payload),
        entity_id="ent-1", payload=payload))

    rpt = migrate_record_ids(store)
    assert rpt.merged == 1 and rpt.rewritten == 0
    assert store.get_record(legacy) is None
    assert store.find_entity_by_record(h1) == "ent-1"


def test_clash_distinct_entity_is_not_merged(tmp_path):
    store = IdentityStore(backend="sqlite", path=str(tmp_path / "id.db"))
    payload = {"name": "Ann"}
    legacy = _seed_legacy_record(store, "acme", payload, "ent-1")
    h1 = _recompute_h1_id("acme", payload)
    store.upsert_identity(IdentityNode(entity_id="ent-2"))
    store.upsert_record(SourceRecord(record_id=h1, source="acme",
        source_pk=h1[len("acme") + 1:], record_hash=_hash_payload(payload),
        entity_id="ent-2", payload=payload))

    rpt = migrate_record_ids(store)
    assert rpt.clashed_distinct_entity == 1 and rpt.merged == 0 and rpt.rewritten == 0
    assert store.get_record(legacy) is not None
    assert store.find_entity_by_record(h1) == "ent-2"


def test_unfingerprintable_row_kept_legacy(tmp_path, monkeypatch):
    store = IdentityStore(backend="sqlite", path=str(tmp_path / "id.db"))
    rid = _seed_legacy_record(store, "acme", {"name": "Ann"}, "ent-1")
    import goldenmatch.identity.migrate_ids as m
    monkeypatch.setattr(m, "record_fingerprint", lambda _: (_ for _ in ()).throw(ValueError()))
    rpt = migrate_record_ids(store)
    assert rpt.kept_unfingerprintable == 1 and rpt.rewritten == 0
    assert store.get_record(rid) is not None


def test_dry_run_changes_nothing(tmp_path):
    store = IdentityStore(backend="sqlite", path=str(tmp_path / "id.db"))
    rid = _seed_legacy_record(store, "acme", {"name": "Ann"}, "ent-1")
    rpt = migrate_record_ids(store, dry_run=True)
    assert rpt.dry_run and rpt.rewritten == 1
    assert store.get_record(rid) is not None
