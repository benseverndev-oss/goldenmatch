"""One fixture through sqlite and snowflake must produce identical objects."""
from __future__ import annotations

from dataclasses import asdict

import pytest

fakesnow = pytest.importorskip("fakesnow")
import snowflake.connector  # noqa: E402


def _seed(store, eid):
    from goldenmatch.identity.model import (
        EvidenceEdge, IdentityAlias, IdentityEvent, IdentityNode, SourceRecord,
    )

    store.upsert_identity(IdentityNode(
        entity_id=eid, dataset="c", status="active", confidence=0.91,
        golden_record={"name": "Ada Lovelace"},
    ))
    for pk in ("1", "2"):
        store.upsert_record(SourceRecord(
            record_id=f"crm:{pk}", source="crm", source_pk=pk,
            record_hash=f"h{pk}", entity_id=eid, dataset="c",
            payload={"email": f"{pk}@example.com"},
        ))
    store.add_edge(EvidenceEdge(
        entity_id=eid, record_a_id="crm:1", record_b_id="crm:2",
        kind="same_as", score=0.97, run_name="run-1", dataset="c",
    ))
    store.emit_event(IdentityEvent(
        entity_id=eid, kind="created", run_name="run-1", dataset="c",
    ))
    store.add_alias(IdentityAlias(alias="MDM-1", entity_id=eid, kind="mdm"))


def _drop_volatile(d: dict) -> dict:
    """Timestamps and generated ids differ between backends by construction.

    ``entry_hash`` is not itself a timestamp, but ``event_content_hash``
    (goldenmatch/identity/audit.py) folds ``recorded_at`` into the hashed
    blob, and each store's ``_seed`` call builds its own ``IdentityEvent()``
    with a fresh ``datetime.now()`` default -- so ``entry_hash`` inherits
    ``recorded_at``'s volatility one level removed. Excluding it here is
    not a parity gap: verified by reading audit.py's ``event_content_hash``
    directly, not guessed from the fixture failing.
    """
    return {
        k: v for k, v in d.items()
        if k not in {
            "created_at", "updated_at", "first_seen_at", "last_seen_at",
            "recorded_at", "edge_id", "event_id", "entry_hash",
        }
    }


def test_sqlite_and_snowflake_produce_identical_objects(tmp_path) -> None:
    from goldenmatch.identity.store import IdentityStore, new_entity_id

    eid = new_entity_id()
    sqlite_store = IdentityStore(
        backend="sqlite", path=str(tmp_path / "identity.db")
    )
    _seed(sqlite_store, eid)

    with fakesnow.patch():
        conn = snowflake.connector.connect(database="GM", schema="PUB")
        sf_store = IdentityStore(
            backend="snowflake", connection=conn, database="GM", schema="PUB",
        )
        _seed(sf_store, eid)

        assert _drop_volatile(asdict(sqlite_store.get_identity(eid))) == \
               _drop_volatile(asdict(sf_store.get_identity(eid)))
        assert _drop_volatile(asdict(sqlite_store.get_record("crm:1"))) == \
               _drop_volatile(asdict(sf_store.get_record("crm:1")))
        a_edges = [_drop_volatile(asdict(e))
                   for e in sqlite_store.edges_for_entity(eid)]
        b_edges = [_drop_volatile(asdict(e))
                   for e in sf_store.edges_for_entity(eid)]
        assert a_edges == b_edges
        a_hist = [_drop_volatile(asdict(e)) for e in sqlite_store.history(eid)]
        b_hist = [_drop_volatile(asdict(e)) for e in sf_store.history(eid)]
        assert a_hist == b_hist
        assert sqlite_store.resolve_alias("MDM-1", kind="mdm") == \
               sf_store.resolve_alias("MDM-1", kind="mdm")
        sf_store.close()
    sqlite_store.close()
