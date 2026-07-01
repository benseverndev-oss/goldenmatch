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


#: Phase-2 schema-DISCOVERY stress test: multiple surface PARAPHRASES per relation. The questions
#: still state the canonical relation ("works at"), so an engine must cluster these synonyms back to
#: one relation to answer -- the real test of schema discovery (Phase 1 used one phrase per relation,
#: so clustering was never exercised). Each set includes the canonical phrasing so a discovered
#: cluster can recover the query-matching label. Enabled via GOLDENGRAPH_BENCH_REL_PARAPHRASE=1.
_REL_PHRASINGS: dict[str, tuple[str, ...]] = {
    "works_at": ("works at", "is employed at", "is on staff at"),
    "located_in": ("located in", "is based in", "sits within"),
    "acquired": ("acquired", "took over", "bought out"),
    "authored": ("authored", "wrote", "penned"),
    "part_of": ("part of", "belongs to", "is a component of"),
}


def _render_relation(rel: str, rng: random.Random) -> str:
    """Canonical 'rel with spaces', or (when GOLDENGRAPH_BENCH_REL_PARAPHRASE is set) a random
    paraphrase from `_REL_PHRASINGS` -- exercising synonym clustering in schema discovery."""
    import os

    if os.environ.get("GOLDENGRAPH_BENCH_REL_PARAPHRASE", "") not in ("", "0", "false"):
        return rng.choice(_REL_PHRASINGS.get(rel, (rel.replace("_", " "),)))
    return rel.replace("_", " ")


def _edge_doc_id(src_id: str, rel: str, dst_id: str) -> str:
    """Stable document id that ENCODES the edge structure (canonical ids, never
    variant surfaces), so a pure-Python oracle can rebuild the graph from the
    corpus. `::` separates the three parts; entity ids use a single `:` at most
    (`gm:foo`), so the split is unambiguous."""
    return f"{src_id}::{rel}::{dst_id}"


def emit_gold_mentions(documents) -> list[tuple[str, str, str]]:
    """Gold mentions read directly off the generated engineered `Document`s -- two per edge-doc,
    `(entity_id, surface, doc_id)` for src and dst. The doc id encodes `src::rel::dst` (gold canonical
    ids) and the Document carries the rendered `src_surface`/`dst_surface`, so the mentions match EXACTLY
    what the build saw -- no rng replay, no drift at any ambiguity. Co-occurrence extras (`::N` suffix,
    4+ `::`-parts) and any non-edge docs are skipped, so run the corpus WITHOUT GOLDENGRAPH_BENCH_COOCCUR
    for a clean base-doc gold set."""
    out: list[tuple[str, str, str]] = []
    for d in documents:
        parts = d.id.split("::")
        if len(parts) != 3:          # not a base edge-doc (cooccur ::N extra / non-edge) -> skip
            continue
        src_id, dst_id = parts[0], parts[2]
        out.append((src_id, d.src_surface, d.id))
        out.append((dst_id, d.dst_surface, d.id))
    return out


def _question_text(start_mention: str, relation_chain: tuple[str, ...]) -> str:
    """Phrase a question that STATES the relation chain to follow -- the fix for the
    old "follow the chain" phrasing, which was unanswerable (a start node has several
    outgoing edges of different relations, so neither the path nor the hop count was
    determined). With the chain stated AND one edge per (entity, relation), the answer
    is unique."""
    steps = ", then ".join(rel.replace("_", " ") for rel in relation_chain)
    return (
        f"Starting from {start_mention}, follow the relation {steps}. "
        "What entity do you reach? Give its canonical name."
    )


def generate_engineered(
    *, seed: int, n_questions: int, ambiguity: float, max_hops: int = 4
) -> QACorpus:
    rng = random.Random(seed)
    entities = _load_entities()
    by_id = {e.id: e for e in entities}
    ids = [e.id for e in entities]

    # Deterministic typed-edge graph with AT MOST ONE edge per (entity, relation):
    # a relation sequence then determines a unique walk, which is what makes a
    # multi-hop question answerable. Each entity gets 2-4 DISTINCT relations.
    edges: dict[str, dict[str, str]] = {e.id: {} for e in entities}
    for e in entities:
        n = rng.randint(2, 4)
        rels = rng.sample(RELATION_SCHEMA, min(n, len(RELATION_SCHEMA)))
        for rel in rels:
            dst = rng.choice(ids)
            if dst != e.id:
                edges[e.id][rel] = dst

    # One document per edge stating the relation, with ambiguity-dialed mentions.
    # Iterate the (src, rel) map in a fixed order so the corpus is seed-deterministic.
    # GOLDENGRAPH_BENCH_COOCCUR renders each edge with EVERY phrasing (extra docs) so synonyms
    # co-occur on the same (subj,obj) pair -- the signal argument-context resolution needs. The BASE
    # doc keeps the unsuffixed `_edge_doc_id` (so question gold-support resolves) and is rendered on
    # the MAIN rng identically to the non-cooccur path (so the questions, sampled later on that rng,
    # stay byte-identical); the extra docs use a per-edge SIDE rng and a `::<i>` id suffix.
    import os as _os

    _cooccur = _os.environ.get("GOLDENGRAPH_BENCH_COOCCUR", "") not in ("", "0", "false")
    documents: list[Document] = []
    for src_id in ids:
        for rel, dst_id in edges[src_id].items():
            s = _render_mention(by_id[src_id], rng, ambiguity)
            o = _render_mention(by_id[dst_id], rng, ambiguity)
            documents.append(
                Document(
                    id=_edge_doc_id(src_id, rel, dst_id),
                    text=f"{s} {_render_relation(rel, rng)} {o}.",
                    src_surface=s,
                    dst_surface=o,
                )
            )
            if _cooccur:
                # ONE extra doc with a RANDOM phrasing (not all) -- so each edge shows 2 phrasings
                # (base + extra) that co-occur on its pair (the clustering signal), but the CANONICAL
                # word is absent from a fraction of edges (~4/9). Those canonical-free edges are
                # reachable only by clustering the synonyms (argctx), not by the canonical-label
                # default backend -- the discriminating case. Side rng so the main rng (questions)
                # is untouched.
                phr = _REL_PHRASINGS.get(rel, ())
                if phr:
                    side = random.Random(f"{seed}:{src_id}:{rel}:{dst_id}")
                    phrase = side.choice(phr)
                    s2 = _render_mention(by_id[src_id], side, ambiguity)
                    o2 = _render_mention(by_id[dst_id], side, ambiguity)
                    documents.append(
                        Document(
                            id=f"{_edge_doc_id(src_id, rel, dst_id)}::1",
                            text=f"{s2} {phrase} {o2}.",
                            src_surface=s2,
                            dst_surface=o2,
                        )
                    )

    # Sample k-hop questions by walking the edge graph and RECORDING the relation
    # sequence taken; the question states that sequence, the answer is the terminal
    # entity's canonical name, and the gold supporting facts are the traversed edges.
    questions: list[QAItem] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for qi in range(n_questions):
        k = rng.randint(1, max_hops)
        start = rng.choice(ids)
        cur = start
        chain: list[str] = []
        support: list[str] = []
        ok = True
        for _ in range(k):
            if not edges[cur]:
                ok = False
                break
            rel = rng.choice(list(edges[cur]))
            nxt = edges[cur][rel]
            chain.append(rel)
            support.append(_edge_doc_id(cur, rel, nxt))
            cur = nxt
        if not ok or len(chain) != k:
            continue
        # Skip duplicate (start, chain) walks: they ask the identical question, which
        # wastes LLM budget and skews the per-engine mean.
        dedup_key = (start, tuple(chain))
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        answer_ent = by_id[cur]
        start_mention = _render_mention(by_id[start], rng, ambiguity)
        relation_chain = tuple(chain)
        questions.append(
            QAItem(
                id=f"eng-q{qi}",
                question=_question_text(start_mention, relation_chain),
                gold_answer=answer_ent.canonical,
                gold_supporting_fact_ids=tuple(support),
                hop_count=k,
                ambiguity_level=ambiguity,
                start_entity_id=start,
                relation_chain=relation_chain,
            )
        )
    return QACorpus(
        name="engineered", documents=tuple(documents), questions=tuple(questions)
    )
