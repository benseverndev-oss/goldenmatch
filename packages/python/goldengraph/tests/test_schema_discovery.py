"""Schema discovery: discover a RelationSchema (vocab + direction) from open extractions (wheel-free)."""
from __future__ import annotations

from goldengraph.extract import Extraction, Mention, Relationship


def _ext(mentions, rels):
    return Extraction(
        mentions=[Mention(name=n, typ="concept") for n in mentions],
        relationships=[Relationship(*r) for r in rels],
    )


class _StubEmbedder:
    """Deterministic toy embedder: vector = per-stem presence over a tiny vocab. Identical-stem
    predicates embed alike; unrelated ones are orthogonal -- so clustering is driven by the STRING
    rules under test, not by accidental embedding overlap."""

    def embed(self, texts):
        import numpy as np

        vocab = ["work", "employ", "acquir", "buy", "author", "wrote", "locat", "part"]
        out = []
        for t in texts:
            v = np.array([1.0 if stem in t.lower() else 0.0 for stem in vocab])
            out.append((v / (np.linalg.norm(v) + 1e-9)).tolist())
        return out


def test_collect_edges_pairs_surfaces_predicate_and_source():
    from goldengraph.schema_discovery import _collect_edges

    ext = _ext(["A", "B"], [(0, "acquired", 1)])
    edges = _collect_edges([ext], ["A acquired B."])
    assert edges == [("A", "acquired", "B", "A acquired B.")]


# ── Task 2: clustering ──


def test_cluster_merges_passive_and_substring_variants():
    from goldengraph.schema_discovery import _cluster_predicates

    preds = ["acquired", "acquired by", "was acquired by", "authored", "was authored by"]
    clusters = _cluster_predicates(preds, _StubEmbedder())
    fam = {frozenset(c) for c in clusters}
    assert frozenset({"acquired", "acquired by", "was acquired by"}) in fam
    assert frozenset({"authored", "was authored by"}) in fam


def test_cluster_keeps_unrelated_separate():
    from goldengraph.schema_discovery import _cluster_predicates

    clusters = _cluster_predicates(["acquired", "located in"], _StubEmbedder())
    assert len(clusters) == 2


# ── Task 3: direction ──


def test_active_phrase_is_forward():
    from goldengraph.schema_discovery import _phrase_is_reverse

    edges = [("A", "acquired", "B", "A acquired B.")]
    assert _phrase_is_reverse("acquired", edges) is False


def test_passive_phrase_is_reverse():
    from goldengraph.schema_discovery import _phrase_is_reverse

    edges = [("B", "was acquired by", "A", "B was acquired by A.")]
    assert _phrase_is_reverse("was acquired by", edges) is True


def test_reversed_extraction_detected_by_source_order():
    from goldengraph.schema_discovery import _phrase_is_reverse

    # active phrase but the MODEL reversed it: source "A located in B", extracted (B, located in, A)
    edges = [("B", "located in", "A", "A located in B.")]
    assert _phrase_is_reverse("located in", edges) is True


# ── Task 4: assemble + end-to-end ──


def test_assemble_schema_labels_and_directions():
    from goldengraph.schema_discovery import _assemble_schema

    clusters = [["acquired", "was acquired by"], ["located in"]]
    edges_by_phrase = {
        "acquired": [("A", "acquired", "B", "A acquired B.")],
        "was acquired by": [("B", "was acquired by", "A", "B was acquired by A.")],
        "located in": [("X", "located in", "Y", "X located in Y.")],
    }
    sch = _assemble_schema(clusters, edges_by_phrase)
    assert "acquired" in sch.relations and "located_in" in sch.relations
    assert sch.match("acquired") == ("acquired", False)
    assert sch.match("was acquired by") == ("acquired", True)


def test_discover_schema_recovers_and_canonicalizes_reversed_edge():
    from goldengraph.schema import canonicalize_extraction
    from goldengraph.schema_discovery import discover_schema

    exts = [
        _ext(["A", "B"], [(0, "acquired", 1)]),  # A acquired B  (canonical)
        _ext(["C", "D"], [(0, "was acquired by", 1)]),  # C was acquired by D == D acquired C
    ]
    sources = ["A acquired B.", "C was acquired by D."]
    sch = discover_schema(exts, sources, _StubEmbedder())
    out = canonicalize_extraction(exts[1], sch)
    r = out.relationships[0]
    assert (out.mentions[r.subj].name, r.predicate, out.mentions[r.obj].name) == (
        "D",
        "acquired",
        "C",
    )


# ── Task 5: LLM tie-break ──


class _StubLLM:
    def __init__(self, reply):
        self._reply = reply

    def complete(self, prompt):
        return self._reply


def test_llm_consolidate_merges_named_groups():
    from goldengraph.schema_discovery import _llm_consolidate

    clusters = [["acquired"], ["purchased"], ["located in"]]
    out = _llm_consolidate(clusters, _StubLLM('{"merge": [[0, 1]]}'))
    fam = {frozenset(c) for c in out}
    assert frozenset({"acquired", "purchased"}) in fam
    assert frozenset({"located in"}) in fam


def test_llm_consolidate_fail_open_on_bad_json():
    from goldengraph.schema_discovery import _llm_consolidate

    clusters = [["acquired"], ["purchased"]]
    assert _llm_consolidate(clusters, _StubLLM("not json")) == clusters


# ── Task 6: discovery flow seam (seam-confirmation, not red-first) ──


def test_discovery_flow_canonicalizes_corpus_edges():
    from goldengraph.schema import canonicalize_extraction
    from goldengraph.schema_discovery import discover_schema

    exts = [
        _ext(["A", "B"], [(0, "acquired", 1)]),
        _ext(["C", "D"], [(0, "was acquired by", 1)]),
    ]
    sources = ["A acquired B.", "C was acquired by D."]
    sch = discover_schema(exts, sources, _StubEmbedder())
    canon = [canonicalize_extraction(e, sch) for e in exts]
    got = [
        (c.mentions[c.relationships[0].subj].name, c.relationships[0].predicate,
         c.mentions[c.relationships[0].obj].name)
        for c in canon
    ]
    assert got == [("A", "acquired", "B"), ("D", "acquired", "C")]
