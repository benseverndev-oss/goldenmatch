"""SP4b pipeline tests.

The marshaling/wiring tests INJECT a deterministic resolver, so they need no
goldenmatch (resolution accuracy is goldenmatch's + SP6's concern). One test
exercises the real goldenmatch-backed `resolve` and is skipped when goldenmatch
isn't importable (it runs in the CI lane, which installs goldenmatch).
"""

from __future__ import annotations

import json

import pytest

from goldengraph import ResolvedEntity, build_batch, extract, ingest, parse_extraction
from goldengraph.extract import Mention
from conftest import StubLLM

# canned extraction: 3 entities, one relationship (Acme -> Rocket)
_EXTRACTION_JSON = json.dumps(
    {
        "entities": [
            {"name": "Acme", "type": "org"},
            {"name": "Coyote", "type": "person"},
            {"name": "Rocket", "type": "product"},
        ],
        "relationships": [
            {"subj": 0, "predicate": "made", "obj": 2},
            {"subj": 99, "predicate": "bad_index", "obj": 0},  # dropped (out of range)
        ],
    }
)


def _identity_resolver(mentions: list[Mention]) -> list[ResolvedEntity]:
    """1:1 mention->entity (no merge); deterministic, no goldenmatch."""
    return [
        ResolvedEntity(
            local_id=i,
            canonical_name=m.name,
            typ=m.typ,
            surface_names=[m.name],
            record_keys=[f"k{i}"],
            member_idx=[i],
        )
        for i, m in enumerate(mentions)
    ]


def test_extract_parses_and_drops_bad_indices():
    ex = parse_extraction(_EXTRACTION_JSON)
    assert [m.name for m in ex.mentions] == ["Acme", "Coyote", "Rocket"]
    assert len(ex.relationships) == 1  # the out-of-range relationship was dropped
    assert (ex.relationships[0].subj, ex.relationships[0].obj) == (0, 2)


def test_extract_strips_code_fence():
    fenced = "```json\n" + _EXTRACTION_JSON + "\n```"
    assert len(parse_extraction(fenced).mentions) == 3


def test_ingest_marshals_to_store(store):
    ingest(
        "irrelevant (stub)",
        store,
        at=100,
        llm=StubLLM(_EXTRACTION_JSON),
        resolver=_identity_resolver,
    )
    view = store.as_of(150, 150)
    seeds = view.seeds_by_name("Acme")
    assert seeds  # Acme entity present
    edges = view.query(seeds, 1)["edges"]
    assert len(edges) == 1 and edges[0]["predicate"] == "made"


def test_ingest_drops_self_loops(store):
    # a relationship whose endpoints resolve to the SAME entity is dropped
    one = json.dumps(
        {
            "entities": [{"name": "X", "type": "t"}],
            "relationships": [{"subj": 0, "predicate": "self", "obj": 0}],
        }
    )
    ingest("x", store, at=10, llm=StubLLM(one), resolver=_identity_resolver)
    view = store.as_of(20, 20)
    assert view.query(view.seeds_by_name("X"), 1)["edges"] == []


def test_build_batch_remaps_to_entity_local_ids():
    ex = parse_extraction(_EXTRACTION_JSON)
    # a resolver that MERGES mentions 0 and 1 into entity 0, mention 2 -> entity 1
    merged = [
        ResolvedEntity(0, "Acme", "org", ["Acme", "Coyote"], ["k0", "k1"], [0, 1]),
        ResolvedEntity(1, "Rocket", "product", ["Rocket"], ["k2"], [2]),
    ]
    batch = build_batch(ex, merged, at=5)
    # rel was mention 0 -> mention 2; remaps to entity 0 -> entity 1
    assert batch["edges"] == [
        {"subj_local": 0, "predicate": "made", "obj_local": 1,
         "valid_from": 5, "valid_to": None, "source_refs": []}
    ]


def test_resolve_groups_exact_duplicates():
    pytest.importorskip("goldenmatch")
    from goldengraph import resolve

    # exact-duplicate surface forms dedup version-stably (no fuzzy toy-merge)
    mentions = [Mention("Acme Inc", "org"), Mention("Acme Inc", "org"), Mention("Beta", "org")]
    entities = resolve(mentions)
    # the two identical "Acme Inc" mentions collapse into one entity
    names = sorted(e.canonical_name for e in entities)
    assert names == ["Acme Inc", "Beta"]
    acme = next(e for e in entities if e.canonical_name == "Acme Inc")
    assert sorted(acme.member_idx) == [0, 1]
