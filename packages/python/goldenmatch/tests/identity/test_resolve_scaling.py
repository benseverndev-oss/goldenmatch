"""Scaling guards for the SQLite ``resolve_clusters`` path (issue #2105).

Three independent costs made the SQLite identity resolve unusable at scale:

1. **Unbounded prep.** ``resolve_clusters`` materialized EVERY input row into
   Python dicts (payload + hash + source + pk + record-id candidates) before
   looking at which rows a cluster actually references. At ~2.5 KB of Python
   heap per row that is ~35 GB on a 14M-row frame -- on top of the pipeline's
   own resident set -- even though ``emit_singletons=False`` means only cluster
   members are ever read. That was the reported OOM.
2. **One transaction per statement.** ``bulk_writes()`` was an explicit no-op
   for SQLite, and the connection is opened ``isolation_level=None``, so every
   INSERT committed on its own. Resolve issues ~6 statements per cluster.
3. **A dead per-pair dict.** ``pair_score_by_recpair`` was built over the FULL
   scored-pair set on every resolve and never read.

These tests lock the fixes: the prep must touch only referenced rows, the
SQLite writes must run inside a transaction, and none of it may change what
lands in the store.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.identity import IdentityStore, resolve_clusters


@pytest.fixture()
def store(tmp_path):
    s = IdentityStore(path=str(tmp_path / "identity.db"))
    yield s
    s.close()


def _df(n: int) -> pl.DataFrame:
    """A frame with a natural PK and a couple of nullable payload columns."""
    return pl.DataFrame({
        "__row_id__": list(range(n)),
        "__source__": ["src"] * n,
        "unique_id": [f"u{i}" for i in range(n)],
        "name": [f"name-{i}" if i % 5 else None for i in range(n)],
        "city": ["Springfield" if i % 3 else "Shelbyville" for i in range(n)],
    })


def _cluster(members, score=0.95):
    pair_scores = {
        (min(a, b), max(a, b)): score
        for i, a in enumerate(members) for b in members[i + 1:]
    }
    return {
        "members": list(members),
        "size": len(members),
        "oversized": False,
        "pair_scores": pair_scores,
        "confidence": score,
        "bottleneck_pair": None,
        "cluster_quality": "strong",
    }


def _dump(store: IdentityStore) -> dict:
    """Canonical, entity-id-independent snapshot of everything resolve wrote.

    Entity ids are random UUIDv7 so they differ run to run; key each identity
    by its (deterministic) record-id set instead.
    """
    out = {}
    for node in store.list_identities(limit=10_000):
        recs = store.get_records_for_entity(node.entity_id)
        key = tuple(sorted(r.record_id for r in recs))
        edges = sorted(
            (e.record_a_id, e.record_b_id, e.kind, e.score)
            for e in store.edges_for_entity(node.entity_id)
        )
        events = sorted(
            (ev.kind, str(ev.payload)) for ev in store.history(node.entity_id)
        )
        out[key] = {
            "status": node.status,
            "golden_record": node.golden_record,
            "confidence": node.confidence,
            "payloads": sorted(str(r.payload) for r in recs),
            "edges": edges,
            "events": events,
        }
    return out


# --------------------------------------------------------------------------
# 1. Bounded prep
# --------------------------------------------------------------------------

def test_prep_skips_rows_no_cluster_references(store, monkeypatch):
    """With emit_singletons=False, rows outside a multi-member cluster are
    never materialized. The pre-fix code prepped all 200 rows."""
    import goldenmatch.identity.resolve as R

    seen: list[int] = []
    real = R._record_id_candidates

    def spy(row, source, source_pk_col, **kw):
        seen.append(row["__row_id__"])
        return real(row, source, source_pk_col, **kw)

    monkeypatch.setattr(R, "_record_id_candidates", spy)

    df = _df(200)
    clusters = {1: _cluster([0, 1]), 2: _cluster([2, 3, 4])}
    resolve_clusters(
        clusters, df, [], "mk", store, run_name="r1",
        source_pk_col="unique_id", emit_singletons=False,
    )

    assert sorted(seen) == [0, 1, 2, 3, 4], (
        f"prep touched {len(seen)} rows; only the 5 cluster members are used"
    )


def test_prep_covers_all_rows_when_emitting_singletons(store, monkeypatch):
    """emit_singletons=True genuinely needs every row -- the bound must not
    silently drop them."""
    import goldenmatch.identity.resolve as R

    seen: list[int] = []
    real = R._record_id_candidates

    def spy(row, source, source_pk_col, **kw):
        seen.append(row["__row_id__"])
        return real(row, source, source_pk_col, **kw)

    monkeypatch.setattr(R, "_record_id_candidates", spy)

    df = _df(20)
    clusters = {i + 1: _cluster([i]) for i in range(20)}
    resolve_clusters(
        clusters, df, [], "mk", store, run_name="r1",
        source_pk_col="unique_id", emit_singletons=True,
    )
    assert sorted(seen) == list(range(20))
    assert store.count_identities() == 20


def test_bounded_prep_does_not_change_what_is_stored(tmp_path):
    """The rows a cluster references resolve identically whether or not the
    frame carries unreferenced rows alongside them."""
    clusters = {1: _cluster([0, 1]), 2: _cluster([2, 3, 4])}

    a = IdentityStore(path=str(tmp_path / "a.db"))
    resolve_clusters(
        clusters, _df(5), [], "mk", a, run_name="r1",
        source_pk_col="unique_id", emit_singletons=False,
    )
    dump_a = _dump(a)
    a.close()

    b = IdentityStore(path=str(tmp_path / "b.db"))
    resolve_clusters(
        clusters, _df(500), [], "mk", b, run_name="r1",
        source_pk_col="unique_id", emit_singletons=False,
    )
    dump_b = _dump(b)
    b.close()

    assert dump_a == dump_b
    assert len(dump_a) == 2


def test_absorb_and_merge_still_work_with_bounded_prep(tmp_path):
    """The absorb / merge branches read pre-run identities via the pre-flight
    lookup, which now only covers referenced rows. Exercise both."""
    store = IdentityStore(path=str(tmp_path / "id.db"))
    df = _df(100)

    # Run 1: two separate identities.
    resolve_clusters(
        {1: _cluster([0, 1]), 2: _cluster([2, 3])}, df, [], "mk", store,
        run_name="r1", source_pk_col="unique_id", emit_singletons=False,
    )
    assert store.count_identities() == 2

    # Run 2: absorb row 5 into the first identity.
    summary = resolve_clusters(
        {1: _cluster([0, 1, 5])}, df, [], "mk", store,
        run_name="r2", source_pk_col="unique_id", emit_singletons=False,
    )
    assert summary.absorbed_records == 1

    # Run 3: a cluster spanning both identities -> merge.
    summary = resolve_clusters(
        {1: _cluster([0, 2])}, df, [], "mk", store,
        run_name="r3", source_pk_col="unique_id", emit_singletons=False,
    )
    assert summary.merged == 1
    store.close()


# --------------------------------------------------------------------------
# 2. Batched SQLite writes
# --------------------------------------------------------------------------

def test_sqlite_bulk_writes_opens_a_transaction(store):
    """``bulk_writes()`` used to be a no-op on SQLite, leaving every statement
    to autocommit on its own."""
    assert not store._conn.in_transaction
    with store.bulk_writes():
        store._exec(
            "INSERT INTO identity_nodes (entity_id, status) VALUES (?, ?)",
            ("e1", "active"),
        )
        assert store._conn.in_transaction, (
            "SQLite writes must batch inside one transaction, not autocommit"
        )
    assert not store._conn.in_transaction
    assert store.count_identities() == 1


def test_sqlite_bulk_writes_kill_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_IDENTITY_SQLITE_BATCH", "0")
    s = IdentityStore(path=str(tmp_path / "id.db"))
    with s.bulk_writes():
        s._exec(
            "INSERT INTO identity_nodes (entity_id, status) VALUES (?, ?)",
            ("e1", "active"),
        )
        assert not s._conn.in_transaction
    assert s.count_identities() == 1
    s.close()


def test_sqlite_bulk_writes_rolls_back_and_reraises(store):
    """A failure inside the batch must not leave the connection stuck in an
    open transaction (every later write would silently ride on it)."""
    with pytest.raises(RuntimeError):
        with store.bulk_writes():
            store._exec(
                "INSERT INTO identity_nodes (entity_id, status) VALUES (?, ?)",
                ("e1", "active"),
            )
            raise RuntimeError("boom")
    assert not store._conn.in_transaction
    assert store.count_identities() == 0


def test_reads_inside_batch_see_pending_writes(store):
    """resolve's absorb / merge branches read back rows written earlier in the
    same run. SQLite sees its own uncommitted writes on the same connection --
    lock that, since the batching depends on it."""
    with store.bulk_writes():
        store._exec(
            "INSERT INTO identity_nodes (entity_id, status) VALUES (?, ?)",
            ("e1", "active"),
        )
        assert store.get_identity("e1") is not None


def test_resolve_commits_everything_it_wrote(store):
    """End-to-end: nothing is left uncommitted when resolve returns."""
    df = _df(50)
    clusters = {1: _cluster([0, 1]), 2: _cluster([2, 3])}
    summary = resolve_clusters(
        clusters, df, [], "mk", store, run_name="r1",
        source_pk_col="unique_id", emit_singletons=False,
    )
    assert not store._conn.in_transaction
    assert summary.created == 2

    # A fresh connection to the same file must see the committed rows.
    fresh = IdentityStore(path=store._conn.execute(
        "PRAGMA database_list"
    ).fetchone()[2])
    assert fresh.count_identities() == 2
    fresh.close()


# --------------------------------------------------------------------------
# 4. emit_singletons SQLite ceiling advisory
# --------------------------------------------------------------------------

@pytest.fixture()
def _reset_singleton_warn(monkeypatch):
    """The advisory fires at most once per process; reset the module flag so
    each test observes the first-fire behaviour independently."""
    import goldenmatch.identity.resolve as R

    monkeypatch.setattr(R, "_singleton_ceiling_warned", False)
    return R


def test_singleton_ceiling_warns_once_on_large_sqlite(
    store, caplog, _reset_singleton_warn
):
    """emit_singletons=True on SQLite at/above the ceiling logs one advisory,
    and only once even across repeated resolves."""
    R = _reset_singleton_warn
    import logging

    with caplog.at_level(logging.WARNING, logger="goldenmatch.identity.resolve"):
        R._maybe_warn_singleton_sqlite_ceiling(
            store, R._SINGLETON_SQLITE_ROW_CEILING
        )
        R._maybe_warn_singleton_sqlite_ceiling(
            store, R._SINGLETON_SQLITE_ROW_CEILING * 20
        )

    warnings = [
        rec for rec in caplog.records
        if "emit_singletons=True on the SQLite backend" in rec.message
    ]
    assert len(warnings) == 1, "advisory must fire exactly once per process"
    assert "emit_singletons=false" in warnings[0].message
    assert "backend=postgres" in warnings[0].message


def test_singleton_ceiling_silent_below_threshold(
    store, caplog, _reset_singleton_warn
):
    """Below the ceiling SQLite is the right default -- no advisory."""
    R = _reset_singleton_warn
    import logging

    with caplog.at_level(logging.WARNING, logger="goldenmatch.identity.resolve"):
        R._maybe_warn_singleton_sqlite_ceiling(
            store, R._SINGLETON_SQLITE_ROW_CEILING - 1
        )
    assert not [
        rec for rec in caplog.records
        if "emit_singletons=True on the SQLite backend" in rec.message
    ]


def test_singleton_ceiling_silent_on_non_sqlite(caplog, _reset_singleton_warn):
    """The advisory is SQLite-only -- Postgres has the bulk COPY fast path."""
    R = _reset_singleton_warn
    import logging

    class _FakeStore:
        _backend = "postgres"

    with caplog.at_level(logging.WARNING, logger="goldenmatch.identity.resolve"):
        R._maybe_warn_singleton_sqlite_ceiling(
            _FakeStore(), R._SINGLETON_SQLITE_ROW_CEILING * 100
        )
    assert not [
        rec for rec in caplog.records
        if "emit_singletons=True on the SQLite backend" in rec.message
    ]


def test_singleton_ceiling_advisory_is_downgrade_safe(
    tmp_path, caplog, monkeypatch, _reset_singleton_warn
):
    """End-to-end: with the ceiling lowered, emit_singletons=True over it warns
    but still resolves normally (advisory, never a gate); emit_singletons=False
    over the same size stays silent."""
    R = _reset_singleton_warn
    import logging

    monkeypatch.setattr(R, "_SINGLETON_SQLITE_ROW_CEILING", 3)

    def _warns_for(emit: bool, db_name: str) -> bool:
        s = IdentityStore(path=str(tmp_path / db_name))
        R._singleton_ceiling_warned = False
        caplog.clear()
        with caplog.at_level(
            logging.WARNING, logger="goldenmatch.identity.resolve"
        ):
            resolve_clusters(
                {1: _cluster([0, 1])}, _df(5), [], "mk", s, run_name="r1",
                source_pk_col="unique_id", emit_singletons=emit,
            )
        warned = any(
            "emit_singletons=True on the SQLite backend" in rec.message
            for rec in caplog.records
        )
        # downgrade-safe: the resolve still produced identities either way.
        assert s.count_identities() >= 1
        s.close()
        return warned

    assert _warns_for(True, "a.db") is True
    assert _warns_for(False, "b.db") is False


# --------------------------------------------------------------------------
# 5. SQLite bulk fast-path == per-row path (byte-identical store contents)
# --------------------------------------------------------------------------

def _resolve_into(path, clusters, df, *, bulk: bool, monkeypatch, batch=None):
    """Resolve into a fresh SQLite store with the bulk fast-path on or off."""
    monkeypatch.setenv("GOLDENMATCH_IDENTITY_BULK", "1" if bulk else "0")
    if batch is not None:
        monkeypatch.setenv("GOLDENMATCH_IDENTITY_BULK_FLUSH_ROWS", str(batch))
    s = IdentityStore(path=str(path))
    resolve_clusters(
        clusters, df, [], "mk", s, run_name="r1",
        source_pk_col="unique_id", emit_singletons=True,
    )
    dump = _dump(s)
    s.close()
    return dump


def test_sqlite_bulk_fast_path_matches_row_path(tmp_path, monkeypatch):
    """Brand-new clusters routed through the SQLite staging bulk write must land
    byte-identical store contents to the per-row upsert loop -- same identities,
    golden records, payloads, edges, and event payloads. This is the no-silent-
    regression gate for flipping SQLite onto the bulk fast-path."""
    df = _df(40)
    # A mix of multi-member and singleton clusters, all brand-new (bulk-eligible).
    clusters = {
        1: _cluster([0, 1, 2]),
        2: _cluster([3, 4]),
        3: _cluster([5]),
        4: _cluster([6, 7, 8, 9]),
    }
    clusters.update({i: _cluster([i + 5]) for i in range(5, 35)})

    row = _resolve_into(tmp_path / "row.db", clusters, df, bulk=False,
                        monkeypatch=monkeypatch)
    bulk = _resolve_into(tmp_path / "bulk.db", clusters, df, bulk=True,
                         monkeypatch=monkeypatch)
    assert bulk == row
    assert len(bulk) == len(clusters)


def test_sqlite_bulk_batched_flush_matches_single_flush(tmp_path, monkeypatch):
    """A tiny flush threshold forces many mid-loop batch flushes; the result
    must be identical to one final flush (batching is a memory bound, never a
    correctness change)."""
    df = _df(60)
    clusters = {i: _cluster([i]) for i in range(60)}
    clusters[100] = _cluster([0, 1, 2])  # a multi-member cluster spanning a batch

    one = _resolve_into(tmp_path / "one.db", clusters, df, bulk=True,
                        monkeypatch=monkeypatch, batch=10_000)
    many = _resolve_into(tmp_path / "many.db", clusters, df, bulk=True,
                         monkeypatch=monkeypatch, batch=3)
    assert many == one


def test_sqlite_bulk_carries_event_provenance(tmp_path, monkeypatch):
    """Beyond the _dump surface: the bulk CREATED events must keep actor + trust
    (the audit spine), not just payload -- otherwise SQLite users lose event
    provenance the per-row path records."""
    monkeypatch.setenv("GOLDENMATCH_IDENTITY_BULK", "1")
    s = IdentityStore(path=str(tmp_path / "id.db"))
    resolve_clusters(
        {1: _cluster([0, 1], score=0.9)}, _df(5), [], "mk", s,
        run_name="r1", source_pk_col="unique_id", emit_singletons=False,
        actor="tester",
    )
    node = s.list_identities(limit=10)[0]
    events = s.history(node.entity_id)
    created = [e for e in events if e.kind == "created"]
    assert created, "bulk path must emit a CREATED event"
    assert created[0].actor == "tester"
    assert created[0].trust is not None and abs(created[0].trust - 0.9) < 1e-9
    assert created[0].payload and created[0].payload.get("member_count") == 2
    s.close()


def test_bulk_upsert_identities_works_on_sqlite(tmp_path):
    """SQLite grew a real bulk write path (#2105 follow-up): staging table +
    executemany + INSERT..SELECT..ON CONFLICT, replacing the old
    NotImplementedError. Round-trips golden_record + confidence like Postgres."""
    import json
    from datetime import datetime

    store = IdentityStore(path=str(tmp_path / "id.db"))
    ts = datetime.now()
    golden = {"name": "Alice"}
    df = pl.DataFrame({
        "entity_id": ["e1", "e2"],
        "status": ["active", "active"],
        "merged_into": [None, None],
        "golden_record": [json.dumps(golden), None],
        "confidence": [0.9, None],
        "dataset": ["d", "d"],
        "created_at": [ts, ts],
        "updated_at": [ts, ts],
    })
    store.bulk_upsert_identities(df)
    assert store.count_identities() == 2
    node = store.get_identity("e1")
    assert node is not None
    assert node.golden_record == golden
    assert node.confidence is not None and abs(node.confidence - 0.9) < 1e-9
    store.close()


def test_bulk_methods_raise_on_mongo():
    """The bulk fast-path is SQL-backend only; a non-SQL backend (mongo) still
    routes through the per-row loop, so the bulk entry points reject it loudly
    rather than silently no-op."""
    store = IdentityStore.__new__(IdentityStore)
    store._backend = "mongo"
    df = pl.DataFrame({"entity_id": ["e1"]})
    with pytest.raises(NotImplementedError, match="bulk_upsert_identities"):
        store.bulk_upsert_identities(df)


def test_sqlite_bulk_commits_and_bounds_transaction(tmp_path, monkeypatch):
    """End-to-end via the bulk path: everything is committed on return (a fresh
    connection sees it) and the resolve does not leave an open transaction."""
    monkeypatch.setenv("GOLDENMATCH_IDENTITY_BULK", "1")
    monkeypatch.setenv("GOLDENMATCH_IDENTITY_BULK_FLUSH_ROWS", "5")
    s = IdentityStore(path=str(tmp_path / "id.db"))
    clusters = {i: _cluster([i]) for i in range(30)}
    resolve_clusters(
        clusters, _df(30), [], "mk", s, run_name="r1",
        source_pk_col="unique_id", emit_singletons=True,
    )
    assert not s._conn.in_transaction
    assert s.count_identities() == 30
    fresh = IdentityStore(path=str(tmp_path / "id.db"))
    assert fresh.count_identities() == 30
    fresh.close()
    s.close()


# --------------------------------------------------------------------------
# 3. scored_pairs must not drive cost
# --------------------------------------------------------------------------

def test_scored_pairs_do_not_affect_output(tmp_path):
    """``scored_pairs`` fed only a dict that was never read. Evidence edges come
    from the per-cluster pair_scores / pair_score_view, so passing the full
    scored-pair set must be indistinguishable from passing none."""
    clusters = {1: _cluster([0, 1]), 2: _cluster([2, 3, 4])}

    a = IdentityStore(path=str(tmp_path / "a.db"))
    resolve_clusters(
        clusters, _df(50), [], "mk", a, run_name="r1",
        source_pk_col="unique_id", emit_singletons=False,
    )
    dump_a = _dump(a)
    a.close()

    big_pairs = [(i, j, 0.7) for i in range(50) for j in range(i + 1, 50)]
    b = IdentityStore(path=str(tmp_path / "b.db"))
    resolve_clusters(
        clusters, _df(50), big_pairs, "mk", b, run_name="r1",
        source_pk_col="unique_id", emit_singletons=False,
    )
    dump_b = _dump(b)
    b.close()

    assert dump_a == dump_b
