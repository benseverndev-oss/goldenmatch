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
