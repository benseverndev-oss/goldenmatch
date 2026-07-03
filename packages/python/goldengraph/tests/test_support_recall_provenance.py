"""Supporting-fact provenance: edges carry their owning document id (`source_refs`), and the
traversal/retrieval paths surface those ids via `provenance_out` -- the wiring that makes the bench's
support_recall measurable (it was hard-zeroed because `ask` exposed no ball provenance)."""
from __future__ import annotations

from goldengraph.answer import _add_refs, aggregate_members, trace_chain
from goldengraph.extract import Attribute, Extraction, Mention, Relationship
from goldengraph.ingest import build_batch
from goldengraph.resolve import ResolvedEntity


def _ents(*names):
    return [
        ResolvedEntity(local_id=i, canonical_name=n, typ="thing",
                       surface_names=[n], record_keys=[f"k{i}"], member_idx=[i])
        for i, n in enumerate(names)
    ]


def test_build_batch_stamps_source_ref_on_every_edge():
    ext = Extraction(
        mentions=[Mention(name="A", typ="thing"), Mention(name="B", typ="thing")],
        relationships=[Relationship(subj=0, predicate="acquired", obj=1)],
        attributes=[Attribute(subj=0, predicate="founded", value="1999", typ="year")],
    )
    batch = build_batch(ext, _ents("A", "B"), at=1, source_ref="doc-7")
    assert batch["edges"], "expected at least the relationship edge"
    for e in batch["edges"]:  # relationship edge AND literal-attribute edge
        assert e["source_refs"] == ["doc-7"]


def test_build_batch_source_ref_none_is_empty_backcompat():
    ext = Extraction(
        mentions=[Mention(name="A", typ="thing"), Mention(name="B", typ="thing")],
        relationships=[Relationship(subj=0, predicate="acquired", obj=1)],
    )
    batch = build_batch(ext, _ents("A", "B"), at=1)  # no source_ref
    assert all(e["source_refs"] == [] for e in batch["edges"])


def test_add_refs_none_is_noop():
    _add_refs(None, [{"source_refs": ["x"]}])  # must not raise


class _StubGraph:
    """Minimal slice graph: edges are dicts with subj/predicate/obj/source_refs (the native query()
    shape). Mirrors test_chain_retrieval's stub plus the source_refs the store now returns."""

    def __init__(self, entities, edges):
        self._ents = entities
        self._edges = edges
        self._byname: dict = {}
        for e in entities:
            self._byname.setdefault(e["canonical_name"], []).append(e["entity_id"])

    def seeds_by_name(self, name):
        return list(self._byname.get(name, []))

    def query(self, ids, hops):
        ids = set(ids)
        edges = [e for e in self._edges if e["subj"] in ids or e["obj"] in ids]
        keep = ids | {e["subj"] for e in edges} | {e["obj"] for e in edges}
        return {"entities": [e for e in self._ents if e["entity_id"] in keep], "edges": edges}


def _graph():
    ents = [{"entity_id": i, "canonical_name": n} for i, n in enumerate(["A", "B", "C"])]
    edges = [
        {"subj": 0, "predicate": "acquired", "obj": 1, "source_refs": ["d_ab"]},
        {"subj": 1, "predicate": "part_of", "obj": 2, "source_refs": ["d_bc"]},
    ]
    return _StubGraph(ents, edges)


def test_trace_chain_collects_traversed_edge_provenance():
    refs: set = set()
    assert trace_chain(_graph(), "A", ("acquired", "part of"), refs_out=refs) == "C"
    # both hops' edges contribute their owning-doc ids
    assert refs == {"d_ab", "d_bc"}


def test_trace_chain_reversed_fallback_collects_provenance():
    # 'authored' extracted backwards (Paper -authored-> Author); reversed fallback still records the ref
    ents = [{"entity_id": 0, "canonical_name": "Author"}, {"entity_id": 1, "canonical_name": "Paper"}]
    edges = [{"subj": 1, "predicate": "authored", "obj": 0, "source_refs": ["d_rev"]}]
    refs: set = set()
    assert trace_chain(_StubGraph(ents, edges), "Author", ("authored",), refs_out=refs) == "Paper"
    assert refs == {"d_rev"}


def test_aggregate_members_collects_provenance():
    refs: set = set()
    members = aggregate_members(_graph(), "A", "acquired", refs_out=refs)
    assert members == {"B"}
    assert refs == {"d_ab"}
