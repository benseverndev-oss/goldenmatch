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


def test_attribution_skips_at_scale(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_QH_ATTR_MAX_PAIRS", "1")  # force the guard
    df, gt = gen_labeled(n_entities=40, seed=7)
    out = evaluate_f1(df, gt)
    assert "f1" in out and "precision" in out and "recall" in out  # floor intact
    assert out["attribution"] == {"skipped": "scale"}  # explicit, not blocking_recall=0
