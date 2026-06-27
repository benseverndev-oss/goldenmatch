"""Aggregation/set/count capability bench (slice B1). A fan-out corpus + goldengraph
exact traversal vs a deterministic passage-window floor. The KG does what RAG can't:
exact set aggregation, size-invariant; the window's recall collapses with set size."""
from __future__ import annotations

import random
import re
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


def goldengraph_aggregate(slice_graph, coverage, anchor_id: str, relation: str) -> set:
    """Exact traversal: seed the anchor's store node (invert coverage), pull its
    1-hop ball, filter edges to `relation` with subj == anchor node, map obj nodes
    -> covered canonical members. No LLM, no embedder; size-invariant."""
    node_of_canon: dict = {}
    for nid in sorted(coverage):  # ascending id -> deterministic
        for c in coverage[nid]:
            node_of_canon.setdefault(c, nid)
    seed = node_of_canon.get(anchor_id)
    if seed is None:
        return set()
    ball = slice_graph.query([seed], 1)
    members: set = set()
    for e in ball.get("edges", ()):
        if e["subj"] == seed and e["predicate"] == relation:
            members |= coverage.get(e["obj"], set())
    members.discard(anchor_id)  # never count the anchor itself
    return members


def _mentions(text: str, surface: str) -> bool:
    """Whole-token(s) match -- NOT substring. Critical: substring matching makes a
    short surface (`M1`) spuriously hit a longer one (`M10`) and surfaces hit inside
    unrelated words, which destroys floor precision and masks the recall-collapse
    signal. Word boundaries fix both (a multi-word surface like `Dice coefficient`
    still matches as a phrase)."""
    return re.search(r"\b" + re.escape(surface) + r"\b", text) is not None


def passage_window_floor(docs, anchor_surfaces: set, relation: str, *,
                         passage_k: int, surface_to_canon: dict) -> set:
    """RAG-without-structure floor: the first `passage_k` docs mentioning ANY anchor
    surface (whole-token), extract every entity-universe surface present -> canonical
    set. Recall is capped by the window (members past `passage_k` docs are unseen);
    no relation filter. Deterministic, no embedder."""
    hits = [d for d in docs if any(_mentions(d.text, a) for a in anchor_surfaces)][:passage_k]
    out: set = set()
    for d in hits:
        for surf, canon in surface_to_canon.items():
            if _mentions(d.text, surf):
                out.add(canon)
    for a in anchor_surfaces:  # the anchor is not a member of its own set
        out.discard(surface_to_canon.get(a))
    out.discard(None)
    return out


@dataclass
class AggregationResult:
    gg_setf1: dict        # size_bucket -> mean goldengraph set-F1
    floor_setf1: dict     # size_bucket -> mean passage-floor set-F1
    gg_count_acc: dict    # size_bucket -> mean goldengraph count-accuracy
    floor_recall: dict | None = None  # size_bucket -> mean floor recall (window effect)
    llm_setf1: dict | None = None  # opt-in real-LLM RAG set-F1 by bucket


def _ordered_buckets(d: dict) -> list[str]:
    """Populated buckets in _BUCKETS order (smallest -> largest)."""
    order = [f"{lo}-{hi}" for lo, hi in _BUCKETS]
    return [b for b in order if b in d]


def gate_verdicts(gg_setf1: dict, floor_setf1: dict, floor_recall: dict | None = None,
                  *, gg_threshold: float = 0.9, gap_margin: float = 0.3,
                  recall_collapse_margin: float = 0.1) -> list[tuple[str, bool, bool]]:
    """[(label, passed, is_hard), ...].

    The capability signature MEASURED: goldengraph exact traversal is ~1.0 and
    size-invariant; the passage-window floor scores far lower at EVERY size because
    it can't filter by relation or direction (structure-blindness -- size-independent)
    AND its recall falls as the set outgrows the window. So the HARD signal is a large
    CONSISTENT gap (not a widening one -- the dominant floor failure is precision, not
    recall). The recall collapse is reported as a SOFT window-effect detail."""
    buckets = _ordered_buckets(gg_setf1)
    size_inv = all(gg_setf1[b] >= gg_threshold for b in buckets)
    big_gap = all((gg_setf1[b] - floor_setf1.get(b, 0.0)) >= gap_margin for b in buckets)
    verdicts = [
        (f"goldengraph set-F1 >= {gg_threshold} in every size bucket (exact, "
         "size-invariant)", size_inv, True),
        (f"goldengraph beats the passage-floor by >= {gap_margin} set-F1 in every "
         "bucket (RAG can't aggregate a structured set)", big_gap, True),
    ]
    if floor_recall:
        rb = _ordered_buckets(floor_recall)
        collapse = len(rb) >= 2 and (
            floor_recall[rb[0]] - floor_recall[rb[-1]]) >= recall_collapse_margin
        verdicts.append(
            ("floor RECALL collapses as the set outgrows the window (soft)",
             collapse, False))
    return verdicts


def gate_exit_code(res: AggregationResult) -> int:
    failed = any(
        not passed for _l, passed, is_hard in
        gate_verdicts(res.gg_setf1, res.floor_setf1, res.floor_recall)
        if is_hard
    )
    return 1 if failed else 0


def render_aggregation_md(res: AggregationResult) -> str:
    buckets = _ordered_buckets(res.gg_setf1)
    lines = [
        "# GoldenGraph aggregation/set/count -- KG vs passage-window floor",
        "",
        "Set-F1 of the recovered member set vs gold, by gold-set-size bucket. The KG",
        "does what RAG can't: exact traversal is size-invariant; a passage window's",
        "recall collapses as the set outgrows it.",
        "",
        "| size bucket | goldengraph set-F1 | floor set-F1 | floor recall | gg count-acc |",
        "|---|---|---|---|---|",
    ]
    fr = res.floor_recall or {}
    for b in buckets:
        lines.append(
            f"| {b} | {res.gg_setf1[b]:.3f} | {res.floor_setf1.get(b, 0.0):.3f} "
            f"| {fr.get(b, 0.0):.3f} | {res.gg_count_acc.get(b, 0.0):.3f} |"
        )
    if res.llm_setf1:
        lines += ["", "real-LLM RAG floor set-F1 (opt-in): " + ", ".join(
            f"{b}:{res.llm_setf1.get(b, 0.0):.3f}" for b in buckets)]
    lines += ["", "## verdicts", ""]
    for label, passed, is_hard in gate_verdicts(res.gg_setf1, res.floor_setf1,
                                                res.floor_recall):
        tag = "PASS" if passed else ("FAIL" if is_hard else "WARN")
        lines.append(f"- [{tag}] {label}")
    return "\n".join(lines) + "\n"


def llm_rag_aggregate(docs, anchor_surfaces: set, relation: str, *, passage_k: int,
                      surface_to_canon: dict, llm) -> set:
    """Realistic RAG floor (opt-in, real LLM): the first `passage_k` anchor-mentioning
    docs + 'list every entity that X <relation>'. Output names mapped back to
    canonical ids via `surface_to_canon` (unknown lines dropped). Same window cap as
    the deterministic floor, so its recall collapses with set size too."""
    hits = [d for d in docs if any(_mentions(d.text, a) for a in anchor_surfaces)][:passage_k]
    passages = "\n".join(f"- {d.text}" for d in hits) or "(no passages)"
    rel_words = relation.replace("_", " ")
    prompt = (
        f"From the passages below, list EVERY entity that the subject {rel_words}. "
        "One entity name per line, nothing else.\n\n" + passages
    )
    out: set = set()
    for line in llm.complete(prompt).splitlines():
        name = line.strip().lstrip("-* ").strip()
        if name in surface_to_canon:
            out.add(surface_to_canon[name])
    for a in anchor_surfaces:
        out.discard(surface_to_canon.get(a))
    out.discard(None)
    return out


def _mean_by_bucket(pairs) -> dict:
    """pairs: iterable of (bucket, value) -> {bucket: mean}."""
    agg: dict = {}
    for b, v in pairs:
        agg.setdefault(b, []).append(v)
    return {b: sum(v) / len(v) for b, v in agg.items()}


def run_aggregation_deterministic(*, seed: int, n_anchors: int, ambiguity: float,
                                  passage_k: int, llm=None) -> AggregationResult:
    """Build the fan-out corpus + oracle store; per list-question score goldengraph
    exact traversal and the passage-window floor by gold-set-size bucket. Needs the
    native wheel (via ablation._build_store). If `llm` is given, ALSO score the
    realistic real-LLM RAG floor (budget-gated via `llm.exhausted` if present)."""
    from . import ablation, dials
    from .gold import GoldGraph

    docs, qs = generate_aggregation(seed=seed, n_anchors=n_anchors, ambiguity=ambiguity)
    corpus = agg_documents_corpus(docs)
    g = GoldGraph.from_corpus(corpus)
    slice_graph, coverage = ablation._build_store(
        corpus, g, dials.oracle_keys(corpus, g), ablation._typ_of(g)
    )
    # surface -> canonical (first-wins) + anchor_id -> its surfaces, for the floor.
    s2c: dict = {}
    anchor_surfaces: dict = {}
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
        fscore = set_f1(floor, gold)
        gg_f1.append((b, set_f1(got, gold)["f1"]))
        floor_f1.append((b, fscore["f1"]))
        floor_rec.append((b, fscore["recall"]))
        gg_count.append((b, count_accuracy(len(got), q.gold_count)))
        if llm is not None and not getattr(llm, "exhausted", False):
            rag = llm_rag_aggregate(docs, a_surfs, q.relation, passage_k=passage_k,
                                    surface_to_canon=s2c, llm=llm)
            llm_f1.append((b, set_f1(rag, gold)["f1"]))
    return AggregationResult(
        gg_setf1=_mean_by_bucket(gg_f1),
        floor_setf1=_mean_by_bucket(floor_f1),
        gg_count_acc=_mean_by_bucket(gg_count),
        floor_recall=_mean_by_bucket(floor_rec),
        llm_setf1=_mean_by_bucket(llm_f1) if llm_f1 else None,
    )
