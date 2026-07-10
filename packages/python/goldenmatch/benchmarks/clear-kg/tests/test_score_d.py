"""The CLEAR composite helpers: the harmonic mean drags to the weakest axis and
zeroes out on any zero component."""
from score_d import clear_score, extraction_surface_f1, grounded_correct_rate, harmonic_mean


def test_harmonic_mean_drags_to_the_weakest_axis():
    assert harmonic_mean([1.0, 1.0, 1.0]) == 1.0
    assert harmonic_mean([1.0, 1.0, 0.0]) == 0.0          # one hollow axis -> 0
    # dragged well below the arithmetic mean (0.833) toward the min
    hm = harmonic_mean([1.0, 1.0, 0.5])
    assert 0.7 < hm < 0.76
    # monotone: improving the weak axis raises the composite
    assert harmonic_mean([1.0, 1.0, 0.75]) > hm


def test_extraction_surface_f1():
    gold = [("Jane Okafor", "works_at", "Acme Labs")]
    assert extraction_surface_f1(gold, gold) == 1.0
    assert extraction_surface_f1([], gold) == 0.0


def test_grounded_correct_rate_is_grounding_precision():
    cands = [
        {"triple_id": "a", "gold_verdict": "supported"},
        {"triple_id": "b", "gold_verdict": "unsupported"},
        {"triple_id": "c", "gold_verdict": "supported"},
    ]
    # grounds a (correct) + b (wrong); refuses c -> precision 1/2
    decisions = [
        {"triple_id": "a", "grounded": True, "verdict": "supported"},
        {"triple_id": "b", "grounded": True, "verdict": "supported"},
        {"triple_id": "c", "grounded": False, "verdict": "unsupported"},
    ]
    assert grounded_correct_rate(decisions, cands) == 0.5


def test_clear_score_shape():
    s = clear_score(1.0, 0.8, 0.75)
    assert set(s) == {"extraction_f1", "er_f1", "grounded_correct", "clear"}
    assert s["clear"] == harmonic_mean([1.0, 0.8, 0.75])
