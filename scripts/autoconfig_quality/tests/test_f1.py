from scripts.autoconfig_quality.anchors import gen_labeled
from scripts.autoconfig_quality.f1 import evaluate_f1


def test_evaluate_f1_on_gen_labeled():
    df, gt = gen_labeled(n_entities=200, seed=7)
    out = evaluate_f1(df, gt, row_cap=None)
    assert 0.0 <= out["f1"] <= 1.0
    assert out["f1"] >= 0.80           # synthetic clones are easy
    assert set(out) >= {"f1", "precision", "recall", "attribution"}
    attr = out["attribution"]
    assert {"blocking_recall", "final_recall", "threshold_loss"} <= set(attr)
    assert attr["blocking_recall"] >= attr["final_recall"]  # blocking is the ceiling
