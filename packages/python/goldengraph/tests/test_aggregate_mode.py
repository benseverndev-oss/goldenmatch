"""aggregate_members + ask(mode='auto') -- needs goldengraph_native (CI lane)."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("goldengraph_native")

from conftest import StubEmbedder, StubLLM  # noqa: E402
from goldengraph import ResolvedEntity, ingest  # noqa: E402
from goldengraph.answer import aggregate_members, ask  # noqa: E402
from goldengraph.extract import Mention  # noqa: E402

_EXTRACTION = json.dumps(
    {
        "entities": [
            {"name": "Apple", "type": "org"},
            {"name": "Banana", "type": "org"},
            {"name": "Cherry", "type": "org"},
        ],
        "relationships": [
            {"subj": 0, "predicate": "works_with", "obj": 1},
            {"subj": 0, "predicate": "works_with", "obj": 2},
        ],
    }
)


def _identity_resolver(mentions: list[Mention]) -> list[ResolvedEntity]:
    return [ResolvedEntity(i, m.name, m.typ, [m.name], [f"k{i}"], [i]) for i, m in enumerate(mentions)]


def _seed(store):
    ingest("doc", store, at=100, llm=StubLLM(_EXTRACTION), resolver=_identity_resolver)


def test_aggregate_members_returns_object_names(store):
    _seed(store)
    g = store.as_of(150, 150)
    assert aggregate_members(g, "Apple", "works_with") == {"Banana", "Cherry"}


def test_aggregate_members_empty_when_anchor_missing(store):
    _seed(store)
    g = store.as_of(150, 150)
    assert aggregate_members(g, "Nonexistent", "works_with") == set()


def test_ask_auto_routes_aggregation(store):
    _seed(store)
    out = ask(
        "List all entities that Apple works with.",
        store,
        llm=StubLLM("UNUSED"),
        embedder=StubEmbedder({}),
        valid_t=150,
        tx_t=150,
        mode="auto",
    )
    assert "Banana" in out and "Cherry" in out
