"""End-to-end Track C: relation-aware span grounding beats every documented
faithfulness mechanism on the distractor false-support rate, the hallucination
rate, AND the confidence axis. The Phase-0 faithfulness thesis, pinned."""
from grounding_data import generate_grounding_dataset
from run_track_c import DEFAULT_ENGINES, run
from score_c import _auroc, _ece


def test_relation_aware_wins_every_faithfulness_axis():
    ds = generate_grounding_dataset(seed=0)
    res = run(ds)
    ra = res["relation_aware"]
    inc = {k: v for k, v in res.items() if k != "relation_aware"}
    assert set(inc) == {"ungrounded", "sentence_presence", "ontology_conformance"}

    # every documented mechanism grounds the distractor (co-occur != support)...
    for name, s in inc.items():
        assert s["distractor_false_support_rate"] > 0.9, (name, s)
    # ...relation-aware refuses it
    assert ra["distractor_false_support_rate"] < 0.1, ra

    # ontology/ungrounded also pass hallucinations through; relation-aware doesn't
    assert res["ontology_conformance"]["hallucination_rate"] > 0.9
    assert res["ungrounded"]["hallucination_rate"] > 0.9
    assert ra["hallucination_rate"] < 0.1

    # perfect support-F1, and it's the ONLY engine emitting a graded confidence
    assert ra["support_f1"] > 0.9
    assert ra["emits_confidence"] and ra["confidence_auroc"] > 0.9
    for name, s in inc.items():
        assert not s["emits_confidence"] and s["confidence_auroc"] == 0.5, (name, s)


def test_default_engines_cover_the_documented_faithfulness_families():
    assert DEFAULT_ENGINES == (
        "ungrounded", "sentence_presence", "ontology_conformance", "relation_aware")


def test_auroc_and_ece_helpers():
    # perfectly separating scores -> AUROC 1.0; constant scores -> 0.5 (ties)
    assert _auroc([0.9, 0.8, 0.2, 0.1], [1, 1, 0, 0]) == 1.0
    assert _auroc([0.5, 0.5, 0.5, 0.5], [1, 1, 0, 0]) == 0.5
    # a confident-and-correct predictor is well calibrated (low ECE)
    assert _ece([0.95, 0.95, 0.05, 0.05], [1, 1, 0, 0]) < 0.1
