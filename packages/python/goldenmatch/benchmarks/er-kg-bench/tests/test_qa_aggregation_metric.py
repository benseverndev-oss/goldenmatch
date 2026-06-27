from __future__ import annotations

from erkgbench.qa_e2e.aggregation import count_accuracy, set_f1


def test_set_f1_perfect():
    r = set_f1({"a", "b", "c"}, {"a", "b", "c"})
    assert r == {"precision": 1.0, "recall": 1.0, "f1": 1.0}


def test_set_f1_missing_member_drops_recall():
    r = set_f1({"a", "b"}, {"a", "b", "c"})
    assert r["recall"] < 1.0 and r["precision"] == 1.0


def test_set_f1_extra_drops_precision():
    r = set_f1({"a", "b", "c", "x"}, {"a", "b", "c"})
    assert r["precision"] < 1.0 and r["recall"] == 1.0


def test_set_f1_empty_gold_no_crash():
    assert set_f1(set(), set())["f1"] == 0.0


def test_count_accuracy_exact():
    assert count_accuracy(3, 3) == 1.0
    assert count_accuracy(2, 3) == 0.0
