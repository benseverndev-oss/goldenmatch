"""Temporal as_of capability bench (slice B2). A bi-temporal corpus + goldengraph
store.as_of(D) traversal vs a temporal-blind passage floor. The KG does what RAG
can't: answer 'as of a PAST date' correctly when a fact was later corrected."""
from __future__ import annotations

import random
from dataclasses import dataclass

from .corpora import Document
from .engineered import RELATION_SCHEMA, _load_entities, _render_mention

T1 = 1            # valid_from of every original edge
_TMAX = 100       # query/date horizon
_N_ANCHORS = 20   # first N entities are anchors; the rest are objects (disjoint)


@dataclass(frozen=True)
class TemporalFact:
    anchor_id: str
    relation: str
    a_id: str     # original object (valid [T1, tc))
    b_id: str     # corrected object (valid [tc, inf))
    tc: int       # correction valid-time


@dataclass(frozen=True)
class TemporalQuestion:
    id: str
    question: str
    anchor_id: str
    relation: str
    D: int
    regime: str    # "past" | "current"
    gold_obj: str  # canonical id of the object true at D


def generate_temporal(*, seed: int, n_facts: int, ambiguity: float):
    rng = random.Random(seed)
    ents = _load_entities()
    by_id = {e.id: e for e in ents}
    ids = [e.id for e in ents]
    anchors = ids[:_N_ANCHORS]
    objects = ids[_N_ANCHORS:]
    docs: list[Document] = []
    facts: list[TemporalFact] = []
    qs: list[TemporalQuestion] = []
    for i in range(n_facts):
        src_id = anchors[i % len(anchors)]
        rel = RELATION_SCHEMA[(i // len(anchors)) % len(RELATION_SCHEMA)]  # B1 outer cycle
        a_id, b_id = rng.sample(objects, 2)
        tc = rng.randint(20, 80)
        facts.append(TemporalFact(src_id, rel, a_id, b_id, tc))
        rel_words = rel.replace("_", " ")
        # two source passages (a real RAG reads these; nothing enforces a slice)
        xs = _render_mention(by_id[src_id], rng, ambiguity)
        docs.append(Document(
            id=f"{src_id}::{rel}::{a_id}::t{T1}",
            text=f"As of {T1}, {xs} {rel_words} {_render_mention(by_id[a_id], rng, ambiguity)}.",
            src_surface=xs, dst_surface=by_id[a_id].canonical))
        xs2 = _render_mention(by_id[src_id], rng, ambiguity)
        docs.append(Document(
            id=f"{src_id}::{rel}::{b_id}::t{tc}",
            text=f"From {tc}, {xs2} {rel_words} {_render_mention(by_id[b_id], rng, ambiguity)}.",
            src_surface=xs2, dst_surface=by_id[b_id].canonical))
        # one past + one current question per fact
        d_past = rng.randint(T1, tc - 1)
        d_cur = rng.randint(tc, _TMAX)
        for tag, D, regime, gold in (("p", d_past, "past", a_id), ("c", d_cur, "current", b_id)):
            qs.append(TemporalQuestion(
                id=f"tmp-{i}-{tag}",
                question=f"As of {D}, what does {by_id[src_id].canonical} {rel_words}?",
                anchor_id=src_id, relation=rel, D=D, regime=regime, gold_obj=gold))
    return tuple(docs), facts, qs
