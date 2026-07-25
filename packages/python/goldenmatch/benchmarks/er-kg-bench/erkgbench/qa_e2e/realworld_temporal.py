"""Real-world (Wikidata) TEMPORAL as-of capability: a committed CEO-history fixture turned
into the SAME `TemporalFact` / `TemporalQuestion` types the synthetic temporal bench uses,
so ALL store build / as-of traversal / temporal-blind floor / scoring in `temporal.py` are
reused unchanged. Only the DATA changes: real companies whose CEO was later corrected
(P169 with P580/P582 date qualifiers) instead of the synthetic bi-temporal corpus.

The capability: `store.as_of(D)` answers 'who was CEO as of a PAST year' correctly after a
succession; a temporal-blind RAG returns the most-recent/last-mentioned CEO -> wrong on past
queries. Entity resolution is held ORACLE here (surfaces == canonical names), exactly like
the synthetic temporal bench -- this slice isolates the TEMPORAL capability, orthogonal to
the ER capability the aggregation slices measure.

The bench NEVER hits live Wikidata -- it reads the committed fixture. Only
`scripts/pull_wikidata_temporal_fixture.py` touches the network (run by hand).
"""
from __future__ import annotations

import json
import random
from pathlib import Path

_FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_temporal_entities(fixture_path) -> dict:
    """qid -> canonical name over the temporal fixture."""
    data = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    return {e["qid"]: e["canonical"] for e in data["entities"]}


def generate_realworld_temporal(fixture_path, *, seed: int = 7):
    """Real-data drop-in for `temporal.generate_temporal`: per succession fact, two dated
    passages ('As of {start_a}, {company} chief executive officer {ceo_a}.' / 'From {tc},
    ... {ceo_b}.') + a PAST question (date before the succession -> gold = the earlier CEO)
    and a CURRENT question (recent date -> gold = the later CEO). Surfaces are canonical
    names (ER held oracle). Returns (docs, facts, qs) with the same types
    `temporal.build_temporal_store` / scoring consume."""
    from .corpora import Document
    from .temporal import T1, TemporalFact, TemporalQuestion

    data = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    canon = {e["qid"]: e["canonical"] for e in data["entities"]}
    current_year = int(data.get("meta", {}).get("current_year", 2026))
    _rng = random.Random(seed)  # reserved; keeps signature stable
    docs, facts, qs = [], [], []
    for i, tf in enumerate(data["temporal_facts"]):
        src, rel = tf["anchor_qid"], tf["relation"]
        a, b, tc = tf["a_qid"], tf["b_qid"], int(tf["tc"])
        if src not in canon or a not in canon or b not in canon:
            continue
        start_a = int(tf.get("start_a", T1))
        rel_words = rel.replace("_", " ")
        facts.append(TemporalFact(src, rel, a, b, tc))
        # Two dated passages a real RAG reads; nothing enforces a slice.
        docs.append(Document(
            id=f"{src}::{rel}::{a}::t{start_a}",
            text=f"As of {start_a}, {canon[src]} {rel_words} {canon[a]}.",
            src_surface=canon[src], dst_surface=canon[a]))
        docs.append(Document(
            id=f"{src}::{rel}::{b}::t{tc}",
            text=f"From {tc}, {canon[src]} {rel_words} {canon[b]}.",
            src_surface=canon[src], dst_surface=canon[b]))
        # build_temporal_store makes the a-edge valid [T1, tc) and the b-edge [tc, inf);
        # a PAST date strictly before the succession -> a, a CURRENT date >= tc -> b.
        d_past = tc - 1
        d_cur = max(tc, current_year)
        for tag, D, regime, gold in (("p", d_past, "past", a), ("c", d_cur, "current", b)):
            qs.append(TemporalQuestion(
                id=f"rwtmp-{i}-{tag}",
                question=f"As of {D}, who is the {rel_words} of {canon[src]}?",
                anchor_id=src, relation=rel, D=D, regime=regime, gold_obj=gold))
    return tuple(docs), facts, qs


def run_realworld_temporal(fixture_path, *, seed: int = 7, llm=None):
    """Mirror of `temporal.run_temporal_deterministic` over the real fixture. Reuses the
    bi-temporal store build, the as-of traversal, and the temporal-blind floor verbatim;
    only the surfaces come from the real entities (canonical == oracle id) instead of the
    synthetic `dials`. Needs the native wheel (via build_temporal_store). Returns a
    `temporal.TemporalResult` (gg_acc / floor_acc by regime)."""
    from .temporal import (
        TemporalResult,
        _mean_by_regime,
        as_of_accuracy,
        build_temporal_store,
        goldengraph_asof,
        llm_temporal_rag,
        temporal_blind_floor,
    )

    docs, facts, qs = generate_realworld_temporal(fixture_path, seed=seed)
    store = build_temporal_store(facts)
    canon = load_temporal_entities(fixture_path)
    s2c = {name: qid for qid, name in canon.items()}          # oracle: surface -> qid
    anchor_surf = {qid: {name} for qid, name in canon.items()}

    gg, floor, llm_acc = [], [], []
    for q in qs:
        a_surfs = anchor_surf.get(q.anchor_id, set())
        gg.append((q.regime, as_of_accuracy(
            goldengraph_asof(store, q.anchor_id, q.relation, q.D), q.gold_obj)))
        floor.append((q.regime, as_of_accuracy(
            temporal_blind_floor(docs, a_surfs, q.relation, q.D, surface_to_canon=s2c),
            q.gold_obj)))
        if llm is not None and not getattr(llm, "exhausted", False):
            llm_acc.append((q.regime, as_of_accuracy(
                llm_temporal_rag(docs, a_surfs, q.relation, q.D, llm, surface_to_canon=s2c),
                q.gold_obj)))
    return TemporalResult(
        gg_acc=_mean_by_regime(gg),
        floor_acc=_mean_by_regime(floor),
        llm_acc=_mean_by_regime(llm_acc) if llm_acc else None,
    )
