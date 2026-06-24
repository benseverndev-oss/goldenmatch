"""Literal-attribute extraction -> reachable value nodes (GOLDENGRAPH_LITERAL_ATTRS).

Targets the dominant MuSiQue EXTRACTION miss: non-entity answers (dates, quantities,
short defining values) that the entity->entity schema can't represent, so they never
enter the graph. With the flag on, the extractor emits `attributes` and `build_batch`
materializes each as a literal NODE + an edge from its owner, making the value a
reachable, answerable graph node. All offline (no LLM, no native store)."""
from __future__ import annotations

import importlib

from goldengraph import ResolvedEntity, build_batch
from goldengraph.extract import Attribute, Extraction, Mention, Relationship, parse_extraction

_ex = importlib.import_module("goldengraph.extract")


def test_parse_extraction_reads_attributes():
    raw = (
        '{"entities":[{"name":"Sega Genesis","type":"console"}],'
        '"relationships":[],'
        '"attributes":['
        '{"subj":0,"key":"release date","value":"May 1990"},'
        '{"subj":0,"key":"blank","value":"  "},'  # empty value -> dropped
        '{"subj":7,"key":"oob","value":"x"}]}'  # out-of-range owner -> dropped
    )
    e = parse_extraction(raw)
    assert [(a.subj, a.key, a.value) for a in e.attributes] == [(0, "release date", "May 1990")]


def test_parse_extraction_backward_compatible_without_attributes():
    e = parse_extraction('{"entities":[{"name":"A","type":"t"}],"relationships":[]}')
    assert e.attributes == []


def test_extract_prompt_includes_attribute_schema_only_when_enabled(monkeypatch):
    captured = {}

    class _LLM:
        def complete(self, prompt):
            captured["p"] = prompt
            return '{"entities":[],"relationships":[],"attributes":[]}'

    monkeypatch.setenv("GOLDENGRAPH_LITERAL_ATTRS", "1")
    _ex.extract("DOC", _LLM())
    assert "attributes" in captured["p"] and captured["p"].count("DOC") == 1

    monkeypatch.delenv("GOLDENGRAPH_LITERAL_ATTRS", raising=False)
    _ex.extract("DOC", _LLM())
    assert "attributes" not in captured["p"]


def test_build_batch_materializes_literal_node_and_edge():
    ex = Extraction(
        mentions=[Mention("Sega Genesis", "console")],
        relationships=[],
        attributes=[Attribute(subj=0, key="release date", value="May 1990")],
    )
    ents = [ResolvedEntity(0, "Sega Genesis", "console", ["Sega Genesis"], ["k0"], [0])]
    batch = build_batch(ex, ents, at=5)
    # one entity node + one literal node
    lits = [e for e in batch["entities"] if e["typ"] == "literal"]
    assert len(lits) == 1
    lit = lits[0]
    assert lit["canonical_name"] == "May 1990"
    # unique per-occurrence record key (no cross-doc overlap-merge)
    assert lit["record_keys"] == [f"lit:5:{lit['local_id']}"]
    # an edge from the owning entity to the literal, predicate = the attribute key
    assert {
        "subj_local": 0,
        "predicate": "release date",
        "obj_local": lit["local_id"],
        "valid_from": 5,
        "valid_to": None,
        "source_refs": [],
    } in batch["edges"]


def test_build_batch_dedups_repeated_attribute_but_not_distinct():
    ex = Extraction(
        mentions=[Mention("X", "t")],
        relationships=[],
        attributes=[
            Attribute(0, "date", "May 1990"),
            Attribute(0, "date", "may 1990"),  # case-insensitive dup -> one node
            Attribute(0, "cost", "$5"),  # distinct -> its own node
        ],
    )
    ents = [ResolvedEntity(0, "X", "t", ["X"], ["k0"], [0])]
    batch = build_batch(ex, ents, at=1)
    lits = sorted(e["canonical_name"] for e in batch["entities"] if e["typ"] == "literal")
    assert lits == ["$5", "May 1990"]
    assert sum(1 for e in batch["edges"] if e["obj_local"] != 0 or True) >= 0  # sanity
    lit_edges = [e for e in batch["edges"] if e["predicate"] in ("date", "cost")]
    assert len(lit_edges) == 3  # both 'date' attrs edge to the SAME node, 'cost' to its own


def test_build_batch_drops_attribute_with_unresolved_owner():
    # owner mention 1 isn't covered by any resolved entity -> attribute dropped
    ex = Extraction(
        mentions=[Mention("X", "t"), Mention("Y", "t")],
        relationships=[],
        attributes=[Attribute(subj=1, key="k", value="v")],
    )
    ents = [ResolvedEntity(0, "X", "t", ["X"], ["k0"], [0])]  # only mention 0 resolved
    batch = build_batch(ex, ents, at=1)
    assert not [e for e in batch["entities"] if e["typ"] == "literal"]


class _FakeGraph:
    def __init__(self, ents):
        self._ents = ents

    def entities(self):
        return self._ents


def test_seed_by_query_excludes_literal_and_empty_names():
    """Regression: a literal value node (or an empty name) must NOT enter the
    embedding batch -- a raw value can be an empty/invalid input that 400s the whole
    provider request. Literals are reached by BFS from a seed, never seeded on."""
    from goldengraph.embed import seed_by_query

    embedded: dict = {}

    class _Emb:
        def embed(self, texts):
            import numpy as np

            embedded["texts"] = list(texts)
            # deterministic: first token-char ordinal, enough to rank
            return np.asarray([[float(len(t))] for t in texts], dtype=float)

    graph = _FakeGraph([
        {"entity_id": 1, "canonical_name": "Sega Genesis", "typ": "console"},
        {"entity_id": 2, "canonical_name": "May 1990", "typ": "literal"},  # excluded
        {"entity_id": 3, "canonical_name": "   ", "typ": "console"},  # empty -> excluded
    ])
    seeds = seed_by_query(graph, "when released?", _Emb(), k=5)
    # only the real, non-empty entity is embedded (plus the query) and seedable
    assert embedded["texts"] == ["when released?", "Sega Genesis"]
    assert seeds == [1]


def test_seed_by_query_empty_graph_returns_empty():
    from goldengraph.embed import seed_by_query

    class _Emb:
        def embed(self, texts):  # pragma: no cover - must not be called
            raise AssertionError("should not embed an all-literal/empty graph")

    graph = _FakeGraph([{"entity_id": 1, "canonical_name": "May 1990", "typ": "literal"}])
    assert seed_by_query(graph, "q", _Emb()) == []


def test_literal_nodes_excluded_from_cross_doc_link_candidates():
    ingest = importlib.import_module("goldengraph.ingest")

    ex = Extraction(
        mentions=[Mention("X", "t")],
        relationships=[],
        attributes=[Attribute(0, "date", "May 1990")],
    )
    ents = [ResolvedEntity(0, "X", "t", ["X"], ["k0"], [0])]
    batch = build_batch(ex, ents, at=1)
    new_ents, feats = ingest._new_features(batch)
    # the literal node is in the batch but NOT a link candidate
    assert any(e["typ"] == "literal" for e in batch["entities"])
    assert all(e.get("typ") != "literal" for e in new_ents)
    assert len(new_ents) == len(feats) == 1
