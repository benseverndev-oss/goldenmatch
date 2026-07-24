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


def _resolution_km(rows, *, resolve_mode: str) -> dict:
    """(qid, surface) -> record_key. The ONE thing Phase 1.5 swaps.

    `oracle` (Phase 0, DEFAULT): key = the ground-truth qid, so every surface of an
    entity shares a key and the store pre-merges all variants for free (entity
    resolution held ORACLE). `real`: key = a real goldenmatch `dedupe_df` CLUSTER over
    the `(surface, type)` universe -- the SAME zero-config resolver `resolve()` runs
    per document, applied here as ONE batch clustering of every surface. Surfaces
    goldenmatch judges the same entity share a cluster key -> the store merges them;
    variants goldenmatch fails to cluster stay fragmented, and distinct entities it
    wrongly collides get merged. So the `real` km folds REAL ER quality into the store,
    exactly the way the ablation `dials.goldengraph_keys` dial models goldengraph-level
    resolution -- the faithful, feasible realization of "swap the oracle record_keys for
    real resolver clustering" (the per-document `ingest_corpus` + O(N^2) cross-doc linker
    is infeasible at the fixture's ~13.5k edges and flaky on tiny data; a batch dedupe of
    the surface universe is the same goldenmatch resolver, deterministic and O(1) store
    passes). name+type is two fields, under the 3-field cross-encoder rerank trigger, so
    it stays fully offline."""
    if resolve_mode == "oracle":
        return {(eid, s): eid for eid, s, _t in rows}
    if resolve_mode != "real":
        raise ValueError(f"resolve_mode must be 'oracle' or 'real', got {resolve_mode!r}")
    import goldenmatch as gm
    import pyarrow as pa

    # goldenmatch is arrow-native (v3.0.0): dedupe_df takes a pa.Table and runs
    # POLARS-FREE -- required here, the goldengraph-pipeline lane has no polars
    # (do NOT switch to pl.DataFrame; ModuleNotFoundError there). name+type is two
    # fields, under the 3-field cross-encoder rerank trigger, so it stays offline.
    df = pa.table({"name": [s for _e, s, _t in rows], "type": [t for _e, _s, t in rows]})
    result = gm.dedupe_df(df)
    # DedupeResult.clusters may surface only multi-member clusters -> default each row to
    # its own singleton key first, then overwrite clustered rows with the shared cluster key.
    cluster_of: dict[int, str] = {i: f"s{i}" for i in range(len(rows))}
    for cid, info in result.clusters.items():
        for ri in info["members"]:
            cluster_of[int(ri)] = f"c{cid}"
    return {(rows[i][0], rows[i][1]): cluster_of[i] for i in range(len(rows))}


def _build_realworld_store_for_mode(fixture_path, *, ambiguity: float, seed: int = 7,
                                    resolve_mode: str = "oracle"):
    """Shared store build for both arms. Returns
    (slice_graph, coverage, docs, qs, anchor_surfaces, s2c_floor). The ONLY difference
    between arms is the `km` (see `_resolution_km`); the docs, coverage, floor keyspace,
    and everything downstream are identical, so the oracle-vs-real GG set-F1 delta
    isolates the entity-resolution contribution."""
    docs, qs = generate_realworld_aggregation(fixture_path, ambiguity=ambiguity, seed=seed)
    rows = _realworld_entity_surfaces(fixture_path)
    km = _resolution_km(rows, resolve_mode=resolve_mode)
    typ_of = {eid: t for eid, _s, t in rows}
    s2c_cov: dict = {}                                     # surface -> set(qid) (coverage)
    s2c_floor: dict = {}                                   # surface -> one qid (floor/anchor)
    anchor_surfaces: dict = {}
    for eid, surf, _t in rows:
        s2c_cov.setdefault(surf, set()).add(eid)
        s2c_floor.setdefault(surf, eid)
        anchor_surfaces.setdefault(eid, set()).add(surf)
    slice_graph, coverage = _build_realworld_store(docs, km, s2c_cov, typ_of)
    return slice_graph, coverage, docs, qs, anchor_surfaces, s2c_floor


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
        if len(parts) < 3:
            continue
        # Co-occurrence docs (Phase 2) carry a `::m{k}` mention suffix (same edge,
        # a different rendered alias per doc); take the first three components.
        src_id, rel, dst_id = parts[0], parts[1], parts[2]
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


def run_realworld_aggregation(fixture_path, *, ambiguity: float, passage_k: int, llm=None,
                              resolve_mode: str = "oracle"):
    """Mirror of aggregation.run_aggregation_deterministic but sourced from the real
    fixture. All scoring/floor/bucket/gate logic is reused unchanged. Needs the native
    wheel (via goldengraph_native.PyStore).

    `resolve_mode="oracle"` (DEFAULT, Phase 0) holds entity resolution ORACLE -- variants
    of an entity are pre-merged for free -- so the GG set-F1 isolates the aggregation /
    traversal capability. `resolve_mode="real"` (Phase 1.5) removes the oracle: the store
    must merge the alias variants ITSELF via goldenmatch's real resolver (see
    `_resolution_km`), so GG set-F1 folds in BOTH resolution correctness (variants merged)
    AND traversal completeness. The oracle-vs-real delta quantifies the ER contribution."""
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

    slice_graph, coverage, docs, qs, anchor_surfaces, s2c_floor = (
        _build_realworld_store_for_mode(
            fixture_path, ambiguity=ambiguity, seed=7, resolve_mode=resolve_mode
        )
    )
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


# --- Phase 2: co-occurrence corpus + ER-blind floor (compounded ER + aggregation) ------
# Phase 1.5 found the floor is ER-BLIND because (a) it resolves via the ground-truth
# surface->qid map and (b) each member appears in exactly ONE doc. Phase 2 fixes BOTH: a
# member recurs under SEVERAL real aliases across docs, and the floor must cluster those
# variants ITSELF (naive normalization) instead of getting the oracle map. The metric where
# it bites is COUNT ("how many members?"): goldenmatch's real dedup merges the aliases ->
# correct count; the ER-blind floor over-counts the un-mergeable ones. That is goldenmatch's
# core value (dedup) showing up inside the aggregation capability.
import re as _re


def _cooccurrence_surfaces(ent, k: int) -> list:
    """Up to `k` DISTINCT real surfaces for an entity (canonical + aliases), stable order."""
    surfaces = list(dict.fromkeys([ent.canonical, *ent.variants]))
    return surfaces[: max(1, k)]


def _word_mentions(text: str, surface: str) -> bool:
    """Whole-token containment, matching the aggregation floor's `_mentions`."""
    return _re.search(r"\b" + _re.escape(surface) + r"\b", text) is not None


def generate_realworld_cooccurrence(fixture_path, *, mentions_per_member: int = 3, seed: int = 7):
    """Co-occurrence drop-in for `generate_realworld_aggregation`: each (anchor, member) edge
    is rendered in UP TO `mentions_per_member` docs, EACH under a DISTINCT real alias of the
    member -- so a member recurs under several surfaces across docs. Doc ids carry a `::m{k}`
    suffix to stay unique (the store build takes the first three `::` components). gold_members
    / gold_count are the TRUE member set, unchanged. Deterministic per seed."""
    from .aggregation import AggQuestion
    from .corpora import Document
    from .engineered import _edge_doc_id

    data = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    ents = load_realworld_entities(fixture_path)
    by_id = {e.id: e for e in ents}
    _rng = random.Random(seed)  # reserved for future jitter; keeps signature stable
    docs, qs = [], []
    for i, fact in enumerate(data["facts"]):
        src_id, rel = fact["anchor_qid"], fact["relation"]
        members = [m for m in fact["member_qids"] if m in by_id]
        rel_words = rel.replace("_", " ")
        anchor_surfs = _cooccurrence_surfaces(by_id[src_id], mentions_per_member)
        for m in members:
            for k, o in enumerate(_cooccurrence_surfaces(by_id[m], mentions_per_member)):
                s = anchor_surfs[k % len(anchor_surfs)]
                docs.append(Document(id=f"{_edge_doc_id(src_id, rel, m)}::m{k}",
                                     text=f"{s} {rel_words} {o}.",
                                     src_surface=s, dst_surface=o))
        canon = by_id[src_id].canonical
        qs.append(AggQuestion(id=f"rwco-list-{i}", kind="list",
                              question=f"List all entities that {canon} {rel_words}.",
                              anchor_id=src_id, relation=rel,
                              gold_members=tuple(members), gold_count=len(members)))
        qs.append(AggQuestion(id=f"rwco-count-{i}", kind="count",
                              question=f"How many entities does {canon} {rel_words}?",
                              anchor_id=src_id, relation=rel,
                              gold_members=tuple(members), gold_count=len(members)))
    return tuple(docs), qs


def _normalize_surface(s: str) -> str:
    """Naive RAG-style surface normalization: lowercase + collapse whitespace + strip
    surrounding punctuation. Merges case/spacing variants but NOT real aliases (acronyms,
    legal-suffix words, transliterations) -- which is why it over-counts a member that
    recurs under several aliases."""
    return _re.sub(r"\s+", " ", s.strip().lower()).strip(".,;:'\"()")


def er_blind_floor_count(docs, anchor_surfaces: set, *, passage_k: int,
                         member_surfaces: set, normalize=_normalize_surface) -> int:
    """DISTINCT-member count the way an ER-BLIND RAG does: over the first `passage_k` window
    docs mentioning any anchor surface, collect every member surface present, cluster by
    `normalize` (NOT the oracle surface->qid map). A member under 3 un-mergeable aliases
    counts as 3. Contrast the oracle floor (surface->qid) which counts it once."""
    hits = [d for d in docs if any(_word_mentions(d.text, a) for a in anchor_surfaces)][:passage_k]
    anchor_norm = {normalize(a) for a in anchor_surfaces}
    forms: set = set()
    for d in hits:
        for surf in member_surfaces:
            if _word_mentions(d.text, surf):
                n = normalize(surf)
                if n not in anchor_norm:
                    forms.add(n)
    return len(forms)


def oracle_floor_count(docs, anchor_surfaces: set, *, passage_k: int,
                       surface_to_qid: dict) -> int:
    """Reference: the SAME window, but resolving each surface to its ground-truth qid
    (perfect ER). A member under any number of aliases counts ONCE. The
    oracle-vs-ER-blind gap is the ER contribution the graph's real dedup recovers."""
    hits = [d for d in docs if any(_word_mentions(d.text, a) for a in anchor_surfaces)][:passage_k]
    anchor_qids = {surface_to_qid.get(a) for a in anchor_surfaces}
    qids: set = set()
    for d in hits:
        for surf, qid in surface_to_qid.items():
            if _word_mentions(d.text, surf):
                qids.add(qid)
    return len(qids - anchor_qids - {None})


def run_realworld_cooccurrence(fixture_path, *, mentions_per_member: int = 3,
                               passage_k: int = 10, seed: int = 7):
    """Compounded ER + aggregation on the co-occurrence corpus. Per size bucket, reports
    COUNT accuracy for THREE arms of the 'how many members?' query:
      - goldengraph: builds the store from the co-occurrence docs with REAL goldenmatch ER
        (resolve_mode='real'), traverses, counts distinct store nodes.
      - oracle floor: window docs resolved surface->qid (perfect ER) -> exact count.
      - ER-blind floor: window docs clustered by naive normalization -> over-counts aliases.
    goldengraph's real dedup should beat the ER-blind floor (the compounded win) while the
    ER-blind floor collapses. Needs the native wheel (PyStore, via _build_realworld_store).
    Returns {'gg_count_acc', 'oracle_floor_count_acc', 'er_blind_count_acc'} by bucket."""
    from .aggregation import _mean_by_bucket, count_accuracy, goldengraph_aggregate, size_bucket

    docs, qs = generate_realworld_cooccurrence(
        fixture_path, mentions_per_member=mentions_per_member, seed=seed)
    rows = _realworld_entity_surfaces(fixture_path)
    km = _resolution_km(rows, resolve_mode="real")
    typ_of = {eid: t for eid, _s, t in rows}
    s2c_cov: dict = {}
    surface_to_qid: dict = {}
    anchor_surfaces: dict = {}
    member_surfaces_all: set = set()
    for eid, surf, _t in rows:
        s2c_cov.setdefault(surf, set()).add(eid)
        surface_to_qid.setdefault(surf, eid)
        anchor_surfaces.setdefault(eid, set()).add(surf)
        member_surfaces_all.add(surf)
    slice_graph, coverage = _build_realworld_store(docs, km, s2c_cov, typ_of)

    gg, orc, erb = [], [], []
    for q in (q for q in qs if q.kind == "list"):
        b = size_bucket(q.gold_count)
        a_surfs = anchor_surfaces.get(q.anchor_id, set())
        gg_n = len(goldengraph_aggregate(slice_graph, coverage, q.anchor_id, q.relation))
        orc_n = oracle_floor_count(docs, a_surfs, passage_k=passage_k,
                                   surface_to_qid=surface_to_qid)
        erb_n = er_blind_floor_count(docs, a_surfs, passage_k=passage_k,
                                     member_surfaces=member_surfaces_all - a_surfs)
        gg.append((b, count_accuracy(gg_n, q.gold_count)))
        orc.append((b, count_accuracy(orc_n, q.gold_count)))
        erb.append((b, count_accuracy(erb_n, q.gold_count)))
    return {
        "gg_count_acc": _mean_by_bucket(gg),
        "oracle_floor_count_acc": _mean_by_bucket(orc),
        "er_blind_count_acc": _mean_by_bucket(erb),
    }
