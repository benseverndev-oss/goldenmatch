"""Slice 4b tier gate -- wheel-free gate shape."""
from __future__ import annotations

from erkgbench.qa_e2e import tier_eval as te


def test_gate_passes_on_good_result():
    res = te.TierResult(fuzzy_recall=0.40, exact_recall=0.0, n_pairs=300)
    assert te.gate_exit_code(res) == 0


def test_gate_fails_when_fuzzy_not_better():
    res = te.TierResult(fuzzy_recall=0.03, exact_recall=0.0, n_pairs=300)  # gap < MARGIN
    assert te.gate_exit_code(res) == 1


def test_render_md_lists_verdict():
    md = te.render_tier_md(te.TierResult(fuzzy_recall=0.40, exact_recall=0.0, n_pairs=300))
    assert "FUZZY out-resolves EXACT" in md and "[PASS]" in md
