"""Entity-aware RAG surface (#1092).

retrieve -> resolve to entities -> conflict-aware fact merge. All deterministic
and offline: the in-house embedder + numpy ANN fallback + deterministic
canonicalization need no network or torch. ``threshold=-1.0`` retrieves the whole
(filtered) frame so the test asserts the RESOLVE + MERGE composition, not the
similarity ranking (covered in test_retrieval.py).
"""
from __future__ import annotations

import json

import goldenmatch as gm
import polars as pl
import pytest
from goldenmatch.core.rag_surface import (
    Entity,
    EntityRetrievalResult,
    entity_aware_retrieve,
)

KB = pl.DataFrame(
    {
        # two "acme" rows are duplicates (exact name); "globex" is distinct.
        "name": ["acme", "acme", "globex"],
        "ceo": ["Jane Doe", "Jane A. Doe", "John Smith"],
        "phone": [None, "555-1234", "555-9999"],
    }
)


def _stub_llm(payload: dict):
    return lambda prompt: (json.dumps(payload), 100, 30)


# ── collapse + conflict-aware merge ──────────────────────────────────────────


def test_collapses_duplicates_into_fewer_entities():
    res = entity_aware_retrieve(KB, "acme", "name", exact=["name"], threshold=-1.0, k=10)
    assert isinstance(res, EntityRetrievalResult)
    assert res.retrieved == 3
    assert res.n_entities == 2
    assert res.collapsed == 1  # one duplicate removed from the LLM context
    assert res.method == "deterministic"


def test_conflict_aware_fact_merge_with_provenance():
    res = entity_aware_retrieve(KB, "acme", "name", exact=["name"], threshold=-1.0, k=10)
    acme = next(e for e in res if e.record["name"] == "acme")
    assert acme.size == 2
    # most-complete value wins per field; the null phone is filled from member 1.
    assert acme.record["ceo"] == "Jane A. Doe"
    assert acme.record["phone"] == "555-1234"
    assert acme.canonical.provenance["phone"].source_index == 1


def test_entities_ranked_by_best_member_similarity():
    res = entity_aware_retrieve(KB, "acme", "name", exact=["name"], threshold=-1.0, k=10)
    scores = [e.score for e in res]
    assert scores == sorted(scores, reverse=True)
    assert [e.entity_id for e in res] == list(range(len(res)))
    # the acme cluster (exact query hit) ranks first.
    assert res.entities[0].record["name"] == "acme"


def test_all_distinct_records_stay_separate():
    df = pl.DataFrame({"name": ["alpha", "beta", "gamma"], "v": [1, 2, 3]})
    res = entity_aware_retrieve(df, "alpha", "name", exact=["name"], threshold=-1.0, k=10)
    assert res.retrieved == 3
    assert res.n_entities == 3
    assert res.collapsed == 0
    assert all(e.size == 1 for e in res)


# ── retrieval edges ──────────────────────────────────────────────────────────


def test_no_hits_returns_empty():
    res = entity_aware_retrieve(KB, "", "name", exact=["name"])
    assert len(res) == 0
    assert res.retrieved == 0 and res.collapsed == 0

    empty = entity_aware_retrieve(pl.DataFrame({"name": []}), "acme", "name")
    assert len(empty) == 0


def test_missing_column_raises():
    with pytest.raises(ValueError, match="nope"):
        entity_aware_retrieve(KB, "acme", "nope")


def test_filters_pre_exclude_before_resolution():
    # Restrict to globex only; the acme rows never enter retrieval/resolution.
    res = entity_aware_retrieve(
        KB, "globex", "name", exact=["name"], threshold=-1.0, k=10,
        filters={"name": "globex"},
    )
    assert res.retrieved == 1
    assert res.n_entities == 1
    assert res.entities[0].record["name"] == "globex"


# ── LLM canonicalization path (stubbed) ──────────────────────────────────────


def test_llm_canonicalization_path_marks_method():
    payload = {
        "fields": {
            "name": {"value": "acme", "source": 0, "reason": "agree"},
            "ceo": {"value": "Jane Doe", "source": 0, "reason": "canonical form"},
            "phone": {"value": "555-1234", "source": 1, "reason": "present"},
        },
        "rationale": "merged acme duplicates",
    }
    res = entity_aware_retrieve(
        KB, "acme", "name", exact=["name"], threshold=-1.0, k=10,
        llm_call=_stub_llm(payload),
    )
    acme = next(e for e in res if e.record.get("name") == "acme")
    assert acme.canonical.method == "llm"
    assert acme.record["ceo"] == "Jane Doe"
    assert res.method == "llm"


# ── resilience ───────────────────────────────────────────────────────────────


def test_resolve_failure_degrades_to_singletons(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("dedupe blew up")

    # Patch the symbol the surface imports lazily from the package root.
    monkeypatch.setattr(gm, "dedupe_df", boom)
    res = entity_aware_retrieve(KB, "acme", "name", exact=["name"], threshold=-1.0, k=10)
    # No crash; every retrieved record becomes its own entity.
    assert res.retrieved == 3
    assert res.n_entities == 3
    assert res.collapsed == 0


def test_single_hit_skips_resolver():
    res = entity_aware_retrieve(
        KB, "acme", "name", exact=["name"], threshold=-1.0, k=1,
    )
    assert res.retrieved == 1
    assert res.n_entities == 1
    assert res.entities[0].size == 1


# ── serialization / ergonomics ───────────────────────────────────────────────


def test_result_iterable_and_sized():
    res = entity_aware_retrieve(KB, "acme", "name", exact=["name"], threshold=-1.0, k=10)
    assert len(res) == len(list(res)) == res.n_entities
    assert all(isinstance(e, Entity) for e in res)


def test_as_dict_serializable():
    res = entity_aware_retrieve(KB, "acme", "name", exact=["name"], threshold=-1.0, k=10)
    blob = json.dumps(res.as_dict())
    assert "collapsed" in blob and "canonical" in blob and "members" in blob
