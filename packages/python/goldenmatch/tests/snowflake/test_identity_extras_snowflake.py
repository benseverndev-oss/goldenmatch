"""Relationships, seals, block keys, runs and stats on the Snowflake backend.

Shapes here are pinned to ``goldenmatch/identity/store.py``, not to the task
brief: ``add_relationships`` takes SIX-tuples, ``sample_records`` returns
``(entity_id, payload)``, and the block-key index columns are
``(record_id, pass_sig, block_key)``. See the task-6 report for the full list of
brief-vs-source discrepancies.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

fakesnow = pytest.importorskip("fakesnow")

from tests.snowflake.test_identity_store_snowflake import store  # noqa: F401,E402


def _node(store, dataset="c", status="active"):  # noqa: F811
    from goldenmatch.identity.model import IdentityNode
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    store.upsert_identity(
        IdentityNode(entity_id=eid, dataset=dataset, status=status)
    )
    return eid


def _record(store, rid, eid, payload, *, source="crm", dataset="c"):  # noqa: F811
    from goldenmatch.identity.model import SourceRecord

    store.upsert_record(SourceRecord(
        record_id=rid, source=source, source_pk=rid, record_hash=rid,
        entity_id=eid, payload=payload, dataset=dataset,
    ))


# ----- persisted block-key index --------------------------------------------

def test_index_and_query_block_keys(store) -> None:  # noqa: F811
    eid = _node(store)
    store.index_record_block_keys("crm:1", eid, [("soundex_last", "S530")])
    assert store.candidates_by_block_keys([("soundex_last", "S530")]) == {"crm:1"}
    assert store.candidates_by_block_keys([("soundex_last", "X999")]) == set()
    assert store.candidates_by_block_keys([]) == set()


def test_index_record_block_keys_is_idempotent(store) -> None:  # noqa: F811
    eid = _node(store)
    for _ in range(2):
        store.index_record_block_keys("crm:1", eid, [("soundex_last", "S530")])
    assert store.candidates_by_block_keys([("soundex_last", "S530")]) == {"crm:1"}


def test_index_record_block_keys_repoints_entity(store) -> None:  # noqa: F811
    a, b = _node(store), _node(store)
    store.index_record_block_keys("crm:1", a, [("p", "K")])
    store.index_record_block_keys("crm:1", b, [("p", "K")])
    from goldenmatch.snowflake._store_sql import fetchall_rows

    rows = fetchall_rows(
        store._sf._conn,
        "SELECT record_id, entity_id FROM identity_record_block_keys",
    )
    assert len(rows) == 1
    assert rows[0]["entity_id"] == b


def test_candidates_by_block_keys_unions_passes(store) -> None:  # noqa: F811
    eid = _node(store)
    store.index_record_block_keys("crm:1", eid, [("p1", "A"), ("p2", "B")])
    store.index_record_block_keys("crm:2", eid, [("p2", "B")])
    assert store.candidates_by_block_keys([("p1", "A")]) == {"crm:1"}
    assert store.candidates_by_block_keys(
        [("p1", "A"), ("p2", "B")]
    ) == {"crm:1", "crm:2"}


# ----- stats ----------------------------------------------------------------

def test_status_counts(store) -> None:  # noqa: F811
    _node(store)
    _node(store)
    _node(store, status="merged_into")
    _node(store, dataset="other")
    assert store.status_counts(dataset="c") == {"active": 2, "merged_into": 1}
    assert store.status_counts()["active"] == 3


def test_active_record_stats(store) -> None:  # noqa: F811
    a, b = _node(store), _node(store)
    retired = _node(store, status="merged_into")
    _record(store, "crm:1", a, {"x": 1})
    _record(store, "crm:2", a, {"x": 2})
    _record(store, "erp:1", b, {"x": 3}, source="erp")
    _record(store, "crm:9", retired, {"x": 4})
    per_entity, per_source = store.active_record_stats(dataset="c")
    assert per_entity == {a: 2, b: 1}
    assert per_source == {"crm": 2, "erp": 1}


def test_active_record_stats_counts_zero_record_entities(store) -> None:  # noqa: F811
    a = _node(store)
    per_entity, per_source = store.active_record_stats(dataset="c")
    assert per_entity == {a: 0}
    assert per_source == {}


# ----- deterministic merge --------------------------------------------------

def test_merge_by_shared_field(store) -> None:  # noqa: F811
    a, b, c = _node(store), _node(store), _node(store)
    _record(store, "crm:1", a, {"npi": "123", "last_name": "Smith"})
    _record(store, "crm:2", b, {"npi": "123", "last_name": "Smith"})
    _record(store, "crm:3", c, {"npi": "999", "last_name": "Jones"})
    merged, groups = store.merge_by_shared_field("c", "npi")
    assert (merged, groups) == (1, 1)
    survivor = min(a, b)
    absorbed = max(a, b)
    assert store.find_entity_by_record("crm:1") == survivor
    assert store.find_entity_by_record("crm:2") == survivor
    assert store.get_identity(absorbed).status == "merged_into"
    assert store.get_identity(absorbed).merged_into == survivor
    # Idempotent: a second pass has nothing left to collapse.
    assert store.merge_by_shared_field("c", "npi") == (0, 0)


def test_merge_by_shared_field_composite_is_guarded(store) -> None:  # noqa: F811
    a, b = _node(store), _node(store)
    _record(store, "crm:1", a, {"npi": "123", "last_name": "Smith"})
    _record(store, "crm:2", b, {"npi": "123", "last_name": "Jones"})
    # Same npi, different surname -> the guarded merge refuses.
    assert store.merge_by_shared_field("c", ["npi", "last_name"]) == (0, 0)
    assert store.find_entity_by_record("crm:2") == b


def test_merge_by_shared_field_skips_oversized_groups(store) -> None:  # noqa: F811
    for i in range(3):
        _record(store, f"crm:{i}", _node(store), {"npi": "0000000000"})
    assert store.merge_by_shared_field("c", "npi", max_group=2) == (0, 0)


# ----- relationship grouping ------------------------------------------------

def test_relationship_groups(store) -> None:  # noqa: F811
    a, b, c = _node(store), _node(store), _node(store)
    _record(store, "crm:1", a, {"phone": "555", "email": "x@acme.com"})
    _record(store, "crm:2", b, {"phone": "555", "email": "y@acme.com"})
    _record(store, "crm:3", c, {"phone": "999", "email": "z@beta.com"})
    groups = store.relationship_groups("phone", "c", 2, 10)
    assert len(groups) == 1
    value, eids = groups[0]
    assert value == "555"
    assert sorted(eids) == sorted([a, b])


def test_relationship_groups_transform_email_domain(store) -> None:  # noqa: F811
    a, b, c = _node(store), _node(store), _node(store)
    _record(store, "crm:1", a, {"email": "x@acme.com"})
    _record(store, "crm:2", b, {"email": "y@ACME.com"})
    _record(store, "crm:3", c, {"email": "no-at-sign"})
    groups = store.relationship_groups(
        "email", "c", 2, 10, transform="email_domain"
    )
    assert [g[0] for g in groups] == ["acme.com"]
    assert sorted(groups[0][1]) == sorted([a, b])


def test_relationship_groups_transform_normalize_company(store) -> None:  # noqa: F811
    a, b = _node(store), _node(store)
    _record(store, "crm:1", a, {"co": "Acme, Inc."})
    _record(store, "crm:2", b, {"co": "acme llc"})
    groups = store.relationship_groups(
        "co", "c", 2, 10, transform="normalize_company"
    )
    assert [g[0] for g in groups] == ["acme"]


def test_relationship_groups_rejects_unknown_transform(store) -> None:  # noqa: F811
    with pytest.raises(ValueError):
        store.relationship_groups("phone", "c", 2, 10, transform="nope")


def test_relationship_groups_rejects_unsafe_field(store) -> None:  # noqa: F811
    with pytest.raises(ValueError):
        store.relationship_groups("a'; DROP TABLE x --", "c", 2, 10)


def test_relationship_field_stats(store) -> None:  # noqa: F811
    a, b, c, d = (_node(store) for _ in range(4))
    _record(store, "crm:1", a, {"phone": "555"})
    _record(store, "crm:2", b, {"phone": "555"})
    _record(store, "crm:3", c, {"state": "CA", "phone": "hub"})
    _record(store, "crm:4", d, {"state": "CA", "phone": "hub"})
    stats = store.relationship_field_stats("phone", "c", 2, 2)
    assert stats["sweet_values"] == 2
    assert stats["hub_values"] == 0
    assert stats["coverage_entities"] == 4
    assert stats["sweet_pairs"] == 2
    assert stats["sweet_pair_n"] == 4
    hubbed = store.relationship_field_stats("phone", "c", 3, 3)
    assert hubbed["sweet_values"] == 0
    assert hubbed["coverage_entities"] == 0


def test_sample_records(store) -> None:  # noqa: F811
    a = _node(store)
    _record(store, "crm:1", a, {"name": "Ada"})
    _record(store, "crm:2", a, {"name": "Bo"})
    sample = store.sample_records("c", 5)
    assert len(sample) == 2
    assert {eid for eid, _ in sample} == {a}
    assert sorted(p["name"] for _, p in sample) == ["Ada", "Bo"]
    assert len(store.sample_records("c", 1)) == 1


# ----- relationship edges ---------------------------------------------------

def test_relationships_round_trip(store) -> None:  # noqa: F811
    a, b = _node(store), _node(store)
    assert store.add_relationships(
        [(a, b, "shares_phone", "phone", "555", "c")]
    ) == 1
    assert store.count_relationships() == 1
    rels = store.get_relationships(a)
    assert len(rels) == 1
    assert rels[0] == {
        "other_entity_id": b, "kind": "shares_phone",
        "field": "phone", "shared_value": "555",
    }
    assert store.get_relationships(b)[0]["other_entity_id"] == a
    listed = store.list_relationships("c")
    assert listed == [{
        "entity_a_id": min(a, b), "entity_b_id": max(a, b),
        "kind": "shares_phone", "field": "phone", "shared_value": "555",
    }]
    assert store.list_relationships("nope") == []


def test_add_relationships_is_idempotent_and_canonicalizes(store) -> None:  # noqa: F811
    a, b = _node(store), _node(store)
    hi, lo = max(a, b), min(a, b)
    store.add_relationships([(hi, lo, "shares_phone", "phone", "555", "c")])
    store.add_relationships([(lo, hi, "shares_phone", "phone", "555", "c")])
    assert store.count_relationships() == 1
    assert store.list_relationships()[0]["entity_a_id"] == lo


def test_add_relationships_drops_self_edges_and_empty(store) -> None:  # noqa: F811
    a = _node(store)
    assert store.add_relationships([]) == 0
    assert store.add_relationships([(a, a, "k", "f", "v", "c")]) == 0
    assert store.count_relationships() == 0


def test_reconcile_relationships(store) -> None:  # noqa: F811
    a, b, c = _node(store), _node(store), _node(store)
    desired = [
        (a, b, "shares_phone", "phone", "555", "c"),
        (b, c, "shares_phone", "phone", "999", "c"),
    ]
    assert store.reconcile_relationships("c", "shares_phone", desired) == (2, 0, 0)
    # Same data twice -> no churn.
    assert store.reconcile_relationships("c", "shares_phone", desired) == (0, 0, 2)
    # Drop one, add one.
    next_desired = [
        (a, b, "shares_phone", "phone", "555", "c"),
        (a, c, "shares_phone", "phone", "777", "c"),
    ]
    assert store.reconcile_relationships(
        "c", "shares_phone", next_desired
    ) == (1, 1, 1)
    assert store.count_relationships() == 2
    values = {r["shared_value"] for r in store.list_relationships("c")}
    assert values == {"555", "777"}


def test_reconcile_relationships_is_scoped_by_kind(store) -> None:  # noqa: F811
    a, b = _node(store), _node(store)
    store.add_relationships([(a, b, "shares_email", "email", "x@y", "c")])
    store.reconcile_relationships(
        "c", "shares_phone", [(a, b, "shares_phone", "phone", "555", "c")]
    )
    # Reconciling to empty removes only the shares_phone edge.
    assert store.reconcile_relationships("c", "shares_phone", []) == (0, 1, 0)
    assert store.count_relationships() == 1
    assert store.list_relationships()[0]["kind"] == "shares_email"


# ----- run metadata ---------------------------------------------------------

def test_record_run_and_run_config(store) -> None:  # noqa: F811
    store.record_run(
        "run-1", config_id="cfg1", schema_version=7,
        config_json='{"a": 1}', dataset="c",
    )
    cfg = store.run_config("run-1")
    assert cfg is not None
    assert cfg["run_name"] == "run-1"
    assert cfg["config_id"] == "cfg1"
    assert int(cfg["schema_version"]) == 7
    assert cfg["config_json"] == '{"a": 1}'
    assert cfg["dataset"] == "c"
    assert cfg["created_at"] is not None
    assert store.run_config("missing") is None


def test_record_run_first_writer_wins(store) -> None:  # noqa: F811
    store.record_run("run-1", config_id="cfg1")
    store.record_run("run-1", config_id="cfg2")
    assert store.run_config("run-1")["config_id"] == "cfg1"


def test_record_run_ignores_empty_run_name(store) -> None:  # noqa: F811
    store.record_run("")
    assert store.run_config("") is None


# ----- audit log export -----------------------------------------------------

def test_export_audit_log(store) -> None:  # noqa: F811
    from goldenmatch.identity.model import IdentityEvent

    eid = _node(store)
    for kind, actor in (("created", "sys"), ("merged", "ben")):
        store.emit_event(IdentityEvent(
            entity_id=eid, kind=kind, actor=actor, dataset="c",
            payload={"reason": kind},
        ))
    store.emit_event(IdentityEvent(
        entity_id=eid, kind="noise", actor="sys", dataset="other",
    ))
    all_c = store.export_audit_log(dataset="c")
    assert [e.kind for e in all_c] == ["created", "merged"]
    assert [e.kind for e in store.export_audit_log(actor="ben")] == ["merged"]
    assert len(store.export_audit_log()) == 3
    future = datetime.now() + timedelta(days=1)
    assert store.export_audit_log(since=future) == []
    past = datetime.now() - timedelta(days=1)
    assert len(store.export_audit_log(since=past)) == 3


# ----- audit seals ----------------------------------------------------------

def test_add_and_latest_seal(store) -> None:  # noqa: F811
    from goldenmatch.identity.model import AuditSeal

    seal_id = store.add_seal(AuditSeal(
        root_hash="abc123", event_count=2, last_event_id=2, dataset="c",
        actor="ben",
    ))
    assert isinstance(seal_id, int)
    latest = store.latest_seal(dataset="c")
    assert latest is not None
    assert latest.root_hash == "abc123"
    assert latest.event_count == 2
    assert latest.last_event_id == 2
    assert latest.actor == "ben"
    assert latest.seal_id == seal_id
    assert len(store.list_seals(dataset="c")) == 1


def test_seal_chain_is_append_only_and_dataset_scoped(store) -> None:  # noqa: F811
    from goldenmatch.identity.model import AuditSeal

    first = store.add_seal(AuditSeal(root_hash="r1", event_count=1, dataset="c"))
    second = store.add_seal(AuditSeal(
        root_hash="r2", event_count=2, dataset="c",
        prev_seal_id=first, prev_root="r1",
    ))
    assert second > first
    # Identical seal twice -> two rows (append-only log, never a MERGE).
    store.add_seal(AuditSeal(root_hash="r2", event_count=2, dataset="c",
                             prev_seal_id=first, prev_root="r1"))
    assert len(store.list_seals(dataset="c")) == 3
    assert [s.root_hash for s in store.list_seals(dataset="c")] == ["r1", "r2", "r2"]
    assert store.latest_seal(dataset="c").prev_root == "r1"
    # dataset=None is its own (global) chain, not "all datasets".
    assert store.latest_seal() is None
    assert store.list_seals() == []
    store.add_seal(AuditSeal(root_hash="g1", event_count=9))
    assert store.latest_seal().root_hash == "g1"
    assert len(store.list_seals()) == 1


def test_reconcile_keeps_stale_null_shared_value_edge(store) -> None:  # noqa: F811
    """A NULL-shared_value edge SURVIVES a reconcile that does not name it.

    Parity pin against store.py:2271-2273, which binds ``shared_value = ?``:
    on SQLite/Postgres ``NULL = NULL`` is never true, so the DELETE misses the
    row even though the returned ``deleted`` count claims it. A NULL-safe
    comparison here would make Snowflake the only backend that actually
    removes it. Reachable state -- a relationship transform with no match
    yields NULL (_store_sql.py:249-255).
    """
    a, b = _node(store), _node(store)
    store.add_relationships([(a, b, "shares_x", "co", None, "c")])
    assert store.count_relationships() == 1
    # Reconciling to empty reports the delete...
    assert store.reconcile_relationships("c", "shares_x", []) == (0, 1, 0)
    # ...but the row survives, exactly as it does on SQLite.
    assert store.count_relationships() == 1
    assert store.list_relationships()[0]["shared_value"] is None


def test_reconcile_still_deletes_non_null_shared_value_edges(store) -> None:  # noqa: F811
    """The `=` predicate must not over-correct: a real value still deletes."""
    a, b = _node(store), _node(store)
    store.add_relationships([(a, b, "shares_x", "co", "acme", "c")])
    assert store.reconcile_relationships("c", "shares_x", []) == (0, 1, 0)
    assert store.count_relationships() == 0


def test_index_record_block_keys_batches_and_dedupes(store) -> None:  # noqa: F811
    """A repeated (pass_sig, block_key) in one call is not a duplicate row.

    The batched MERGE has a WHEN MATCHED branch, so two source rows hitting one
    target row is a non-deterministic MERGE. The pre-stage dedupe is what stops
    it -- and the index must still hold exactly one row per key.
    """
    from goldenmatch.snowflake._store_sql import fetchall_rows

    eid = _node(store)
    store.index_record_block_keys(
        "crm:1", eid,
        [("p1", "A"), ("p1", "A"), ("p2", "B"), ("p1", "A")],
    )
    rows = fetchall_rows(
        store._sf._conn,
        "SELECT record_id, entity_id, pass_sig, block_key "
        "FROM identity_record_block_keys ORDER BY pass_sig",
    )
    assert [(r["pass_sig"], r["block_key"]) for r in rows] == [("p1", "A"), ("p2", "B")]
    assert {r["entity_id"] for r in rows} == {eid}
    assert store.candidates_by_block_keys([("p1", "A")]) == {"crm:1"}


def test_index_record_block_keys_accepts_null_entity_id(store) -> None:  # noqa: F811
    """entity_id is nullable -- an unresolved record can still be indexed."""
    from goldenmatch.snowflake._store_sql import fetchall_rows

    store.index_record_block_keys("crm:1", None, [("p1", "A")])
    rows = fetchall_rows(
        store._sf._conn,
        "SELECT entity_id FROM identity_record_block_keys",
    )
    assert len(rows) == 1
    assert rows[0]["entity_id"] is None
    assert store.candidates_by_block_keys([("p1", "A")]) == {"crm:1"}
