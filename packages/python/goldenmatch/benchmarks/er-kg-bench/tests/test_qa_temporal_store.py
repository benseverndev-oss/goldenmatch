"""goldengraph store.as_of over the bi-temporal store. Needs the native wheel ->
skips locally, validates in the gate lane. The valid_to round-trip is FIRST: it
pins the previously-unexercised Python->JSON->store valid-time path."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("goldengraph_native")


def test_valid_to_round_trips_through_pystore_append_and_as_of():
    from goldengraph_native import _native as ggn

    store = ggn.PyStore()
    # X(0) -rel-> A(1) valid [1,5); X(0) -rel-> B(2) valid [5,inf). ONE batch.
    batch = {
        "entities": [
            {"local_id": 0, "canonical_name": "X", "typ": "c", "surface_names": ["X"], "record_keys": ["x"]},
            {"local_id": 1, "canonical_name": "A", "typ": "c", "surface_names": ["A"], "record_keys": ["a"]},
            {"local_id": 2, "canonical_name": "B", "typ": "c", "surface_names": ["B"], "record_keys": ["b"]},
        ],
        "edges": [
            {"subj_local": 0, "predicate": "rel", "obj_local": 1, "valid_from": 1, "valid_to": 5, "source_refs": []},
            {"subj_local": 0, "predicate": "rel", "obj_local": 2, "valid_from": 5, "valid_to": None, "source_refs": []},
        ],
        "ingested_at": 1,
    }
    store.append(json.dumps(batch))
    BIG = 10**12
    past_names = {e["canonical_name"] for e in store.as_of(3, BIG).entities()}
    cur_names = {e["canonical_name"] for e in store.as_of(7, BIG).entities()}
    assert "A" in past_names and "B" not in past_names   # D=3 -> only A's window
    assert "B" in cur_names and "A" not in cur_names      # D=7 -> only B's window


def test_goldengraph_asof_returns_the_gold_object_in_both_regimes():
    from erkgbench.qa_e2e.temporal import (
        build_temporal_store,
        generate_temporal,
        goldengraph_asof,
    )

    docs, facts, qs = generate_temporal(seed=7, n_facts=20, ambiguity=0.6)
    store = build_temporal_store(facts)
    for q in qs:
        got = goldengraph_asof(store, q.anchor_id, q.relation, q.D)
        assert got == q.gold_obj  # exact, in both regimes
