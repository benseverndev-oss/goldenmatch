"""Semantic-graph: entity<->entity relationship edges from shared attributes.

Different entities that share a NON-identity attribute (a clinic phone, an
address) get a relationship edge keyed on their stable entity_ids, emitted in the
same resolve pass, idempotent across runs, and fan-out-capped.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import RelationshipRule
from goldenmatch.identity import IdentityStore, resolve_clusters


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
