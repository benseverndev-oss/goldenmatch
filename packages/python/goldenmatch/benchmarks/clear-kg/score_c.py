"""CLEAR-KG Track C scoring: span-grounded faithfulness.

Given per-triple grounding decisions and the gold verdicts/classes, report:

- support P / R / F1  -- of the SUPPORT decision (verdict == supported) vs gold.
- grounding_coverage  -- fraction of triples the system cites a span for (the
                         "did you show your source" axis; 0 for ungrounded).
- grounded_correctness-- of the triples it grounded-and-supported, fraction gold
                         supported (SPEC def; None if it grounds nothing).
- distractor_false_support_rate -- THE headline: of gold DISTRACTOR triples
                         (entities co-occur, relation not stated), fraction the
                         system wrongly marks supported. ~1.0 for every presence/
                         type mechanism; ~0 for relation-aware grounding.
- hallucination_rate  -- of gold HALLUCINATED triples (never in corpus), fraction
                         wrongly marked supported.
- confidence AUROC / ECE + emits_confidence -- grades the "never black box" axis:
                         a system with no graded confidence gets AUROC 0.5.
"""
from __future__ import annotations

import numpy as np
from scipy.stats import rankdata


def _auroc(scores: list[float], labels: list[int]) -> float:
    pos = sum(labels)
    neg = len(labels) - pos
    if pos == 0 or neg == 0:
        return 0.5
    ranks = rankdata(scores)  # average ranks -> ties handled (constant -> 0.5)
    rank_pos = sum(r for r, y in zip(ranks, labels) if y == 1)
    return (rank_pos - pos * (pos + 1) / 2) / (pos * neg)


def _ece(confs: list[float], labels: list[int], n_bins: int = 10) -> float:
    if not confs:
        return 0.0
    c = np.asarray(confs, dtype=float)
    y = np.asarray(labels, dtype=float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    n = len(c)
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        m = (c > lo) & (c <= hi) if i > 0 else (c >= lo) & (c <= hi)
        if not m.any():
            continue
        ece += (m.sum() / n) * abs(y[m].mean() - c[m].mean())
    return float(ece)


def _rate(decisions_by_id, cands, gold_class) -> tuple[float, int]:
    """Fraction of gold-``gold_class`` triples the system marked supported."""
    sub = [c for c in cands if c["gold_class"] == gold_class]
    if not sub:
        return 0.0, 0
    wrong = sum(1 for c in sub if decisions_by_id[c["triple_id"]]["verdict"] == "supported")
    return wrong / len(sub), len(sub)


def score_track_c(decisions: list[dict], cands: list[dict]) -> dict:
    by_id = {d["triple_id"]: d for d in decisions}
    gold = {c["triple_id"]: c["gold_verdict"] for c in cands}

    tp = fp = fn = 0
    for c in cands:
        sup = by_id[c["triple_id"]]["verdict"] == "supported"
        gsup = gold[c["triple_id"]] == "supported"
        if sup and gsup:
            tp += 1
        elif sup and not gsup:
            fp += 1
        elif not sup and gsup:
            fn += 1
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0

    grounded = [c for c in cands if by_id[c["triple_id"]]["grounded"]]
    coverage = len(grounded) / len(cands) if cands else 0.0
    if grounded:
        gc = sum(1 for c in grounded if gold[c["triple_id"]] == "supported") / len(grounded)
    else:
        gc = None

    distractor_fsr, n_distractor = _rate(by_id, cands, "distractor")
    hallucination_rate, n_halluc = _rate(by_id, cands, "hallucinated")

    # confidence axis: None -> a constant, so a no-confidence system lands at 0.5
    raw = [by_id[c["triple_id"]]["confidence"] for c in cands]
    emits = any(v is not None for v in raw) and len({v for v in raw if v is not None}) > 1
    confs = [(v if v is not None else 0.5) for v in raw]
    labels = [1 if gold[c["triple_id"]] == "supported" else 0 for c in cands]
    auroc = _auroc(confs, labels) if emits else 0.5
    ece = _ece(confs, labels) if emits else None

    return {
        "support_p": prec, "support_r": rec, "support_f1": f1,
        "grounding_coverage": coverage,
        "grounded_correctness": gc,
        "distractor_false_support_rate": distractor_fsr,
        "hallucination_rate": hallucination_rate,
        "confidence_auroc": auroc, "confidence_ece": ece, "emits_confidence": emits,
        "n_distractor": n_distractor, "n_hallucinated": n_halluc,
        "n_candidates": len(cands),
    }
