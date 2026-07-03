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


def test_answer_setf1_pure():
    # parse "Banana, Cherry" -> {Banana,Cherry}; gold {Banana,Cherry} -> 1.0
    assert re_.answer_setf1("Banana, Cherry.", {"Banana", "Cherry"}, {"Banana", "Cherry", "Date"}) == 1.0


def test_answer_setf1_partial():
    got = re_.answer_setf1("Only Banana here.", {"Banana", "Cherry"}, {"Banana", "Cherry"})
    assert 0.0 < got < 1.0


def test_temporal_classifier_accuracy_on_b2_questions():
    acc = re_.temporal_classifier_accuracy(seed=7, n_facts=20, ambiguity=0.6)
    assert acc["temporal_recall"] == 1.0
    assert acc["temporal_slot_acc"] == 1.0


def test_gate_shape_includes_temporal():
    res = re_.RouterResult(
        aggregate_recall=1.0, slot_accuracy=1.0, routed_setf1=1.0,
        temporal_recall=1.0, temporal_slot_acc=1.0, temporal_past_acc=1.0, temporal_current_acc=1.0,
    )
    assert re_.gate_exit_code(res) == 0


def test_gate_fails_when_temporal_past_low():
    res = re_.RouterResult(
        aggregate_recall=1.0, slot_accuracy=1.0, routed_setf1=1.0,
        temporal_recall=1.0, temporal_slot_acc=1.0, temporal_past_acc=0.0, temporal_current_acc=1.0,
    )
    assert re_.gate_exit_code(res) == 1


def test_first_known_name_in_text():
    assert re_.first_known_name("It is Banana.", {"Apple", "Banana"}) == "Banana"
    assert re_.first_known_name("nothing", {"Apple"}) is None


def test_heuristic_misses_paraphrases():
    assert re_.heuristic_paraphrase_accuracy() <= 0.2


def test_stub_escalation_recovers_paraphrases():
    assert re_.stub_escalation_accuracy() == 1.0


def test_gate_shape_includes_paraphrase_rows():
    res = re_.RouterResult(
        aggregate_recall=1.0, slot_accuracy=1.0, routed_setf1=1.0,
        temporal_recall=1.0, temporal_slot_acc=1.0, temporal_past_acc=1.0, temporal_current_acc=1.0,
        heuristic_paraphrase_acc=0.0, stub_escalation_acc=1.0,
    )
    assert re_.gate_exit_code(res) == 0


def test_gate_fails_when_stub_escalation_low():
    res = re_.RouterResult(
        aggregate_recall=1.0, slot_accuracy=1.0, routed_setf1=1.0,
        temporal_recall=1.0, temporal_slot_acc=1.0, temporal_past_acc=1.0, temporal_current_acc=1.0,
        heuristic_paraphrase_acc=0.0, stub_escalation_acc=0.5,
    )
    assert re_.gate_exit_code(res) == 1


def test_gate_fails_when_paraphrases_too_easy():
    res = re_.RouterResult(
        aggregate_recall=1.0, slot_accuracy=1.0, routed_setf1=1.0,
        temporal_recall=1.0, temporal_slot_acc=1.0, temporal_past_acc=1.0, temporal_current_acc=1.0,
        heuristic_paraphrase_acc=0.9, stub_escalation_acc=1.0,
    )
    assert re_.gate_exit_code(res) == 1


def test_llm_classifier_accuracy_with_stub_llm():
    from erkgbench.qa_e2e.router_paraphrases import Paraphrase
    from goldengraph.route import QueryIntent

    class _LLM:
        def complete(self, prompt):
            return '{"intent":"aggregate","anchor":"Soundex","relation":"works_at","as_of":null}'

    pps = [Paraphrase("who all does Soundex works at, name them", QueryIntent.AGGREGATE, "Soundex", "works_at")]
    acc = re_.llm_classifier_accuracy(pps, _LLM())
    assert acc["slot_acc"] == 1.0 and acc["intent_acc"] == 1.0
