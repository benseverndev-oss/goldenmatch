"""Pairwise-F1 scoring: hand cases, macro, and PARITY with goldenmatch's own
``core.evaluate.evaluate_clusters`` -- so the reported number is defensible."""
from score import (
    ground_truth_clusters,
    pairwise_f1_macro,
    pairwise_score_one,
)


def test_perfect_prediction_is_f1_one():
    truth = [["a", "b", "c"], ["d", "e"]]
    s = pairwise_score_one(truth, truth)
    assert s.f1 == 1.0 and s.fp == 0 and s.fn == 0


def test_all_singletons_is_zero_recall():
    truth = [["a", "b", "c"]]
    pred = [["a"], ["b"], ["c"]]
    s = pairwise_score_one(pred, truth)
    assert s.tp == 0 and s.recall == 0.0


def test_over_merge_hits_precision():
    truth = [["a", "b"], ["c", "d"]]
    pred = [["a", "b", "c", "d"]]  # one big cluster
    s = pairwise_score_one(pred, truth)
    # pred pairs: ab ac ad bc bd cd = 6; truth pairs: ab cd = 2; tp = 2
    assert s.tp == 2 and s.fp == 4 and s.fn == 0
    assert abs(s.precision - 2 / 6) < 1e-9 and s.recall == 1.0


def test_macro_averages_per_name_equally():
    truth = {"n1": [["a", "b"]], "n2": [["c", "d"]]}
    pred = {"n1": [["a", "b"]], "n2": [["c"], ["d"]]}  # n1 perfect, n2 misses
    out = pairwise_f1_macro(pred, truth)
    assert out["n_names"] == 2
    # n1 F1=1.0, n2 F1=0.0 -> macro 0.5
    assert abs(out["pairwise_f1_macro"] - 0.5) < 1e-9


def test_missing_prediction_scores_as_singletons():
    truth = {"n1": [["a", "b", "c"]]}
    out = pairwise_f1_macro({}, truth)  # no prediction at all
    assert out["per_name"]["n1"]["f1"] == 0.0


def test_ground_truth_clusters_handles_both_shapes():
    # valid shape: name -> [[pid], ...]
    v = ground_truth_clusters({"n": [["a", "b"], ["c"]]})
    assert v == {"n": [["a", "b"], ["c"]]}
    # train shape: name -> {aid -> [pid]}
    t = ground_truth_clusters({"n": {"A1": ["a", "b"], "A2": ["c"]}})
    assert sorted(map(sorted, t["n"])) == [["a", "b"], ["c"]]


def test_parity_with_goldenmatch_evaluate_clusters():
    from itertools import combinations

    from goldenmatch.core.evaluate import evaluate_clusters

    truth = [["a", "b", "c"], ["d", "e"], ["f"]]
    pred = [["a", "b"], ["c", "d"], ["e"], ["f"]]

    mine = pairwise_score_one(pred, truth)

    # translate to goldenmatch's (clusters dict, gt_pairs) surface
    pid2int = {pid: i for i, pid in enumerate(sorted({p for c in truth for p in c}))}
    clusters = {
        cid: {"members": [pid2int[p] for p in members]}
        for cid, members in enumerate(pred)
    }
    gt_pairs = set()
    for members in truth:
        for a, b in combinations(sorted(pid2int[p] for p in members), 2):
            gt_pairs.add((a, b))
    theirs = evaluate_clusters(clusters, gt_pairs)

    assert (mine.tp, mine.fp, mine.fn) == (theirs.tp, theirs.fp, theirs.fn)
    assert abs(mine.f1 - theirs.f1) < 1e-12
