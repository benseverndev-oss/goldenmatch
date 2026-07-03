"""CLEAR-KG Track D scoring: the CLEAR composite.

CLEAR = harmonic mean of three axis scores measured on ONE corpus by ONE system:
  * extraction_f1   -- surface triples recovered from the real docs (Track A);
  * er_f1           -- B-cubed over the system's mention clustering (Track B);
  * grounded_correct-- of the triples the system grounds-and-supports, the
                       fraction whose span actually supports them (Track C).

The harmonic mean is 0 if any axis is 0 and is dragged toward the WEAKEST axis --
so a system that extracts well but merges homographs or grounds distractors
cannot post a high CLEAR score. Extraction is shared (table stakes); the composite
is decided by the two moats.
"""
from __future__ import annotations

from er_utils import norm


def harmonic_mean(vals) -> float:
    vals = list(vals)
    if not vals or any(v <= 0 for v in vals):
        return 0.0
    return len(vals) / sum(1.0 / v for v in vals)


def extraction_surface_f1(emitted_surfaces, gold_surfaces) -> float:
    """Set-F1 of extracted (subj, rel, obj) surface triples vs gold."""
    def key(t):
        s, r, o = t
        return (norm(s), r, norm(o))
    e = {key(t) for t in emitted_surfaces}
    g = {key(t) for t in gold_surfaces}
    tp = len(e & g)
    p = tp / len(e) if e else 0.0
    r = tp / len(g) if g else 0.0
    return 2 * p * r / (p + r) if (p + r) else 0.0


def grounded_correct_rate(decisions: list[dict], cands: list[dict]) -> float:
    """Track C grounded-correctness: of the triples the system grounds AND marks
    supported, the fraction whose gold verdict is actually supported."""
    by_id = {d["triple_id"]: d for d in decisions}
    gold = {c["triple_id"]: c["gold_verdict"] for c in cands}
    grounded_sup = [c for c in cands
                    if by_id[c["triple_id"]]["grounded"]
                    and by_id[c["triple_id"]]["verdict"] == "supported"]
    if not grounded_sup:
        return 0.0
    correct = sum(1 for c in grounded_sup if gold[c["triple_id"]] == "supported")
    return correct / len(grounded_sup)


def clear_score(extraction_f1: float, er_f1: float, grounded_correct: float) -> dict:
    return {
        "extraction_f1": extraction_f1,
        "er_f1": er_f1,
        "grounded_correct": grounded_correct,
        "clear": harmonic_mean([extraction_f1, er_f1, grounded_correct]),
    }
