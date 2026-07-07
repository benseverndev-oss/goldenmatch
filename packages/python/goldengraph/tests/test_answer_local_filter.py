"""Lever A: the gated path-preserving prune on the LOCAL retrieval ball.

`_apply_local_filter` unit tests are pure (no wheel/LLM). The wiring test drives real `ask`
(needs the store wheel) and spies `_apply_local_filter` to prove the local branch routes the
ball through it with the seeds. See docs/superpowers/plans/2026-07-07-goldengraph-path-aware-retrieval.md.
"""
from __future__ import annotations

import json

from goldengraph import ResolvedEntity, ask
from goldengraph import answer as answer_mod
from goldengraph.extract import Mention
from goldengraph.subgraph_filter import filter_subgraph_to_paths
from conftest import RecordingLLM, StubEmbedder, StubLLM


def _sub():
    # chain 0->1->2 (anchors 0,2) + off-topic leaf 3 off of 0
    return {
        "entities": [{"entity_id": i, "canonical_name": f"n{i}", "typ": "concept"} for i in range(4)],
        "edges": [
            {"subj": 0, "predicate": "r", "obj": 1},
            {"subj": 1, "predicate": "r", "obj": 2},
            {"subj": 0, "predicate": "r", "obj": 3},
        ],
    }


def test_off_by_default_is_identity(monkeypatch):
    monkeypatch.delenv("GOLDENGRAPH_LOCAL_FILTER", raising=False)
    sub = _sub()
    assert answer_mod._apply_local_filter(sub, [0, 2]) is sub  # unchanged object


def test_on_prunes_to_paths(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_LOCAL_FILTER", "path")
    monkeypatch.setenv("GOLDENGRAPH_LOCAL_FILTER_HALO", "0")
    sub = _sub()
    out = answer_mod._apply_local_filter(sub, [0, 2])
    assert out == filter_subgraph_to_paths(sub, [0, 2], halo=0)
    kept = sorted(e["entity_id"] for e in out["entities"])
    assert kept == [0, 1, 2]  # chain kept, off-topic leaf 3 dropped


def test_halo_reader_defaults_and_bad_value(monkeypatch):
    monkeypatch.delenv("GOLDENGRAPH_LOCAL_FILTER_HALO", raising=False)
    assert answer_mod._local_filter_halo() == 1
    monkeypatch.setenv("GOLDENGRAPH_LOCAL_FILTER_HALO", "notanint")
    assert answer_mod._local_filter_halo() == 1


# --- wiring: `ask` local branch routes the ball through _apply_local_filter ---

_EXTRACTION = json.dumps(
    {
        "entities": [
            {"name": "Acme", "type": "org"},
            {"name": "Rocket", "type": "product"},
        ],
        "relationships": [{"subj": 0, "predicate": "made", "obj": 1}],
    }
)


def _identity_resolver(mentions: list[Mention]) -> list[ResolvedEntity]:
    return [ResolvedEntity(i, m.name, m.typ, [m.name], [f"k{i}"], [i]) for i, m in enumerate(mentions)]


def test_ask_local_routes_ball_through_apply_local_filter(store, monkeypatch):
    from goldengraph import ingest

    ingest("doc", store, at=100, llm=StubLLM(_EXTRACTION), resolver=_identity_resolver)

    calls = []

    def _spy(subgraph, seeds):
        calls.append((subgraph, list(seeds)))
        return subgraph  # passthrough

    monkeypatch.setattr(answer_mod, "_apply_local_filter", _spy)
    ask(
        "Acme",
        store,
        llm=RecordingLLM(),
        embedder=StubEmbedder({"Acme": 0, "Rocket": 1}),
        valid_t=150,
        tx_t=150,
        mode="local",
        hops=1,
    )
    assert len(calls) == 1  # the local branch called it exactly once
    sub, seeds = calls[0]
    assert seeds  # seeded
    names = {e["canonical_name"] for e in sub["entities"]}
    assert "Acme" in names  # it received the retrieved ball, not an empty graph
