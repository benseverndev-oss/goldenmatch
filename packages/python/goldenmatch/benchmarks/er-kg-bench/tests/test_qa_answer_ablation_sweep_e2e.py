"""Runner tests for the ER->answer ablation sweep.

- `test_run_sweep_offline_stubbed` stubs the wheel-dependent primitive so the SWEEP WIRING
  (loop over ambiguities -> collect -> aggregate) is covered with no wheel/network.
- `test_run_sweep_e2e_fixed_llm` exercises the real path (wheel + a deterministic offline
  LLM); it SKIPS when `goldengraph_native` is absent, and runs in the opt-in bench lane.
"""
from __future__ import annotations

import pytest
from erkgbench.qa_e2e import answer_ablation_sweep as mod


def test_run_sweep_offline_stubbed(monkeypatch):
    from erkgbench.qa_e2e import ablation, engineered, gold, scorecard_llm

    def fake_generate(*, seed, n_questions, ambiguity, max_hops):
        return {"amb": ambiguity}  # sentinel corpus carrying its own ambiguity

    def fake_ama(corpus, g, typ_of, llm):
        a = corpus["amb"]

        def cell(v):  # oracle decays with ambiguity; none flat -> the delta shrinks

            return {"answer_match": {"mean": v, "by_hop": {}},
                    "bridge_recall": {"mean": v, "by_hop": {}}}

        return {
            "oracle": cell(0.9 - 0.5 * a),
            "goldengraph": cell(0.7 - 0.4 * a),
            "name_only": cell(0.4 - 0.2 * a),
            "none": cell(0.3 - 0.1 * a),
        }

    monkeypatch.setattr(engineered, "generate_engineered", fake_generate)
    monkeypatch.setattr(gold.GoldGraph, "from_corpus", classmethod(lambda cls, c: c))
    monkeypatch.setattr(ablation, "_typ_of", lambda g: {})
    monkeypatch.setattr(scorecard_llm, "answer_match_ablation", fake_ama)

    sw = mod.run_answer_ablation_sweep(
        seed=7, n_questions=5, ambiguities=(0.0, 0.5, 1.0), max_hops=4, llm=object()
    )
    assert sw.ambiguities == (0.0, 0.5, 1.0)
    assert set(sw.answer_match) == {"oracle", "goldengraph", "name_only", "none"}
    # the runner threaded each ambiguity through and mapped results back in order
    assert sw.answer_match["oracle"][0.0] > sw.answer_match["oracle"][1.0]
    # delta computed vs `none`, per ambiguity
    assert abs(sw.delta["oracle"][0.0] - ((0.9) - (0.3))) < 1e-9


def test_run_sweep_e2e_fixed_llm():
    pytest.importorskip("goldengraph_native")

    class _FixedLLM:  # deterministic, no network
        def complete(self, prompt):
            return "Answer: X"

    sw = mod.run_answer_ablation_sweep(
        seed=7, n_questions=20, ambiguities=(0.0, 1.0), max_hops=4, llm=_FixedLLM()
    )
    assert sw.ambiguities == (0.0, 1.0)
    for d in ("oracle", "goldengraph", "name_only", "none"):
        assert set(sw.answer_match[d]) == {0.0, 1.0}
        assert set(sw.bridge_recall[d]) == {0.0, 1.0}
    # bridge-recall is the deterministic backbone: oracle resolves >= none.
    assert sw.bridge_recall["oracle"][0.0] >= sw.bridge_recall["none"][0.0]
