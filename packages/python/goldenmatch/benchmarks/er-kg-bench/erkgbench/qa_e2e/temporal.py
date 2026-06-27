"""Temporal as_of capability bench (slice B2). A bi-temporal corpus + goldengraph
store.as_of(D) traversal vs a temporal-blind passage floor. The KG does what RAG
can't: answer 'as of a PAST date' correctly when a fact was later corrected."""
from __future__ import annotations

import json
import random
import re
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


# --- temporal-blind floor + metric + gate + render (wheel-free) ---


def _mentions(text: str, surface: str) -> bool:
    return re.search(r"\b" + re.escape(surface) + r"\b", text) is not None


def temporal_blind_floor(docs, anchor_surfaces: set, relation: str, D: int, *,
                         surface_to_canon: dict) -> str | None:
    """RAG-without-a-temporal-axis: among docs mentioning the anchor AND the relation
    phrase, take the LAST in doc order (corrections appended after originals) and
    return its non-anchor object. Ignores D -> wrong on past-date queries."""
    rel_words = relation.replace("_", " ")
    hits = [d for d in docs
            if any(_mentions(d.text, a) for a in anchor_surfaces) and rel_words in d.text]
    if not hits:
        return None
    d = hits[-1]  # latest-mentioned (temporal-blind)
    anchor_canons = {surface_to_canon.get(a) for a in anchor_surfaces}
    for surf, canon in surface_to_canon.items():
        if canon not in anchor_canons and _mentions(d.text, surf):
            return canon
    return None


def as_of_accuracy(predicted_obj, gold_obj) -> float:
    return 1.0 if predicted_obj == gold_obj else 0.0


@dataclass
class TemporalResult:
    gg_acc: dict        # regime -> mean goldengraph as_of-accuracy
    floor_acc: dict     # regime -> mean temporal-blind floor accuracy
    llm_acc: dict | None = None


def gate_verdicts(gg_acc: dict, floor_acc: dict, *, gg_threshold: float = 0.9,
                  past_gap_margin: float = 0.5) -> list[tuple[str, bool, bool]]:
    """[(label, passed, is_hard), ...]. Expected gg = 1.0 both regimes (right by
    construction); >=0.9 is slack. The capability is the PAST-regime gap (the floor
    returns the corrected value -> ~0 on past)."""
    both = all(gg_acc.get(r, 0.0) >= gg_threshold for r in ("past", "current"))
    past_gap = (gg_acc.get("past", 0.0) - floor_acc.get("past", 0.0)) >= past_gap_margin
    floor_current_ok = floor_acc.get("current", 0.0) >= 0.5
    return [
        (f"goldengraph as_of-accuracy >= {gg_threshold} in BOTH regimes (respects "
         "valid-time)", both, True),
        (f"goldengraph beats the temporal-blind floor by >= {past_gap_margin} on PAST "
         "queries (RAG can't answer 'as of a past date')", past_gap, True),
        ("floor is OK on the current regime (it's temporal-blind, not broken) (soft)",
         floor_current_ok, False),
    ]


def gate_exit_code(res: TemporalResult) -> int:
    return 1 if any(not p for _l, p, h in gate_verdicts(res.gg_acc, res.floor_acc) if h) else 0


def render_temporal_md(res: TemporalResult) -> str:
    has_llm = res.llm_acc is not None
    header = ("| regime | goldengraph | floor | llm-rag |" if has_llm
              else "| regime | goldengraph | floor |")
    sep = ("|---|---|---|---|" if has_llm else "|---|---|---|")
    lines = ["# GoldenGraph temporal as_of -- KG vs temporal-blind floor", "",
             "as_of-accuracy by regime (past = ask about a corrected-away value).", "",
             header, sep]
    for r in ("past", "current"):
        la = f" {res.llm_acc.get(r, 0.0):.3f} |" if has_llm else ""
        lines.append(f"| {r} | {res.gg_acc.get(r, 0.0):.3f} | {res.floor_acc.get(r, 0.0):.3f} |{la}")
    lines += ["", "## verdicts", ""]
    for label, passed, is_hard in gate_verdicts(res.gg_acc, res.floor_acc):
        tag = "PASS" if passed else ("FAIL" if is_hard else "WARN")
        lines.append(f"- [{tag}] {label}")
    return "\n".join(lines) + "\n"


# --- store build + goldengraph as_of traversal (needs the native wheel) ---

_BIG_TX = 10**12


def build_temporal_store(facts):
    """Build a bi-temporal store from the facts: per fact ONE batch with X-rel-A
    [T1,tc) and X-rel-B [tc,inf). Oracle record_keys (= canonical id) so X merges
    across facts/relations. Hand-built JSON (build_batch can't set valid_to).

    LOAD-BEARING: surface_names=[<id>] is REQUIRED -- the store recomputes
    canonical_name as the longest surface (ignoring the batch canonical_name field),
    so surface_names=[id] makes canonical_name==id, which goldengraph_asof relies on
    to map view-entity-id -> the gold canonical id. Do not drop it."""
    from goldengraph_native import _native as ggn

    store = ggn.PyStore()
    for f in facts:
        batch = {
            "entities": [
                {"local_id": 0, "canonical_name": f.anchor_id, "typ": "concept",
                 "surface_names": [f.anchor_id], "record_keys": [f.anchor_id]},
                {"local_id": 1, "canonical_name": f.a_id, "typ": "concept",
                 "surface_names": [f.a_id], "record_keys": [f.a_id]},
                {"local_id": 2, "canonical_name": f.b_id, "typ": "concept",
                 "surface_names": [f.b_id], "record_keys": [f.b_id]},
            ],
            "edges": [
                {"subj_local": 0, "predicate": f.relation, "obj_local": 1,
                 "valid_from": T1, "valid_to": f.tc, "source_refs": []},
                {"subj_local": 0, "predicate": f.relation, "obj_local": 2,
                 "valid_from": f.tc, "valid_to": None, "source_refs": []},
            ],
            "ingested_at": 1,
        }
        store.append(json.dumps(batch))
    return store


def goldengraph_asof(store, anchor_id: str, relation: str, D: int) -> str | None:
    """Exact as_of traversal: slice the store at valid_t=D, seed the anchor, 1-hop,
    filter edges by predicate -> the single object whose valid window contains D."""
    slice_g = store.as_of(D, _BIG_TX)
    # canonical_name == the canonical id here (build_temporal_store sets
    # surface_names=[id] -> store recomputes canonical_name = id), so map directly.
    id_to_canon = {e["entity_id"]: e["canonical_name"] for e in slice_g.entities()}
    seed = next((eid for eid, c in id_to_canon.items() if c == anchor_id), None)
    if seed is None:
        return None
    ball = slice_g.query([seed], 1)
    objs = {id_to_canon.get(e["obj"]) for e in ball.get("edges", ())
            if e["subj"] == seed and e["predicate"] == relation}
    objs.discard(None)
    objs.discard(anchor_id)
    return next(iter(objs), None) if len(objs) <= 1 else next(iter(objs))
