"""Offline tests for the vectorised ensemble kernels (torch-free): predict_np,
fast_f1, decode_preds. These are the hot paths that used to be Python per-pair loops."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from ensemble_ops import decode_preds, fast_f1, predict_np  # noqa: E402


def _reference_get_label(logits, delta=0.0, num_labels=4):
    """Straightforward (slow) reference: predict c iff logit_c > TH - delta, top-k capped."""
    out = np.zeros_like(logits, dtype=np.int8)
    for i, row in enumerate(logits):
        th = row[0]
        cand = [c for c in range(len(row)) if row[c] > th - delta]
        if num_labels > 0:
            cand = sorted(cand, key=lambda c: -row[c])[:num_labels]
        for c in cand:
            if c != 0:
                out[i, c] = 1
    return out


def test_predict_np_matches_reference():
    rng = np.random.default_rng(0)
    logits = rng.normal(size=(50, 97)).astype(np.float32)
    for delta in (-0.2, 0.0, 0.3):
        got = predict_np(logits, delta=delta, num_labels=4)
        exp = _reference_get_label(logits, delta=delta, num_labels=4)
        assert np.array_equal(got, exp), f"mismatch at delta={delta}"
    # class 0 (TH) is always zeroed
    assert predict_np(logits)[:, 0].sum() == 0


def test_predict_np_delta_monotonic_recall():
    rng = np.random.default_rng(1)
    logits = rng.normal(size=(200, 97)).astype(np.float32)
    # larger delta lowers the bar -> at least as many predictions
    counts = [predict_np(logits, delta=d, num_labels=4)[:, 1:].sum() for d in (-0.3, 0.0, 0.5)]
    assert counts[0] <= counts[1] <= counts[2]


def test_fast_f1_hand_computed():
    # 3 pairs, tiny 4-class space (TH + 3 rels)
    gold = np.array([[0, 1, 0, 0], [0, 0, 1, 0], [1, 0, 0, 0]], dtype=np.int8)
    pred = np.array([[0, 1, 0, 0], [0, 0, 0, 1], [0, 0, 0, 0]], dtype=np.int8)
    # tp=1 (pair0 rel1), n_pred=2, n_gold=2 -> P=R=0.5 -> F1=0.5
    assert abs(fast_f1(pred, gold) - 0.5) < 1e-9
    # perfect
    assert fast_f1(gold, gold) == 1.0
    # empty prediction -> 0
    assert fast_f1(np.zeros_like(gold), gold) == 0.0


def test_decode_preds_vectorised():
    id2rel = {1: "P1", 2: "P2", 3: "P3"}
    # 2 docs: doc0 has 2 pairs, doc1 has 1 pair
    hts_per_doc = [[[0, 1], [1, 0]], [[0, 2]]]
    pred = np.zeros((3, 4), dtype=np.int8)
    pred[0, 1] = 1          # doc0 pair(0,1) -> P1
    pred[0, 3] = 1          # doc0 pair(0,1) -> P3 (multi-label)
    pred[2, 2] = 1          # doc1 pair(0,2) -> P2
    got = decode_preds(pred, hts_per_doc, id2rel)
    assert len(got) == 2  # one list per doc
    assert set(got[0]) == {(0, 1, "P1"), (0, 1, "P3")}  # doc0 (pair (1,0) predicted nothing)
    assert got[1] == [(0, 2, "P2")]  # doc1
