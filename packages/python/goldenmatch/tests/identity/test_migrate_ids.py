from goldenmatch.identity.migrate_ids import (
    MigrationReport,
    _legacy_match,
    _recompute_h1_id,
)


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
