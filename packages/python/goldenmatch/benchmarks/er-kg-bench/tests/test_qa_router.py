"""Router gate -- wheel-free classifier accuracy + gate shape."""
from __future__ import annotations

from erkgbench.qa_e2e import router_eval as re_


def test_classifier_accuracy_on_b1_questions():
    acc = re_.classifier_accuracy(seed=7, n_anchors=20, ambiguity=0.0)
    assert acc["aggregate_recall"] == 1.0
    assert acc["slot_accuracy"] == 1.0


def test_gate_shape_passes_on_good_result():
    res = re_.RouterResult(aggregate_recall=1.0, slot_accuracy=1.0, routed_setf1=1.0)
    assert re_.gate_exit_code(res) == 0


def test_gate_fails_when_routed_setf1_low():
    res = re_.RouterResult(aggregate_recall=1.0, slot_accuracy=1.0, routed_setf1=0.5)
    assert re_.gate_exit_code(res) == 1


def test_gate_fails_when_classifier_misses():
    res = re_.RouterResult(aggregate_recall=0.5, slot_accuracy=1.0, routed_setf1=1.0)
    assert re_.gate_exit_code(res) == 1
