"""Pure-numpy ensemble/decoding kernels — the hot paths, vectorised and torch-free so
they unit-test offline. Used by both the eval loop (`model.decode_preds`) and the
ensemble sweep (`modal_app.ensemble_eval`). No Python per-pair loops on the hot path."""
from __future__ import annotations

import numpy as np


def predict_np(logits, delta=0.0, num_labels=4):
    """ATLOP adaptive-threshold decode with a global offset, vectorised: predict class c
    iff ``logit_c > TH_logit - delta`` (delta>0 -> more predictions -> higher recall),
    capped to the top-``num_labels`` classes per pair. Returns a binary [n, 97] matrix
    with class 0 (TH) zeroed."""
    logits = np.asarray(logits)
    th = logits[:, 0:1]
    mask = logits > (th - delta)
    if num_labels > 0:
        kth = np.partition(logits, -num_labels, axis=1)[:, -num_labels][:, None]
        mask = mask & (logits >= kth)
    out = mask.astype(np.int8)
    out[:, 0] = 0
    return out


def fast_f1(pred, gold):
    """Micro F1 over relation classes 1..96 from a binary pred matrix vs the gold matrix,
    fully vectorised. Equals the official (non-Ign) F1 when pairs are unique per doc and
    every title is in scope -- a faithful ranking signal for the dev sweep."""
    p, g = np.asarray(pred)[:, 1:], np.asarray(gold)[:, 1:]
    tp = int(np.logical_and(p, g).sum())
    n_pred, n_gold = int(p.sum()), int(g.sum())
    if tp == 0 or n_pred == 0 or n_gold == 0:
        return 0.0
    prec, rec = tp / n_pred, tp / n_gold
    return 2 * prec * rec / (prec + rec)


def decode_preds(pred_matrix, hts_per_doc, id2rel):
    """Map a [total_pairs, 97] binary prediction matrix back to per-doc
    ``(h_idx, t_idx, relation_Pid)`` triples. ``np.nonzero`` touches only the predicted
    cells, not the full pairs x 96 grid. Class 0 (TH) is skipped."""
    pred_matrix = np.asarray(pred_matrix)
    counts = [len(h) for h in hts_per_doc]
    doc_of = np.repeat(np.arange(len(hts_per_doc)), counts) if counts else np.array([], dtype=int)
    flat_hts = [ht for hts in hts_per_doc for ht in hts]
    rows, cls_minus1 = np.nonzero(pred_matrix[:, 1:] > 0)
    preds_per_doc = [[] for _ in hts_per_doc]
    for row, c in zip(rows.tolist(), cls_minus1.tolist()):
        h, t = flat_hts[row]
        preds_per_doc[doc_of[row]].append((int(h), int(t), id2rel[c + 1]))
    return preds_per_doc
