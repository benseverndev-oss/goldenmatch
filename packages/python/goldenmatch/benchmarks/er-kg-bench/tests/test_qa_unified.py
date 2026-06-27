"""Slice 4a unified planner gate -- wheel-free routing + gate shape."""
from __future__ import annotations

from erkgbench.qa_e2e import unified_eval as ue


def test_capability_workload_routes_fuzzy():
    r = ue.routing_correctness(seed=7, n_anchors=12, n_facts=12)
    assert r["capability_tier"] == "fuzzy"
    assert r["lookup_tier"] == "exact"
    assert r["capability_fraction"] >= 0.5
    assert r["lookup_fraction"] < 0.5


def test_gate_shape_passes_on_good_result():
    res = ue.UnifiedResult(capability_tier="fuzzy", lookup_tier="exact",
                           capability_fraction=1.0, lookup_fraction=0.0,
                           agg_delta=0.287, bridge_delta=0.324)
    assert ue.gate_exit_code(res) == 0


def test_gate_fails_when_tier_wrong():
    res = ue.UnifiedResult(capability_tier="exact", lookup_tier="exact",
                           capability_fraction=1.0, lookup_fraction=0.0,
                           agg_delta=0.287, bridge_delta=0.324)
    assert ue.gate_exit_code(res) == 1


def test_gate_fails_when_justification_delta_low():
    res = ue.UnifiedResult(capability_tier="fuzzy", lookup_tier="exact",
                           capability_fraction=1.0, lookup_fraction=0.0,
                           agg_delta=0.05, bridge_delta=0.05)
    assert ue.gate_exit_code(res) == 1
