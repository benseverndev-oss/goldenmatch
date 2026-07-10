"""CLEAR-KG Track A — Re-DocRED relation-triple scoring.

Standard DocRED micro-averaged relation-F1: a predicted (head_idx, relation,
tail_idx) triple is correct iff it is in the document's gold triple set. Reported
as micro precision / recall / F1 over all documents, comparable to the published
Re-DocRED numbers (SOTA ~80.7 fine-tuned BERT / ~74.6 strong LLM).
"""
from __future__ import annotations


def score_redocred(pred_by_doc: list[set], docs: list[dict]) -> dict:
    tp = fp = fn = 0
    for pred, doc in zip(pred_by_doc, docs):
        gold = doc["gold"]
        tp += len(pred & gold)
        fp += len(pred - gold)
        fn += len(gold - pred)
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"precision": p, "recall": r, "f1": f1, "tp": tp, "fp": fp, "fn": fn,
            "n_docs": len(docs), "n_gold": tp + fn, "n_pred": tp + fp}
