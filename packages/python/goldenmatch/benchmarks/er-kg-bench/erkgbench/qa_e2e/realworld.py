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


def _realworld_entity_surfaces(fixture_path):
    """[(qid, surface, "concept"), ...] over the FIXTURE universe (canonical + aliases).

    DEVIATION from the plan: `dials._entity_surfaces` / `oracle_keys` / `surface_to_canon`
    / `ablation._typ_of` all read the SYNTHETIC `dataset/concepts.jsonl`, NOT the corpus
    or gold graph -- so they cannot key a real fixture (`km[(qid, real_surface)]` KeyErrors).
    The real generator derives the keyspace from the fixture entities instead. `typ` is a
    uniform "concept" (matching the synthetic default `typ_of.get(id, "concept")`); the
    aggregation traversal filters by predicate, not type."""
    out = []
    for e in load_realworld_entities(fixture_path):
        for s in dict.fromkeys([e.canonical, *e.variants]):
            out.append((e.id, s, "concept"))
    return out


def _build_realworld_store(docs, km, s2c_cov, typ_of):
    """Build the native store from the real edge docs, mirroring
    `ablation._build_store_obj` but with FIXTURE-derived coverage (that function's
    coverage is hard-wired to `dials.surface_to_canon`, i.e. the synthetic universe).
    Returns (slice_graph, coverage: store_entity_id -> set(qid))."""
    import json

    from goldengraph.extract import Extraction, Mention, Relationship
    from goldengraph.ingest import build_batch
    from goldengraph.resolve import ResolvedEntity
    from goldengraph_native import _native as ggn

    from .engines.goldengraph import _AS_OF

    store = ggn.PyStore()
    at = 0
    for d in docs:
        parts = d.id.split("::")
        if len(parts) != 3:
            continue
        src_id, rel, dst_id = parts
        at += 1
        s_surf, o_surf = d.src_surface, d.dst_surface
        extraction = Extraction(
            mentions=[
                Mention(name=s_surf, typ=typ_of.get(src_id, "concept")),
                Mention(name=o_surf, typ=typ_of.get(dst_id, "concept")),
            ],
            relationships=[Relationship(subj=0, predicate=rel, obj=1)],
        )
        entities = [
            ResolvedEntity(local_id=0, canonical_name=s_surf,
                           typ=typ_of.get(src_id, "concept"), surface_names=[s_surf],
                           record_keys=[km[(src_id, s_surf)]], member_idx=[0]),
            ResolvedEntity(local_id=1, canonical_name=o_surf,
                           typ=typ_of.get(dst_id, "concept"), surface_names=[o_surf],
                           record_keys=[km[(dst_id, o_surf)]], member_idx=[1]),
        ]
        store.append(json.dumps(build_batch(extraction, entities, at=at)))
    slice_graph = store.as_of(_AS_OF, _AS_OF)
    coverage: dict[int, set] = {}
    for e in slice_graph.entities():
        cov: set = set()
        for s in e.get("surface_names", ()):
            cov |= s2c_cov.get(s, set())
        coverage[e["entity_id"]] = cov
    return slice_graph, coverage


def run_realworld_aggregation(fixture_path, *, ambiguity: float, passage_k: int, llm=None):
    """Mirror of aggregation.run_aggregation_deterministic but sourced from the real
    fixture. All scoring/floor/bucket/gate logic is reused unchanged. Needs the native
    wheel (via goldengraph_native.PyStore)."""
    from .aggregation import (
        AggregationResult,
        _mean_by_bucket,
        count_accuracy,
        goldengraph_aggregate,
        llm_rag_aggregate,
        passage_window_floor,
        set_f1,
        size_bucket,
    )

    docs, qs = generate_realworld_aggregation(fixture_path, ambiguity=ambiguity, seed=7)
    rows = _realworld_entity_surfaces(fixture_path)
    km = {(eid, s): eid for eid, s, _t in rows}            # oracle keys: surface -> its qid
    typ_of = {eid: t for eid, _s, t in rows}
    s2c_cov: dict = {}                                     # surface -> set(qid) (coverage)
    s2c_floor: dict = {}                                   # surface -> one qid (floor/anchor)
    anchor_surfaces: dict = {}
    for eid, surf, _t in rows:
        s2c_cov.setdefault(surf, set()).add(eid)
        s2c_floor.setdefault(surf, eid)
        anchor_surfaces.setdefault(eid, set()).add(surf)
    slice_graph, coverage = _build_realworld_store(docs, km, s2c_cov, typ_of)
    s2c = s2c_floor
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
