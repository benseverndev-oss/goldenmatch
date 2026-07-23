"""Real-world (Wikidata) capability corpus: a committed SPARQL-pull fixture turned
into the SAME `_Entity` / `Document` / `AggQuestion` types the synthetic aggregation
bench uses, so ALL scoring / floor / bucket / gate logic in `aggregation.py` is reused
unchanged. The only thing that changes is the data: real company names + aliases +
real subsidiary sets instead of the synthetic fan-out corpus.

The bench NEVER hits live Wikidata -- it reads the committed fixture. Only
`scripts/pull_wikidata_capability_fixture.py` touches the network (run by hand)."""
from __future__ import annotations

import json
import random
from pathlib import Path

from .engineered import _Entity

_FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_realworld_entities(fixture_path) -> list[_Entity]:
    """Load the committed Wikidata fixture into the harness's `_Entity` type.
    qid -> id (ground truth), canonical -> canonical, aliases -> variants
    (real name variation; canonical is never duplicated into variants)."""
    data = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    out = []
    for e in data["entities"]:
        variants = tuple(a for a in e.get("aliases", ()) if a != e["canonical"])
        out.append(_Entity(id=e["qid"], canonical=e["canonical"], variants=variants))
    return out


def generate_realworld_aggregation(fixture_path, *, ambiguity: float, seed: int):
    """Real-data drop-in for `generate_aggregation`: one Document per (anchor, member)
    edge with real aliases sampled by `ambiguity`, plus a list+count AggQuestion per
    fact. `facts` are pre-aggregated (one row per (anchor, relation)), so the
    (anchor_id, relation) uniqueness invariant holds by construction."""
    from .aggregation import AggQuestion
    from .corpora import Document
    from .engineered import _edge_doc_id, _render_mention

    data = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    ents = load_realworld_entities(fixture_path)
    by_id = {e.id: e for e in ents}
    rng = random.Random(seed)
    docs, qs = [], []
    for i, fact in enumerate(data["facts"]):
        src_id, rel = fact["anchor_qid"], fact["relation"]
        members = [m for m in fact["member_qids"] if m in by_id]
        rel_words = rel.replace("_", " ")
        for m in members:
            s = _render_mention(by_id[src_id], rng, ambiguity)
            o = _render_mention(by_id[m], rng, ambiguity)
            docs.append(Document(id=_edge_doc_id(src_id, rel, m),
                                 text=f"{s} {rel_words} {o}.",
                                 src_surface=s, dst_surface=o))
        canon = by_id[src_id].canonical
        qs.append(AggQuestion(id=f"rw-list-{i}", kind="list",
                              question=f"List all entities that {canon} {rel_words}.",
                              anchor_id=src_id, relation=rel,
                              gold_members=tuple(members), gold_count=len(members)))
        qs.append(AggQuestion(id=f"rw-count-{i}", kind="count",
                              question=f"How many entities does {canon} {rel_words}?",
                              anchor_id=src_id, relation=rel,
                              gold_members=tuple(members), gold_count=len(members)))
    return tuple(docs), qs


def run_realworld_aggregation(fixture_path, *, ambiguity: float, passage_k: int, llm=None):
    """Mirror of aggregation.run_aggregation_deterministic but sourced from the real
    fixture. All scoring/floor/bucket/gate logic is reused unchanged. Needs the native
    wheel (via ablation._build_store)."""
    from . import ablation, dials
    from .aggregation import (
        AggregationResult,
        _mean_by_bucket,
        agg_documents_corpus,
        count_accuracy,
        goldengraph_aggregate,
        llm_rag_aggregate,
        passage_window_floor,
        set_f1,
        size_bucket,
    )
    from .gold import GoldGraph

    docs, qs = generate_realworld_aggregation(fixture_path, ambiguity=ambiguity, seed=7)
    corpus = agg_documents_corpus(docs)
    g = GoldGraph.from_corpus(corpus)
    slice_graph, coverage = ablation._build_store(
        corpus, g, dials.oracle_keys(corpus, g), ablation._typ_of(g))
    s2c, anchor_surfaces = {}, {}
    for eid, surf, _typ in dials._entity_surfaces(g):
        s2c.setdefault(surf, eid)
        anchor_surfaces.setdefault(eid, set()).add(surf)
    gg_f1, floor_f1, floor_rec, gg_count, llm_f1 = [], [], [], [], []
    for q in (q for q in qs if q.kind == "list"):
        b = size_bucket(q.gold_count)
        gold = set(q.gold_members)
        a_surfs = anchor_surfaces.get(q.anchor_id, set())
        got = goldengraph_aggregate(slice_graph, coverage, q.anchor_id, q.relation)
        floor = passage_window_floor(docs, a_surfs, q.relation, passage_k=passage_k,
                                     surface_to_canon=s2c)
        gg_f1.append((b, set_f1(got, gold)["f1"]))
        fscore = set_f1(floor, gold)
        floor_f1.append((b, fscore["f1"]))
        floor_rec.append((b, fscore["recall"]))
        gg_count.append((b, count_accuracy(len(got), q.gold_count)))
        if llm is not None and not getattr(llm, "exhausted", False):
            rag = llm_rag_aggregate(docs, a_surfs, q.relation, passage_k=passage_k,
                                    surface_to_canon=s2c, llm=llm)
            llm_f1.append((b, set_f1(rag, gold)["f1"]))
    return AggregationResult(
        gg_setf1=_mean_by_bucket(gg_f1), floor_setf1=_mean_by_bucket(floor_f1),
        gg_count_acc=_mean_by_bucket(gg_count), floor_recall=_mean_by_bucket(floor_rec),
        llm_setf1=_mean_by_bucket(llm_f1) if llm_f1 else None)
