"""Pure-Python oracle over the engineered corpus: rebuild the gold graph from
edge-document ids and walk each question's relation_chain. No native/LLM/network.

Engineered `Document.id` is `src_id::rel::dst_id` with CANONICAL ids; each
(entity, relation) has a unique edge, so a relation_chain walk is deterministic."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .corpora import QACorpus, QAItem


@dataclass
class GoldGraph:
    # src_id -> {relation -> dst_id}
    _edges: dict[str, dict[str, str]] = field(default_factory=dict)
    _names: dict[str, str] = field(default_factory=dict)  # canonical_id -> canonical name

    @classmethod
    def from_corpus(cls, corpus: QACorpus) -> GoldGraph:
        g = cls()
        # canonical names from the concept universe (ids -> concept string)
        from dataset.concepts_loader import load_concepts  # type: ignore

        bench_root = Path(__file__).resolve().parents[2]
        for c in load_concepts(bench_root / "dataset" / "concepts.jsonl"):
            g._names[c.canonical_id] = c.concept
        for d in corpus.documents:
            parts = d.id.split("::")
            if len(parts) != 3:  # non-edge doc (MuSiQue) -> skip
                continue
            src, rel, dst = parts
            g._edges.setdefault(src, {})[rel] = dst
        return g

    def has_edge(self, src: str, rel: str, dst: str) -> bool:
        return self._edges.get(src, {}).get(rel) == dst

    def edge_count(self) -> int:
        return sum(len(v) for v in self._edges.values())

    def canonical_name(self, entity_id: str) -> str:
        return self._names.get(entity_id, entity_id)


def gold_chain(g: GoldGraph, qa: QAItem) -> list[tuple[str, str, str]]:
    """Walk `qa.relation_chain` from `qa.start_entity_id` over the gold graph.
    Returns the ordered edge list [(src_id, rel, dst_id), ...]. Raises KeyError
    if the chain is broken (should never happen for a generated question)."""
    chain: list[tuple[str, str, str]] = []
    cur = qa.start_entity_id
    for rel in qa.relation_chain:
        dst = g._edges[cur][rel]
        chain.append((cur, rel, dst))
        cur = dst
    return chain
