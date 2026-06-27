"""Aggregation/set/count capability bench (slice B1). A fan-out corpus + goldengraph
exact traversal vs a deterministic passage-window floor. The KG does what RAG can't:
exact set aggregation, size-invariant; the window's recall collapses with set size."""
from __future__ import annotations

import random
from dataclasses import dataclass

from .corpora import Document, QACorpus
from .engineered import RELATION_SCHEMA, _edge_doc_id, _load_entities, _render_mention

_BUCKETS = ((2, 4), (5, 10), (11, 20))


@dataclass(frozen=True)
class AggQuestion:
    id: str
    kind: str            # "list" | "count"
    question: str
    anchor_id: str       # canonical id of the source entity
    relation: str
    gold_members: tuple[str, ...]  # canonical ids of the member set
    gold_count: int


def size_bucket(n: int) -> str:
    for lo, hi in _BUCKETS:
        if lo <= n <= hi:
            return f"{lo}-{hi}"
    return f">{_BUCKETS[-1][1]}"


def generate_aggregation(*, seed: int, n_anchors: int, ambiguity: float,
                         fanout_buckets=_BUCKETS):
    rng = random.Random(seed)
    ents = _load_entities()
    by_id = {e.id: e for e in ents}
    ids = [e.id for e in ents]
    docs: list[Document] = []
    qs: list[AggQuestion] = []
    for i in range(n_anchors):
        lo, hi = fanout_buckets[i % len(fanout_buckets)]
        src_id = ids[i % len(ids)]
        # Relation varies on the OUTER cycle so (src_id, rel) is unique per anchor up
        # to n_anchors = len(ids)*len(RELATION_SCHEMA) (=225), AND a reused src_id
        # accumulates MULTIPLE relations in the store -- both load-bearing: the former
        # keeps the gate's exactness (no two anchors merge into one node and union
        # their gold sets -> set-F1 precision would halve and the HARD gate would
        # fail); the latter makes the predicate round-trip test real. Do NOT revert
        # to `i % len(RELATION_SCHEMA)` (collides at n_anchors > len(ids)).
        rel = RELATION_SCHEMA[(i // len(ids)) % len(RELATION_SCHEMA)]
        k = min(rng.randint(lo, hi), len(ids) - 1)
        members = rng.sample([x for x in ids if x != src_id], k)
        for m in members:
            s = _render_mention(by_id[src_id], rng, ambiguity)
            o = _render_mention(by_id[m], rng, ambiguity)
            docs.append(Document(
                id=_edge_doc_id(src_id, rel, m),
                text=f"{s} {rel.replace('_', ' ')} {o}.",
                src_surface=s, dst_surface=o,
            ))
        rel_words = rel.replace("_", " ")
        canon = by_id[src_id].canonical
        qs.append(AggQuestion(
            id=f"agg-list-{i}", kind="list",
            question=f"List all entities that {canon} {rel_words}.",
            anchor_id=src_id, relation=rel,
            gold_members=tuple(members), gold_count=len(members)))
        qs.append(AggQuestion(
            id=f"agg-count-{i}", kind="count",
            question=f"How many entities does {canon} {rel_words}?",
            anchor_id=src_id, relation=rel,
            gold_members=tuple(members), gold_count=len(members)))
    return tuple(docs), qs


def agg_documents_corpus(docs) -> QACorpus:
    """Wrap the fan-out docs as a QACorpus so ablation._build_store can consume it."""
    return QACorpus(name="aggregation", documents=tuple(docs), questions=())


def set_f1(predicted: set, gold: set) -> dict:
    tp = len(predicted & gold)
    p = tp / len(predicted) if predicted else 0.0
    r = tp / len(gold) if gold else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"precision": p, "recall": r, "f1": f1}


def count_accuracy(predicted_count: int, gold_count: int) -> float:
    return 1.0 if predicted_count == gold_count else 0.0
