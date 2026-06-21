"""Engineered ambiguity corpus -- the (ER_accuracy)^hops thesis instrument.

Builds a typed-edge graph over the ER-KG-Bench entity universe, samples k-hop
questions (k in 1..max_hops), and renders supporting documents in which a
controllable fraction (`ambiguity`) of entity mentions use a VARIANT surface form
instead of the canonical name. Deterministic for a seed: same seed -> identical
corpus."""
from __future__ import annotations

import random
import sys
from dataclasses import dataclass
from pathlib import Path

from .corpora import Document, QACorpus, QAItem

#: Typed relations the question generator traverses. Each maps (subject -> object).
RELATION_SCHEMA: tuple[str, ...] = (
    "works_at",
    "located_in",
    "acquired",
    "authored",
    "part_of",
)


@dataclass(frozen=True)
class _Entity:
    id: str
    canonical: str
    variants: tuple[str, ...]  # abbreviation / nickname / synonym forms


def _load_entities() -> list[_Entity]:
    """Load the ER-KG-Bench entity universe + variant surface forms from dataset/.

    Reuses `dataset/concepts_loader.load_concepts`; the canonical name is the
    concept, and the variants are the distinct non-canonical surface forms (which
    feed the ambiguity dial). Pure / no network."""
    bench_root = Path(__file__).resolve().parents[2]
    if str(bench_root) not in sys.path:
        sys.path.insert(0, str(bench_root))
    from dataset.concepts_loader import load_concepts

    concepts = load_concepts(bench_root / "dataset" / "concepts.jsonl")
    entities: list[_Entity] = []
    for c in concepts:
        variants = tuple(
            dict.fromkeys(v.surface for v in c.variants if v.surface != c.concept)
        )
        entities.append(_Entity(id=c.canonical_id, canonical=c.concept, variants=variants))
    return entities


def _render_mention(ent: _Entity, rng: random.Random, ambiguity: float) -> str:
    if ent.variants and rng.random() < ambiguity:
        return rng.choice(list(ent.variants))
    return ent.canonical


def generate_engineered(
    *, seed: int, n_questions: int, ambiguity: float, max_hops: int = 4
) -> QACorpus:
    rng = random.Random(seed)
    entities = _load_entities()
    by_id = {e.id: e for e in entities}
    ids = [e.id for e in entities]

    # Deterministic typed-edge graph: each entity gets 2-4 outgoing edges.
    edges: dict[str, list[tuple[str, str]]] = {e.id: [] for e in entities}
    for e in entities:
        for _ in range(rng.randint(2, 4)):
            rel = rng.choice(RELATION_SCHEMA)
            dst = rng.choice(ids)
            if dst != e.id:
                edges[e.id].append((rel, dst))

    # One document per edge stating the relation, with ambiguity-dialed mentions.
    documents: list[Document] = []
    for src_id, outs in edges.items():
        for j, (rel, dst_id) in enumerate(outs):
            s = _render_mention(by_id[src_id], rng, ambiguity)
            o = _render_mention(by_id[dst_id], rng, ambiguity)
            documents.append(
                Document(
                    id=f"{src_id}-{rel}-{dst_id}-{j}",
                    text=f"{s} {rel.replace('_', ' ')} {o}.",
                )
            )

    # Sample k-hop questions by walking the edge graph; the answer is the terminal
    # entity's canonical name; the gold path is the entity-id chain (length k).
    questions: list[QAItem] = []
    for qi in range(n_questions):
        k = rng.randint(1, max_hops)
        start = rng.choice(ids)
        path = [start]
        cur = start
        ok = True
        for _ in range(k):
            if not edges[cur]:
                ok = False
                break
            _, nxt = rng.choice(edges[cur])
            path.append(nxt)
            cur = nxt
        if not ok or len(path) != k + 1:
            continue
        answer_ent = by_id[path[-1]]
        start_mention = _render_mention(by_id[start], rng, ambiguity)
        questions.append(
            QAItem(
                id=f"eng-q{qi}",
                question=(
                    f"Following the chain from {start_mention}, what is the final entity?"
                ),
                gold_answer=answer_ent.canonical,
                gold_supporting_fact_ids=tuple(path[1:]),
                hop_count=k,
                ambiguity_level=ambiguity,
            )
        )
    return QACorpus(
        name="engineered", documents=tuple(documents), questions=tuple(questions)
    )
