"""Semantic-graph: entity<->entity relationship edges from shared attributes.

Different entities that share a NON-identity attribute (a clinic phone, an
address) get a relationship edge keyed on their stable entity_ids, emitted in the
same resolve pass, idempotent across runs, and fan-out-capped.
"""
from __future__ import annotations

import json

import polars as pl
import pytest
from goldenmatch.config.schemas import RelationshipRule
from goldenmatch.identity import IdentityStore, resolve_clusters, to_graph_batch


@pytest.fixture()
def store(tmp_path):
    s = IdentityStore(path=str(tmp_path / "identity.db"))
    yield s
    s.close()


def _df(rows):
    out = []
    for i, r in enumerate(rows):
        rec = {"__row_id__": i, "__source__": "src"}
        rec.update(r)
        out.append(rec)
    return pl.DataFrame(out)


def _singletons(n):
    return {i: {"members": [i], "size": 1, "confidence": 1.0, "pair_scores": {}}
            for i in range(n)}


def _resolve(store, df, clusters, rules, run_name="r"):
    return resolve_clusters(
        clusters, df, [], "mk", store, run_name=run_name, source_pk_col="id",
        dataset="d", emit_singletons=True, relationships=rules,
    )


PHONE = [RelationshipRule(field="phone", kind="shares_phone")]


def test_shared_attribute_relates_distinct_entities(store):
    # 1 and 2 are different people sharing phone 555; 3 has its own phone.
    df = _df([{"id": "1", "name": "Al", "phone": "555"},
              {"id": "2", "name": "Bo", "phone": "555"},
              {"id": "3", "name": "Cy", "phone": "999"}])
    s = _resolve(store, df, _singletons(3), PHONE)

    a = store.find_entity_by_record("src:1")
    b = store.find_entity_by_record("src:2")
    c = store.find_entity_by_record("src:3")
    assert a != b != c
    assert s.relationships_added == 1
    assert store.count_relationships() == 1

    rels = store.get_relationships(a)
    assert len(rels) == 1
    assert rels[0]["other_entity_id"] == b
    assert rels[0]["kind"] == "shares_phone"
    assert rels[0]["shared_value"] == "555"
    # symmetric
    assert store.get_relationships(b)[0]["other_entity_id"] == a
    # c is unrelated (unique phone)
    assert store.get_relationships(c) == []


def test_reresolve_is_idempotent(store):
    df = _df([{"id": "1", "name": "Al", "phone": "555"},
              {"id": "2", "name": "Bo", "phone": "555"}])
    _resolve(store, df, _singletons(2), PHONE, run_name="r1")
    assert store.count_relationships() == 1
    s2 = _resolve(store, df, _singletons(2), PHONE, run_name="r2")
    assert store.count_relationships() == 1          # NOT 2
    assert s2.relationships_added in (0, 1)          # attempted, but de-duped in store


def test_same_entity_records_do_not_self_relate(store):
    # two records of the SAME person sharing a phone -> one entity, no self-edge.
    df = _df([{"id": "1", "name": "Al", "phone": "555"},
              {"id": "2", "name": "Al", "phone": "555"}])
    clusters = {0: {"members": [0, 1], "size": 2, "confidence": 1.0,
                    "pair_scores": {(0, 1): 0.99}}}
    _resolve(store, df, clusters, PHONE)
    assert store.count_relationships() == 0


def test_max_fanout_skips_hub_values(store):
    # 3 entities share a switchboard line; max_fanout=2 skips it as a hub.
    df = _df([{"id": "1", "name": "Al", "phone": "555"},
              {"id": "2", "name": "Bo", "phone": "555"},
              {"id": "3", "name": "Cy", "phone": "555"}])
    rules = [RelationshipRule(field="phone", kind="shares_phone", max_fanout=2)]
    _resolve(store, df, _singletons(3), rules)
    assert store.count_relationships() == 0
    # with a higher cap, all 3 pairwise edges appear
    store2_rules = [RelationshipRule(field="phone", kind="shares_phone", max_fanout=10)]
    _resolve(store, df, _singletons(3), store2_rules, run_name="r2")
    assert store.count_relationships() == 3          # 3 choose 2


def test_no_rules_no_relationships(store):
    df = _df([{"id": "1", "name": "Al", "phone": "555"},
              {"id": "2", "name": "Bo", "phone": "555"}])
    s = _resolve(store, df, _singletons(2), None)
    assert s.relationships_added == 0
    assert store.count_relationships() == 0


def test_multiple_rules(store):
    df = _df([{"id": "1", "name": "Al", "phone": "555", "zip": "07001"},
              {"id": "2", "name": "Bo", "phone": "555", "zip": "07001"}])
    rules = [RelationshipRule(field="phone", kind="shares_phone"),
             RelationshipRule(field="zip", kind="same_area")]
    _resolve(store, df, _singletons(2), rules)
    kinds = {r["kind"] for r in store.get_relationships(
        store.find_entity_by_record("src:1"))}
    assert kinds == {"shares_phone", "same_area"}


# ── GoldenGraph export ──────────────────────────────────────────────────────

def test_to_graph_batch_shape(store):
    df = _df([{"id": "1", "name": "Al", "phone": "555"},
              {"id": "2", "name": "Bo", "phone": "555"}])
    _resolve(store, df, _singletons(2), PHONE)
    a = store.find_entity_by_record("src:1")
    b = store.find_entity_by_record("src:2")

    batch = to_graph_batch(store, "d", name_field="name")

    # GoldenGraph StoreBatch shape + JSON-serializable (the append contract).
    assert set(batch) == {"entities", "edges"}
    json.dumps(batch)  # must not raise
    assert len(batch["entities"]) == 2
    assert len(batch["edges"]) == 1

    # each entity carries its stable entity_id as the reconciliation record_key
    by_key = {e["record_keys"][0]: e for e in batch["entities"]}
    assert set(by_key) == {a, b}
    assert {e["canonical_name"] for e in batch["entities"]} == {"Al", "Bo"}
    for e in batch["entities"]:
        assert e["typ"] == "entity"
        assert isinstance(e["local_id"], int)

    # the edge is the (subj_local, predicate, obj_local) triple over local ids
    edge = batch["edges"][0]
    assert edge["predicate"] == "shares_phone"
    locals_ = {e["local_id"]: e["record_keys"][0] for e in batch["entities"]}
    assert {locals_[edge["subj_local"]], locals_[edge["obj_local"]]} == {a, b}
    assert edge["source_refs"] == ["555"]


def test_to_graph_batch_empty(store):
    df = _df([{"id": "1", "name": "Al", "phone": "555"},
              {"id": "2", "name": "Bo", "phone": "999"}])
    _resolve(store, df, _singletons(2), PHONE)  # no shared phone -> no edges
    batch = to_graph_batch(store, "d")
    assert batch == {"entities": [], "edges": []}


def test_to_graph_batch_roundtrips_into_goldengraph(store):
    """If goldengraph is installed, the exported batch appends into its PyStore
    and the edge is queryable -- i.e. it feeds the path-retrieval layer."""
    gg_store = pytest.importorskip("goldengraph.core")
    PyStore = getattr(gg_store, "PyStore", None)
    if PyStore is None:
        pytest.skip("goldengraph PyStore unavailable")
    df = _df([{"id": "1", "name": "Al", "phone": "555"},
              {"id": "2", "name": "Bo", "phone": "555"}])
    _resolve(store, df, _singletons(2), PHONE)
    batch = to_graph_batch(store, "d", name_field="name")

    ps = PyStore()
    ps.append(json.dumps(batch))
    dump = json.loads(ps.query())
    preds = {e["predicate"] for e in dump.get("edges", [])}
    assert "shares_phone" in preds
