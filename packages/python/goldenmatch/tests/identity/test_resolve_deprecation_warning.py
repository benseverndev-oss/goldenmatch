import logging

import goldenmatch.identity.resolve as R
import polars as pl
from goldenmatch.identity import IdentityStore
from goldenmatch.identity.model import IdentityNode, SourceRecord
from goldenmatch.identity.resolve import _hash_payload, resolve_clusters


def _df(rows):
    return pl.DataFrame(rows)


def test_warns_once_when_legacy_candidate_resolves(tmp_path, caplog, monkeypatch):
    monkeypatch.setattr(R, "_LEGACY_SCHEME_WARNED", False)
    store = IdentityStore(backend="sqlite", path=str(tmp_path / "id.db"))
    store.upsert_identity(IdentityNode(entity_id="ent-1"))
    legacy = f"acme:hash:{_hash_payload({'name': 'Ann'})[:12]}"
    store.upsert_record(SourceRecord(record_id=legacy, source="acme",
        source_pk=legacy[5:], record_hash=_hash_payload({'name': 'Ann'}),
        entity_id="ent-1", payload={'name': 'Ann'}))

    df = _df([{"__row_id__": 0, "__source__": "acme", "name": "Ann"}])
    clusters = {1: {"members": [0], "size": 1}}
    with caplog.at_level(logging.WARNING):
        resolve_clusters(df=df, clusters=clusters, store=store,
                         run_name="r1", scored_pairs=[], emit_singletons=True)
    assert sum("migrate-ids" in r.message for r in caplog.records) == 1


def test_silent_when_no_legacy(tmp_path, caplog, monkeypatch):
    monkeypatch.setattr(R, "_LEGACY_SCHEME_WARNED", False)
    store = IdentityStore(backend="sqlite", path=str(tmp_path / "id.db"))
    df = _df([{"__row_id__": 0, "__source__": "acme", "name": "Zoe"}])
    with caplog.at_level(logging.WARNING):
        resolve_clusters(df=df, clusters={1: {"members": [0], "size": 1}},
                         store=store, run_name="r1", scored_pairs=[], emit_singletons=True)
    assert not any("migrate-ids" in r.message for r in caplog.records)
