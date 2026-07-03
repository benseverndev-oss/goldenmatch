from __future__ import annotations

from erkgbench.qa_e2e.scorecard_llm import (
    ScorecardResult,
    _BudgetedLLM,
    render_scorecard_md,
)
from goldenmatch.config.schemas import BudgetConfig
from goldenmatch.core.llm_budget import BudgetTracker


class _CostLLM:
    def complete(self, prompt):
        return "x" * 4000  # big output -> burns budget fast


def test_budgeted_llm_stops_at_cap():
    # tiny POSITIVE cap (not 0.0 -- 0.0 is exhausted at construction, which wouldn't
    # exercise the record->exhaust transition). One big-output call records enough
    # usage to flip the flag, proving _BudgetedLLM.complete -> record_usage works.
    tracker = BudgetTracker(BudgetConfig(max_cost_usd=1e-9))
    llm = _BudgetedLLM(_CostLLM(), tracker, model="gpt-4o-mini")
    assert llm.exhausted is False  # not yet -- nothing recorded
    llm.complete("a prompt that costs tokens")
    assert llm.exhausted is True   # the recorded usage crossed the cap


def test_render_scorecard_md_has_all_three_stages():
    res = ScorecardResult(
        extraction={"entity": {"f1": 0.8}, "relation": {"f1": 0.6}},
        synthesis_ceiling={"mean": 0.9, "by_hop": {2: 0.95, 4: 0.85}},
        answer_match_ablation={
            "oracle": {"answer_match": {"mean": 0.9, "by_hop": {}}, "bridge_recall": {"mean": 1.0, "by_hop": {}}},
            "goldengraph": {"answer_match": {"mean": 0.6, "by_hop": {}}, "bridge_recall": {"mean": 0.55, "by_hop": {}}},
            "name_only": {"answer_match": {"mean": 0.3, "by_hop": {}}, "bridge_recall": {"mean": 0.23, "by_hop": {}}},
            "none": {"answer_match": {"mean": 0.2, "by_hop": {}}, "bridge_recall": {"mean": 0.23, "by_hop": {}}},
        },
        tracking=("answer-match tracks bridge-recall", True),
        budget_exhausted=False,
    )
    md = render_scorecard_md(res)
    assert "extraction" in md.lower() and "entity-F1" in md
    assert "synthesis" in md.lower()
    assert "answer-match" in md.lower() and "bridge-recall" in md.lower()
    assert "PASS" in md or "WARN" in md
