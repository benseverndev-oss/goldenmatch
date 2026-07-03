from __future__ import annotations

import pytest
from erkgbench.qa_e2e.scorecard_llm import tracking_verdict


def test_tracking_verdict_pass_when_orders_match():
    am = {"oracle": 0.9, "goldengraph": 0.6, "name_only": 0.3, "none": 0.2}
    br = {"oracle": 1.0, "goldengraph": 0.55, "name_only": 0.23, "none": 0.23}
    _label, passed = tracking_verdict(am, br)
    assert passed is True


def test_tracking_verdict_warn_on_divergence():
    # answer-match HIGH for none but bridge-recall says none is worst -> divergence
    am = {"oracle": 0.5, "goldengraph": 0.5, "name_only": 0.5, "none": 0.9}
    br = {"oracle": 1.0, "goldengraph": 0.55, "name_only": 0.23, "none": 0.23}
    _label, passed = tracking_verdict(am, br)
    assert passed is False


def test_answer_match_ablation_e2e():
    pytest.importorskip("goldengraph_native")
    from erkgbench.qa_e2e import ablation
    from erkgbench.qa_e2e.engineered import generate_engineered
    from erkgbench.qa_e2e.gold import GoldGraph
    from erkgbench.qa_e2e.scorecard_llm import answer_match_ablation

    corpus = generate_engineered(seed=7, n_questions=40, ambiguity=0.6, max_hops=4)
    g = GoldGraph.from_corpus(corpus)
    typ_of = ablation._typ_of(g)

    class _FixedLLM:  # deterministic, no network -- always answers "Answer: X"
        def complete(self, prompt):
            return "Answer: X"

    res = answer_match_ablation(corpus, g, typ_of, _FixedLLM())
    for d in ("oracle", "goldengraph", "name_only", "none"):
        assert "answer_match" in res[d] and "bridge_recall" in res[d]
    assert res["oracle"]["bridge_recall"]["mean"] >= res["none"]["bridge_recall"]["mean"]
