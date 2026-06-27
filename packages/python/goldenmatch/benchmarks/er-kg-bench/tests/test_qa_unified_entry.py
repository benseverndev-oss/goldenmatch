"""Slice 4c unified-entry gate -- wheel-free gate shape."""
from __future__ import annotations

from erkgbench.qa_e2e import unified_entry_eval as ue


def _good():
    return ue.UnifiedEntryResult(capability_tier="fuzzy", routed_set_f1=1.0,
                                 budget_shared=True, plan_scales=True,
                                 small_rule="plan_selected_simple", huge_rule="plan_selected_duckdb")


def test_gate_passes_on_good_result():
    assert ue.gate_exit_code(_good()) == 0


def test_gate_fails_when_plan_constant():
    res = ue.UnifiedEntryResult(capability_tier="fuzzy", routed_set_f1=1.0,
                                budget_shared=True, plan_scales=False,
                                small_rule="x", huge_rule="x")
    assert ue.gate_exit_code(res) == 1


def test_gate_fails_when_budget_not_shared():
    res = ue.UnifiedEntryResult(capability_tier="fuzzy", routed_set_f1=1.0,
                                budget_shared=False, plan_scales=True,
                                small_rule="a", huge_rule="b")
    assert ue.gate_exit_code(res) == 1


def test_gate_fails_when_not_fuzzy():
    res = ue.UnifiedEntryResult(capability_tier="exact", routed_set_f1=1.0,
                                budget_shared=True, plan_scales=True,
                                small_rule="a", huge_rule="b")
    assert ue.gate_exit_code(res) == 1


def test_render_lists_verdicts():
    md = ue.render_unified_entry_md(_good())
    assert "[PASS]" in md and "program close" in md
