"""resolve_clusters unit tests."""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.identity import (
    EventKind,
    IdentityStatus,
    IdentityStore,
    resolve_clusters,
)


@pytest.fixture()
def store(tmp_path):
    p = str(tmp_path / "identity.db")
    s = IdentityStore(path=p)
    yield s
    s.close()


def _df(rows, with_source=True):
    out = []
    for i, r in enumerate(rows):
        rec = {"__row_id__": i}
        if with_source:
            rec["__source__"] = r.get("__source__", "src")
        for k, v in r.items():
            if k.startswith("__"):
                continue
            rec[k] = v
        out.append(rec)
    return pl.DataFrame(out)


def _cluster(members, score=0.95):
    pair_scores = {}
    for i, a in enumerate(members):
        for b in members[i + 1:]:
            pair_scores[(min(a, b), max(a, b))] = score
    return {
        "members": list(members),
        "size": len(members),
        "oversized": False,
        "pair_scores": pair_scores,
        "confidence": score,
        "cluster_quality": "strong",
    }


def test_resolve_creates_new_identity(store):
    df = _df([
        {"id": "1", "name": "Alice"},
        {"id": "2", "name": "Alyce"},
    ])
    clusters = {0: _cluster([0, 1])}
    pairs = [(0, 1, 0.95)]
    summary = resolve_clusters(
        clusters, df, pairs, "weighted_default", store,
        run_name="run-1", source_pk_col="id",
    )
    assert summary.created == 1
    assert summary.records_upserted == 2
    assert summary.edges_added == 1
    assert store.count_identities() == 1


def test_resolve_singleton_creates_identity(store):
    df = _df([{"id": "1", "name": "Solo"}])
    clusters = {0: {"members": [0], "size": 1, "oversized": False, "pair_scores": {}, "confidence": 1.0}}
    summary = resolve_clusters(clusters, df, [], None, store, run_name="r", source_pk_col="id")
    assert summary.created == 1
    assert store.count_identities() == 1
    assert store.find_entity_by_record("src:1") is not None


def test_resolve_singleton_can_be_skipped(store):
    df = _df([{"id": "1"}])
    clusters = {0: {"members": [0], "size": 1, "oversized": False, "pair_scores": {}}}
    summary = resolve_clusters(
        clusters, df, [], None, store, run_name="r",
        source_pk_col="id", emit_singletons=False,
    )
    assert summary.created == 0
    assert store.count_identities() == 0


def test_resolve_absorb_on_rerun(store):
    df1 = _df([
        {"id": "1", "name": "Alice"},
        {"id": "2", "name": "Alyce"},
    ])
    resolve_clusters(
        {0: _cluster([0, 1])}, df1, [(0, 1, 0.95)],
        "wd", store, run_name="r1", source_pk_col="id",
    )
    eid_before = store.find_entity_by_record("src:1")
    assert eid_before is not None

    # Rerun with an additional record joining the same cluster.
    df2 = _df([
        {"id": "1", "name": "Alice"},
        {"id": "2", "name": "Alyce"},
        {"id": "3", "name": "Alise"},
    ])
    summary = resolve_clusters(
        {0: _cluster([0, 1, 2])}, df2,
        [(0, 1, 0.95), (0, 2, 0.93), (1, 2, 0.92)],
        "wd", store, run_name="r2", source_pk_col="id",
    )
    assert summary.absorbed_records == 1
    assert store.count_identities() == 1
    assert store.find_entity_by_record("src:1") == eid_before
    assert store.find_entity_by_record("src:3") == eid_before


def test_resolve_merge_on_overlap(store):
    # Run 1: two separate clusters / identities.
    df1 = _df([
        {"id": "A", "name": "Alice"},
        {"id": "B", "name": "Bob"},
    ])
    resolve_clusters(
        {0: {"members": [0], "size": 1, "oversized": False, "pair_scores": {}, "confidence": 1.0},
         1: {"members": [1], "size": 1, "oversized": False, "pair_scores": {}, "confidence": 1.0}},
        df1, [], None, store, run_name="r1", source_pk_col="id",
    )
    eid_a = store.find_entity_by_record("src:A")
    eid_b = store.find_entity_by_record("src:B")
    assert eid_a != eid_b

    # Run 2: same two records cluster together -> merge.
    df2 = _df([
        {"id": "A", "name": "Alice"},
        {"id": "B", "name": "Bob"},
    ])
    summary = resolve_clusters(
        {0: _cluster([0, 1])}, df2, [(0, 1, 0.91)],
        "wd", store, run_name="r2", source_pk_col="id",
    )
    assert summary.merged == 1
    # One winner remains active, one loser flipped to merged_into.
    winners = [
        n for n in store.list_identities()
        if n.status == IdentityStatus.ACTIVE.value
    ]
    losers = [
        n for n in store.list_identities()
        if n.status == IdentityStatus.MERGED_INTO.value
    ]
    assert len(winners) == 1
    assert len(losers) == 1
    assert losers[0].merged_into == winners[0].entity_id
    # Both records point at winner now.
    assert store.find_entity_by_record("src:A") == winners[0].entity_id
    assert store.find_entity_by_record("src:B") == winners[0].entity_id


def test_resolve_idempotent_replay(store):
    df = _df([{"id": "1"}, {"id": "2"}])
    pairs = [(0, 1, 0.9)]
    resolve_clusters(
        {0: _cluster([0, 1])}, df, pairs, "wd", store,
        run_name="r1", source_pk_col="id",
    )
    n1 = store.count_identities()
    eid = store.find_entity_by_record("src:1")
    events_before = len(store.history(eid))
    # Replay
    resolve_clusters(
        {0: _cluster([0, 1])}, df, pairs, "wd", store,
        run_name="r1", source_pk_col="id",
    )
    assert store.count_identities() == n1
    assert len(store.history(eid)) == events_before  # CREATED guard prevented duplicate


def test_resolve_no_source_pk_uses_h1_fingerprint(store):
    df = _df([
        {"name": "Alice", "email": "a@x.com"},
        {"name": "Alyce", "email": "a@x.com"},
    ])
    summary = resolve_clusters(
        {0: _cluster([0, 1])}, df, [(0, 1, 0.95)],
        "wd", store, run_name="r1", source_pk_col=None,
    )
    assert summary.created == 1
    # No-PK records now use the canonical fingerprint id "src:h1:..." (default).
    eid = store.list_identities()[0].entity_id
    recs = store.get_records_for_entity(eid)
    assert all(r.record_id.startswith("src:h1:") for r in recs)


def test_resolve_legacy_hash_ids_still_resolve(store, monkeypatch):
    """Migration: records ingested under the legacy 'src:hash:' scheme keep
    resolving to the same entity once the default flips to 'h1' -- no new
    identity, no split, no duplicate record rows."""
    rows = [
        {"name": "Alice", "email": "a@x.com"},
        {"name": "Alyce", "email": "a@x.com"},
    ]
    # Ingest #1 simulates a pre-cutover store (legacy scheme primary).
    monkeypatch.setenv("GOLDENMATCH_IDENTITY_ID_SCHEME", "hash")
    resolve_clusters(
        {0: _cluster([0, 1])}, _df(rows), [(0, 1, 0.95)],
        "wd", store, run_name="r1", source_pk_col=None,
    )
    eid = store.list_identities()[0].entity_id
    legacy_ids = {r.record_id for r in store.get_records_for_entity(eid)}
    assert all(rid.startswith("src:hash:") for rid in legacy_ids)

    # Ingest #2 under the new default (h1): same records must absorb, not split.
    monkeypatch.delenv("GOLDENMATCH_IDENTITY_ID_SCHEME", raising=False)
    summary = resolve_clusters(
        {0: _cluster([0, 1])}, _df(rows), [(0, 1, 0.95)],
        "wd", store, run_name="r2", source_pk_col=None,
    )
    assert store.count_identities() == 1
    assert summary.created == 0
    # Records stay keyed under their existing legacy ids (no duplicate rows).
    assert {r.record_id for r in store.get_records_for_entity(eid)} == legacy_ids


def test_resolve_h1_idempotent_replay(store):
    """No-PK records under the default h1 scheme replay idempotently."""
    rows = [
        {"name": "Alice", "email": "a@x.com"},
        {"name": "Alyce", "email": "a@x.com"},
    ]
    resolve_clusters(
        {0: _cluster([0, 1])}, _df(rows), [(0, 1, 0.95)],
        "wd", store, run_name="r1", source_pk_col=None,
    )
    n1 = store.count_identities()
    resolve_clusters(
        {0: _cluster([0, 1])}, _df(rows), [(0, 1, 0.95)],
        "wd", store, run_name="r1", source_pk_col=None,
    )
    assert store.count_identities() == n1


def test_resolve_id_scheme_killswitch(store, monkeypatch):
    """GOLDENMATCH_IDENTITY_ID_SCHEME=hash forces the legacy primary scheme."""
    monkeypatch.setenv("GOLDENMATCH_IDENTITY_ID_SCHEME", "hash")
    resolve_clusters(
        {0: _cluster([0, 1])},
        _df([
            {"name": "Alice", "email": "a@x.com"},
            {"name": "Alyce", "email": "a@x.com"},
        ]),
        [(0, 1, 0.95)], "wd", store, run_name="r1", source_pk_col=None,
    )
    eid = store.list_identities()[0].entity_id
    recs = store.get_records_for_entity(eid)
    assert all(r.record_id.startswith("src:hash:") for r in recs)


def test_record_id_candidates_coerces_date_to_h1():
    """A date value (polars Date -> datetime.date in to_dicts) must not break
    id derivation: _canonical_payload coerces it (isoformat) so the record
    still gets a canonical h1 id rather than falling back to legacy."""
    import datetime as _dt

    from goldenmatch.identity import resolve as _resolve

    row = {"__row_id__": 0, "__source__": "src", "name": "Alice",
           "dob": _dt.date(1990, 1, 1)}
    primary, candidates = _resolve._record_id_candidates(row, "src", None)
    assert primary.startswith("src:h1:")
    assert primary == candidates[0]
    assert any(c.startswith("src:hash:") for c in candidates)  # legacy still a candidate


def test_resolve_evidence_edges_have_field_scores(store):
    df = _df([{"id": "1", "name": "Alice"}, {"id": "2", "name": "Alyce"}])
    resolve_clusters(
        {0: _cluster([0, 1])}, df, [(0, 1, 0.92)],
        "wd", store, run_name="r1", source_pk_col="id",
        controller_snapshot={"available": False, "source": "test"},
    )
    eid = store.list_identities()[0].entity_id
    edges = store.edges_for_entity(eid)
    assert len(edges) == 1
    assert edges[0].score == pytest.approx(0.95)
    assert edges[0].controller_snapshot == {"available": False, "source": "test"}
    assert edges[0].matchkey_name == "wd"


def test_resolve_event_log_chain(store):
    df = _df([{"id": "1"}, {"id": "2"}])
    resolve_clusters(
        {0: _cluster([0, 1])}, df, [(0, 1, 0.9)],
        "wd", store, run_name="r1", source_pk_col="id",
    )
    eid = store.list_identities()[0].entity_id
    history = store.history(eid)
    kinds = [e.kind for e in history]
    assert kinds[0] == EventKind.CREATED.value
    assert all(e.run_name == "r1" for e in history)
