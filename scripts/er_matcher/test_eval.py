"""Tests for the ER-matcher eval harness + ship gate (P5).

Pure — no model/server. Locks the metric math, the abstain handling, and the
ship-gate decision logic (mirrors scripts/test_qis_gate.py)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import eval as ev  # noqa: E402,I001


def test_confusion_and_prf1():
    c = ev.confusion([True, True, False, False], [True, False, True, False])
    assert c == {"tp": 1, "fp": 1, "fn": 1, "tn": 1}
    m = ev.prf1(c)
    assert m["precision"] == 0.5 and m["recall"] == 0.5 and m["f1"] == 0.5


def test_prf1_empty_is_zero_not_crash():
    assert ev.prf1({"tp": 0, "fp": 0, "fn": 0, "tn": 0})["f1"] == 0.0


def test_ece_perfect_calibration_is_zero():
    # confidence == accuracy in every populated bin -> ECE 0.
    assert ev.expected_calibration_error([1.0, 1.0], [True, True]) == 0.0
    assert ev.expected_calibration_error([0.0, 0.0], [False, False]) == 0.0


def test_ece_detects_overconfidence():
    # confident (1.0) but always wrong -> ECE ~1.0.
    assert ev.expected_calibration_error([1.0, 1.0, 1.0], [False, False, False]) > 0.9


def _rows():
    return [
        {"a": {"x": "1"}, "b": {"x": "1"}, "label": "match", "domain": "people"},
        {"a": {"x": "2"}, "b": {"x": "9"}, "label": "no_match", "domain": "people"},
        {"a": {"x": "3"}, "b": {"x": "3"}, "label": "match", "domain": "biz"},
    ]


def test_run_eval_perfect_matcher():
    def perfect(a, b):
        return {"match": a["x"] == b["x"], "confidence": 1.0, "reason": ""}
    sc = ev.run_eval({"test": _rows()}, perfect)
    o = sc["splits"]["test"]["overall"]
    assert o["f1"] == 1.0 and o["abstained"] == 0
    assert set(sc["splits"]["test"]["by_domain"]) == {"people", "biz"}


def test_run_eval_abstain_counts_as_no_match():
    def always_abstain(a, b):
        return None
    sc = ev.run_eval({"test": _rows()}, always_abstain)
    o = sc["splits"]["test"]["overall"]
    assert o["abstained"] == 3
    # everything predicted no_match -> the two true matches are FN -> recall 0 -> F1 0.
    assert o["f1"] == 0.0


def _scorecard(f1: float, ece: float = 0.05) -> dict:
    return {"splits": {"test": {"overall": {"f1": f1, "ece": ece}}}}


def test_gate_passes_when_all_checks_clear():
    g = ev.evaluate_gate(_scorecard(0.90), abs_floor=0.80, hosted_boost_f1=0.85,
                         fs_baseline_f1=0.75, min_gain_over_fs=0.02)
    assert g.passed is True


def test_gate_fails_below_floor():
    g = ev.evaluate_gate(_scorecard(0.70), abs_floor=0.80)
    assert g.passed is False
    assert any(c["check"] == "absolute_floor" and not c["passed"] for c in g.checks)


def test_gate_fails_when_not_beating_hosted():
    g = ev.evaluate_gate(_scorecard(0.82), abs_floor=0.80, hosted_boost_f1=0.90)
    assert g.passed is False
    assert any(c["check"] == "beats_hosted_boost" and not c["passed"] for c in g.checks)


def test_gate_fails_when_no_gain_over_fs():
    g = ev.evaluate_gate(_scorecard(0.80), abs_floor=0.80, fs_baseline_f1=0.80,
                         min_gain_over_fs=0.05)
    assert g.passed is False
    assert any(c["check"] == "adds_over_fs" and not c["passed"] for c in g.checks)


def test_gate_fails_on_poor_calibration():
    g = ev.evaluate_gate(_scorecard(0.95, ece=0.40), abs_floor=0.80, max_ece=0.15)
    assert g.passed is False
    assert any(c["check"] == "calibration" and not c["passed"] for c in g.checks)


def test_gate_regression_check():
    g = ev.evaluate_gate(_scorecard(0.80), abs_floor=0.70, baseline_f1=0.90,
                         regression_tol=0.02)
    assert g.passed is False
    assert any(c["check"] == "no_regression" and not c["passed"] for c in g.checks)


def test_gate_missing_split_fails_cleanly():
    g = ev.evaluate_gate({"splits": {}}, split="test")
    assert g.passed is False
