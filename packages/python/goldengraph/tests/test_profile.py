"""Virtual Fingerprint synthesis tests.

Covers the genuinely new host logic -- the LLM synthesis pass and its defensive
fallbacks -- with a stub LLM. The engine itself (blocking / scoring / clustering)
is tested in the Rust core; `resolve_profiles` marshaling is covered by a
skip-if-absent integration test so this file needs no compiled wheel.
"""

from __future__ import annotations

import importlib.util

import pytest

from goldengraph.extract import Extraction, Mention, Relationship
from goldengraph.profile import (
    Fingerprint,
    synthesize_edge_fingerprints,
    synthesize_node_fingerprints,
    synthesize_profiles,
)


class StubLLM:
    def __init__(self, response: str):
        self._response = response

    def complete(self, prompt: str) -> str:
        self.last_prompt = prompt
        return self._response


def _extraction() -> Extraction:
    return Extraction(
        mentions=[
            Mention(name="Thomas Nabbes", typ="Person", context="English playwright"),
            Mention(name="Play X", typ="Work"),
        ],
        relationships=[Relationship(subj=0, predicate="wrote", obj=1)],
    )


def test_node_synthesis_parses_aligned_llm_output():
    llm = StubLLM(
        '{"fingerprints": ["Thomas Nabbes | Playwright | 17th Century | Wrote Play X", '
        '"Play X | Play | 1638 | Comedy"]}'
    )
    fps = synthesize_node_fingerprints(_extraction(), llm)
    assert fps[0] == "Thomas Nabbes | Playwright | 17th Century | Wrote Play X"
    assert fps[1].startswith("Play X | Play")


def test_node_synthesis_strips_code_fence():
    llm = StubLLM('```json\n{"fingerprints": ["A | B | C | D", "E | F | G | H"]}\n```')
    fps = synthesize_node_fingerprints(_extraction(), llm)
    assert fps == ["A | B | C | D", "E | F | G | H"]


def test_node_synthesis_falls_back_on_count_drift():
    # LLM returns one fingerprint for two entities -> misalignment risk -> fall
    # back wholesale to the deterministic per-mention fingerprints.
    llm = StubLLM('{"fingerprints": ["only one"]}')
    fps = synthesize_node_fingerprints(_extraction(), llm)
    assert len(fps) == 2
    assert fps[0].startswith("Thomas Nabbes | Person")


def test_node_synthesis_falls_back_on_malformed_json():
    fps = synthesize_node_fingerprints(_extraction(), StubLLM("not json at all"))
    assert len(fps) == 2
    assert "UNKNOWN" in fps[1]  # Play X has no context -> UNKNOWN attribute


def test_no_llm_uses_deterministic_fingerprints():
    fps = synthesize_node_fingerprints(_extraction(), None)
    assert fps[0] == "Thomas Nabbes | Person | UNKNOWN | English playwright"


def test_edge_fingerprints_use_resolved_endpoint_names():
    node_fps = ["Thomas Nabbes | Playwright | x | y", "Play X | Play | x | y"]
    edges = synthesize_edge_fingerprints(_extraction(), node_fps)
    assert edges == ["wrote | wrote | UNKNOWN | Thomas Nabbes -> Play X"]


def test_synthesize_profiles_includes_nodes_and_edges():
    profiles = synthesize_profiles(_extraction(), None, include_edges=True)
    kinds = [p.kind for p in profiles]
    assert kinds == ["node", "node", "edge"]
    assert all(isinstance(p, Fingerprint) for p in profiles)


def test_synthesize_profiles_can_skip_edges():
    profiles = synthesize_profiles(_extraction(), None, include_edges=False)
    assert [p.kind for p in profiles] == ["node", "node"]


def test_empty_extraction_yields_nothing():
    empty = Extraction(mentions=[], relationships=[])
    assert synthesize_profiles(empty, None) == []


@pytest.mark.skipif(
    importlib.util.find_spec("goldenprofile_native") is None,
    reason="goldenprofile_native wheel not built",
)
def test_resolve_profiles_reunites_disjoint_mentions():
    from goldengraph.profile import resolve_profiles

    fps = [
        Fingerprint("node", 0, "Thomas Nabbes | Playwright | 17th c | Wrote Play X"),
        Fingerprint("node", 1, "Nabbes | Playwright | UNKNOWN | Born 1605"),
        Fingerprint("node", 2, "William Shakespeare | Playwright | UNKNOWN | Wrote Hamlet"),
    ]
    res = resolve_profiles(fps)
    nabbes = next(c for c in res.clusters if 0 in c)
    assert sorted(nabbes) == [0, 1]
    assert any(c == [2] for c in res.clusters)


class _CatStubEmbedder:
    """Returns a fixed-dim vector per text; unknown -> zeros. Lets us assert the
    request shape without the wheel."""

    def embed(self, texts):
        import numpy as np

        return np.ones((len(texts), 3), dtype=float)


def test_resolve_profiles_marshals_category_embeddings(monkeypatch):
    """With an embedder, the request carries a category-only embedding per profile
    (the gate's synonym escape-hatch signal) -- wheel-free: we capture the JSON the
    engine boundary receives instead of running the engine."""
    import json

    from goldengraph import profile as prof

    captured: dict = {}

    def _fake_resolve_json(req_str: str) -> str:
        captured["req"] = json.loads(req_str)
        return json.dumps({"clusters": [[0], [1]], "edges": []})

    monkeypatch.setattr(prof, "_engine", lambda: _fake_resolve_json)
    fps = [
        Fingerprint("node", 0, "Australia | Country | UNKNOWN | Federal monarchy"),
        Fingerprint("node", 1, "Australia | Nation | UNKNOWN | Smallest continent"),
    ]
    prof.resolve_profiles(fps, embedder=_CatStubEmbedder())
    req = captured["req"]
    assert "category_embeddings" in req
    # One category embedding per profile, aligned with the fingerprint order.
    assert len(req["category_embeddings"]) == len(fps)
    assert all(len(row) == 3 for row in req["category_embeddings"])
    # The whole-fingerprint embeddings are still sent (blocking + soft term).
    assert len(req["embeddings"]) == len(fps)


def test_resolve_profiles_no_embedder_omits_embeddings(monkeypatch):
    """Without an embedder, neither embedding array is sent (legacy structured-only
    path) -- the back-compat contract the Rust core's empty-slice fallback relies on."""
    import json

    from goldengraph import profile as prof

    captured: dict = {}

    def _fake_resolve_json(req_str: str) -> str:
        captured["req"] = json.loads(req_str)
        return json.dumps({"clusters": [[0]], "edges": []})

    monkeypatch.setattr(prof, "_engine", lambda: _fake_resolve_json)
    prof.resolve_profiles([Fingerprint("node", 0, "A | C | UNKNOWN | UNKNOWN")])
    assert "embeddings" not in captured["req"]
    assert "category_embeddings" not in captured["req"]
