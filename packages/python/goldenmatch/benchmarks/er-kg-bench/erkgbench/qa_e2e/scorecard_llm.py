"""Real-LLM scorecard rows (Phase 2 of slice A): extraction-F1, synthesis-given-gold,
and the 4-dial answer-match ablation matched to bridge-recall. Opt-in, budget-capped,
NON-gating -- the deterministic bridge-recall gate (#1274) stays the blocking signal."""
from __future__ import annotations

from . import metrics


def _norm(s: str) -> str:
    return metrics._normalize(s)


def extraction_counts(gold_src: str, gold_dst: str, extraction) -> dict:
    """Per-doc entity + (existence-based) relation TP/FP/FN of `extraction` vs the
    one gold edge {gold_src, gold_dst}. Predicate label ignored; edge counted in
    either direction."""
    gold_ents = {_norm(gold_src), _norm(gold_dst)}
    got_ents = {_norm(m.name) for m in extraction.mentions}
    ent_tp = len(gold_ents & got_ents)
    ent_fp = len(got_ents - gold_ents)
    ent_fn = len(gold_ents - got_ents)

    gold_edge = frozenset(gold_ents)
    got_edges = [
        frozenset(
            {_norm(extraction.mentions[r.subj].name), _norm(extraction.mentions[r.obj].name)}
        )
        for r in extraction.relationships
        if r.subj < len(extraction.mentions) and r.obj < len(extraction.mentions)
    ]
    rel_tp = 1 if gold_edge in got_edges else 0
    rel_fp = sum(1 for e in got_edges if e != gold_edge)
    rel_fn = 1 - rel_tp
    return {
        "ent_tp": ent_tp, "ent_fp": ent_fp, "ent_fn": ent_fn,
        "rel_tp": rel_tp, "rel_fp": rel_fp, "rel_fn": rel_fn,
    }


def f1_from_counts(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"precision": p, "recall": r, "f1": f1}


def build_gold_subgraph(gold_chain, g, typ_of: dict) -> dict:
    """{entities, edges} over the chain's canonical entities -- the shape
    synthesize_local's _format_subgraph reads. entity_id = canonical id."""
    ids: list = []
    for (s, _rel, o) in gold_chain:
        for x in (s, o):
            if x not in ids:
                ids.append(x)
    entities = [
        {"entity_id": x, "canonical_name": g.canonical_name(x), "typ": typ_of.get(x, "concept")}
        for x in ids
    ]
    edges = [{"subj": s, "predicate": rel, "obj": o} for (s, rel, o) in gold_chain]
    return {"entities": entities, "edges": edges}


def synthesis_given_gold(question, gold_chain, g, typ_of, gold_answer, llm) -> float:
    from goldengraph.synthesize import synthesize_local

    sub = build_gold_subgraph(gold_chain, g, typ_of)
    start_name = g.canonical_name(gold_chain[0][0])
    pred = synthesize_local(question, sub, llm, seed_names=[start_name])
    return metrics.answer_match(pred, gold_answer)


_DIAL_ORDER = ("oracle", "goldengraph", "name_only", "none")


def tracking_verdict(answer_match_by_dial: dict, bridge_recall_by_dial: dict) -> tuple[str, bool]:
    """PASS if the answer-match dial ranking matches the bridge-recall ranking
    (both should descend oracle..none). A faithful proxy tracks."""

    def _rank(d):
        return sorted(d, key=lambda k: -d[k])

    same = _rank(answer_match_by_dial) == _rank(bridge_recall_by_dial)
    return ("answer-match tracks bridge-recall", same)


def answer_match_ablation(corpus, g, typ_of, llm) -> dict:
    """Per dial: reuse ablation._build_store (oracle extraction, dial record_keys),
    oracle-seed + _retrieve_local ball (IDENTICAL to bridge-recall), then real
    synthesize_local over that ball. Returns per-dial answer-match + bridge-recall
    (mean + by_hop)."""
    from goldengraph.answer import _retrieve_local
    from goldengraph.synthesize import synthesize_local

    from .ablation import _DIALS, _KEYFN, _build_store
    from .engines.goldengraph import _NODE_BUDGET, _RETRIEVAL_HOPS
    from .gold import gold_chain
    from .scorecard import bridge_recall

    chains = {qa.id: gold_chain(g, qa) for qa in corpus.questions}
    out: dict = {}
    for dial in _DIALS:
        km = _KEYFN[dial](corpus, g)
        slice_graph, coverage = _build_store(corpus, g, km, typ_of)
        seed_of: dict = {}
        for nid in sorted(coverage):
            for c in coverage[nid]:
                seed_of.setdefault(c, nid)
        id_to_name = {e["entity_id"]: e["canonical_name"] for e in slice_graph.entities()}

        am, br = [], []
        am_hop: dict = {}
        br_hop: dict = {}
        for qa in corpus.questions:
            seed_node = seed_of.get(qa.start_entity_id)
            if seed_node is None:
                a, b = 0.0, 0.0
            else:
                ball = _retrieve_local(
                    slice_graph, [seed_node], max_hops=_RETRIEVAL_HOPS, node_budget=_NODE_BUDGET
                )
                # mid-ablation budget short-circuit (duck-typed: no-op for a plain LLM)
                if getattr(llm, "exhausted", False):
                    pred = ""
                else:
                    pred = synthesize_local(
                        qa.question, ball, llm, seed_names=[id_to_name.get(seed_node, "")]
                    )
                a = metrics.answer_match(pred, qa.gold_answer)
                b = bridge_recall(chains[qa.id], ball, coverage)["whole_chain"]
            am.append(a)
            br.append(b)
            am_hop.setdefault(qa.hop_count, []).append(a)
            br_hop.setdefault(qa.hop_count, []).append(b)
        out[dial] = {
            "answer_match": {
                "mean": sum(am) / len(am) if am else 0.0,
                "by_hop": {h: sum(v) / len(v) for h, v in sorted(am_hop.items())},
            },
            "bridge_recall": {
                "mean": sum(br) / len(br) if br else 0.0,
                "by_hop": {h: sum(v) / len(v) for h, v in sorted(br_hop.items())},
            },
        }
    return out
