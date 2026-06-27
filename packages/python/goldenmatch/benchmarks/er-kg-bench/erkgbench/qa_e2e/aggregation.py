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


def passage_window_floor(docs, anchor_surfaces: set, relation: str, *,
                         passage_k: int, surface_to_canon: dict) -> set:
    """RAG-without-structure floor: the first `passage_k` docs mentioning ANY anchor
    surface, extract every entity-universe surface present -> canonical set. Recall
    is capped by the window (members past `passage_k` docs are unseen); precision is
    low (no relation filter). Deterministic, no embedder."""
    hits = [d for d in docs if any(a in d.text for a in anchor_surfaces)][:passage_k]
    out: set = set()
    for d in hits:
        for surf, canon in surface_to_canon.items():
            if surf in d.text:
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
    llm_setf1: dict | None = None  # opt-in real-LLM RAG set-F1 by bucket


def _ordered_buckets(d: dict) -> list[str]:
    """Populated buckets in _BUCKETS order (smallest -> largest)."""
    order = [f"{lo}-{hi}" for lo, hi in _BUCKETS]
    return [b for b in order if b in d]


def gate_verdicts(gg_setf1: dict, floor_setf1: dict, *, gg_threshold: float = 0.9,
                  widen_margin: float = 0.1) -> list[tuple[str, bool, bool]]:
    """[(label, passed, is_hard), ...]. All three are HARD."""
    buckets = _ordered_buckets(gg_setf1)
    size_inv = all(gg_setf1[b] >= gg_threshold for b in buckets)
    lo_b, hi_b = (buckets[0], buckets[-1]) if len(buckets) >= 2 else (None, None)
    collapse = bool(lo_b) and floor_setf1.get(hi_b, 0.0) < floor_setf1.get(lo_b, 0.0)
    gap_lo = gg_setf1.get(lo_b, 0.0) - floor_setf1.get(lo_b, 0.0) if lo_b else 0.0
    gap_hi = gg_setf1.get(hi_b, 0.0) - floor_setf1.get(hi_b, 0.0) if hi_b else 0.0
    widen = bool(lo_b) and (gap_hi - gap_lo) >= widen_margin
    return [
        (f"goldengraph set-F1 >= {gg_threshold} in every size bucket", size_inv, True),
        ("passage-floor set-F1 collapses (largest < smallest bucket)", collapse, True),
        ("the (goldengraph - floor) gap WIDENS with set size", widen, True),
    ]


def gate_exit_code(res: AggregationResult) -> int:
    failed = any(
        not passed for _l, passed, is_hard in gate_verdicts(res.gg_setf1, res.floor_setf1)
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
        "| size bucket | goldengraph set-F1 | floor set-F1 | gg count-acc |",
        "|---|---|---|---|",
    ]
    for b in buckets:
        lines.append(
            f"| {b} | {res.gg_setf1[b]:.3f} | {res.floor_setf1.get(b, 0.0):.3f} "
            f"| {res.gg_count_acc.get(b, 0.0):.3f} |"
        )
    if res.llm_setf1:
        lines += ["", "real-LLM RAG floor set-F1 (opt-in): " + ", ".join(
            f"{b}:{res.llm_setf1.get(b, 0.0):.3f}" for b in buckets)]
    lines += ["", "## verdicts", ""]
    for label, passed, _hard in gate_verdicts(res.gg_setf1, res.floor_setf1):
        lines.append(f"- [{'PASS' if passed else 'FAIL'}] {label}")
    return "\n".join(lines) + "\n"
