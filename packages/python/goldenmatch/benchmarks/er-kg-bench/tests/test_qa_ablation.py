"""End-to-end ER-ablation: build the store per dial, bridge-recall by hop.

Needs the goldengraph_native wheel -> skips locally, validates in the wheel-building
qa-ablation CI gate. The markdown render test (bottom) is wheel-free."""
from __future__ import annotations

import pytest
from erkgbench.qa_e2e.ablation import AblationResult, render_ablation_md


def test_ablation_monotonic_and_hop_widening():
    pytest.importorskip("goldengraph_native")
    from erkgbench.qa_e2e.ablation import run_ablation

    res = run_ablation(seed=7, n_questions=80, ambiguity=0.6, max_hops=4)
    oracle, gg, name_only, none = (
        res.recall[d] for d in ("oracle", "goldengraph", "name_only", "none")
    )
    # 1. monotone in ER quality (mean whole-chain recall)
    assert oracle["mean"] >= gg["mean"] >= name_only["mean"] - 1e-9 >= none["mean"] - 1e-9
    # 2. oracle-none gap widens with hops
    gap_lo = oracle["by_hop"][2] - none["by_hop"][2]
    gap_hi = oracle["by_hop"][4] - none["by_hop"][4]
    assert gap_hi > gap_lo
    # 3. resolver earns its keep (SOFT)
    assert gg["mean"] >= name_only["mean"]


# --- wheel-free: markdown rendering over a synthetic result ---


def _fake_result() -> AblationResult:
    def dial(mean, h2, h3, h4):
        return {"mean": mean, "by_hop": {2: h2, 3: h3, 4: h4}}

    return AblationResult(
        recall={
            "oracle": dial(0.90, 0.95, 0.90, 0.85),
            "goldengraph": dial(0.70, 0.85, 0.70, 0.55),
            "name_only": dial(0.65, 0.82, 0.66, 0.50),
            "none": dial(0.30, 0.60, 0.25, 0.10),
        }
    )


def test_render_ablation_md_has_dials_hops_and_verdicts():
    md = render_ablation_md(_fake_result())
    for dial in ("oracle", "goldengraph", "name_only", "none"):
        assert dial in md
    assert "2-hop" in md and "4-hop" in md
    # the three assertion verdict lines render
    assert "monotonic" in md.lower()
    assert "widen" in md.lower()
    assert "PASS" in md or "FAIL" in md
