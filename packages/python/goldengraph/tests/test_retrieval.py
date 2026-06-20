"""SP4c retrieval + synthesis + query tests.

Deterministic via a one-hot StubEmbedder + a RecordingLLM (asserts WHAT the
synthesizer saw, not free-form text). Builds a graph through SP4b `ingest` with
an injected resolver, then queries it through the real `PyStore` engine — no
goldenmatch, no real embedder/LLM.
"""

from __future__ import annotations

import json

from goldengraph import ResolvedEntity, ask, seed_by_query, to_cypher
from goldengraph.extract import Mention
from conftest import RecordingLLM, StubEmbedder, StubLLM

# Acme --made--> Rocket ; Coyote isolated-ish
_EXTRACTION = json.dumps(
    {
        "entities": [
            {"name": "Acme", "type": "org"},
            {"name": "Rocket", "type": "product"},
            {"name": "Coyote", "type": "person"},
        ],
        "relationships": [{"subj": 0, "predicate": "made", "obj": 1}],
    }
)


def _identity_resolver(mentions: list[Mention]) -> list[ResolvedEntity]:
    return [
        ResolvedEntity(i, m.name, m.typ, [m.name], [f"k{i}"], [i])
        for i, m in enumerate(mentions)
    ]


def _seed_store(store):
    from goldengraph import ingest

    ingest("doc", store, at=100, llm=StubLLM(_EXTRACTION), resolver=_identity_resolver)


# vocab: the query word + entity canonical names, each its own basis vector
_VOCAB = {"Acme": 0, "Rocket": 1, "Coyote": 2}


def test_seed_by_query_picks_nearest_entity(store):
    _seed_store(store)
    g = store.as_of(150, 150)
    seeds = seed_by_query(g, "Acme", StubEmbedder(_VOCAB), k=1)
    # the "Acme" entity is the cosine-nearest to the query "Acme"
    acme_id = g.seeds_by_name("Acme")[0]
    assert seeds == [acme_id]


def test_ask_local_synthesizes_over_the_seeded_subgraph(store):
    _seed_store(store)
    llm = RecordingLLM()
    out = ask(
        "Acme",
        store,
        llm=llm,
        embedder=StubEmbedder(_VOCAB),
        valid_t=150,
        tx_t=150,
        mode="local",
        hops=1,
    )
    assert out == "ANSWER"
    # the synthesis prompt saw the Acme->Rocket fact (seeded + 1-hop)
    prompt = llm.prompts[-1]
    assert "Acme" in prompt and "Rocket" in prompt and "made" in prompt


def test_ask_global_maps_then_reduces(store):
    _seed_store(store)
    llm = RecordingLLM()
    out = ask(
        "what happened",
        store,
        llm=llm,
        embedder=StubEmbedder(_VOCAB),
        valid_t=150,
        tx_t=150,
        mode="global",
    )
    assert out == "ANSWER"
    # >=1 map prompt + 1 reduce prompt; the reduce prompt combines summaries
    assert len(llm.prompts) >= 2
    assert "Summaries:" in llm.prompts[-1]


def test_ask_rejects_bad_mode(store):
    import pytest

    _seed_store(store)
    with pytest.raises(ValueError):
        ask("q", store, llm=RecordingLLM(), embedder=StubEmbedder(_VOCAB),
            valid_t=1, tx_t=1, mode="sideways")


def test_to_cypher_returns_string_only():
    llm = StubLLM("MATCH (a)-[:MADE]->(b) RETURN a, b")
    cy = to_cypher("what did Acme make?", llm)
    assert cy.startswith("MATCH")  # emitted, not executed
