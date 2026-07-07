"""Pure (no wheel, no LLM) tests for the ER->answer ablation SWEEP layer.

The per-ambiguity primitive (`scorecard_llm.answer_match_ablation`) already exists and
is tested elsewhere. These cover the thin sweep layer this experiment adds: reshape the
per-ambiguity dicts into per-dial curves, compute the ER->answer delta vs the `none` dial,
and render the World-A/World-B verdict (does the delta SURVIVE rising ambiguity).
"""
from __future__ import annotations

from erkgbench.qa_e2e.answer_ablation_sweep import (
    DELTA_HOLD_FRAC,
    aggregate_sweep,
    render_sweep_md,
    sweep_verdict,
)

_DIALS = ("oracle", "goldengraph", "name_only", "none")


def _ab(am: dict, br: dict) -> dict:
    """An `answer_match_ablation`-shaped dict from per-dial (answer_match, bridge_recall) means."""
    return {
        d: {
            "answer_match": {"mean": am[d], "by_hop": {}},
            "bridge_recall": {"mean": br[d], "by_hop": {}},
        }
        for d in _DIALS
    }


# World A: monotonic in ER at every ambiguity, and delta_oracle (oracle-none) HOLDS.
_WORLD_A = {
    0.0: _ab({"oracle": 0.90, "goldengraph": 0.70, "name_only": 0.50, "none": 0.50},
             {"oracle": 1.00, "goldengraph": 0.60, "name_only": 0.25, "none": 0.20}),
    1.0: _ab({"oracle": 0.50, "goldengraph": 0.40, "name_only": 0.15, "none": 0.15},
             {"oracle": 0.90, "goldengraph": 0.45, "name_only": 0.20, "none": 0.15}),
}

# World B: still monotonic, but delta_oracle COLLAPSES toward 0 as ambiguity rises.
_WORLD_B = {
    0.0: _ab({"oracle": 0.90, "goldengraph": 0.70, "name_only": 0.50, "none": 0.50},
             {"oracle": 1.00, "goldengraph": 0.60, "name_only": 0.25, "none": 0.20}),
    1.0: _ab({"oracle": 0.20, "goldengraph": 0.20, "name_only": 0.18, "none": 0.18},
             {"oracle": 0.90, "goldengraph": 0.45, "name_only": 0.20, "none": 0.15}),
}

# Non-monotonic: `none` beats the resolved dials at high ambiguity (HARD assertion must fail).
_NON_MONOTONIC = {
    0.0: _ab({"oracle": 0.90, "goldengraph": 0.70, "name_only": 0.50, "none": 0.50},
             {"oracle": 1.00, "goldengraph": 0.60, "name_only": 0.25, "none": 0.20}),
    1.0: _ab({"oracle": 0.30, "goldengraph": 0.30, "name_only": 0.30, "none": 0.90},
             {"oracle": 0.90, "goldengraph": 0.45, "name_only": 0.20, "none": 0.15}),
}


def test_aggregate_sweep_shapes():
    sw = aggregate_sweep(_WORLD_A)
    assert sw.ambiguities == (0.0, 1.0)
    for d in _DIALS:
        assert set(sw.answer_match[d]) == {0.0, 1.0}
        assert set(sw.bridge_recall[d]) == {0.0, 1.0}
    # delta == answer_match[dial] - answer_match[none], per ambiguity
    assert sw.delta["oracle"][0.0] == 0.90 - 0.50
    assert abs(sw.delta["oracle"][1.0] - (0.50 - 0.15)) < 1e-12
    assert sw.delta["none"][0.0] == 0.0


def _verdict(res):
    return {label: (passed, is_hard) for label, passed, is_hard in res}


def test_verdict_world_a_delta_holds():
    res = sweep_verdict(aggregate_sweep(_WORLD_A))
    hard = [(p) for _l, p, is_hard in res if is_hard]
    soft = [(p) for _l, p, is_hard in res if not is_hard]
    assert all(hard)  # monotonic holds
    assert any(soft)  # the delta-holds (World A) verdict passes


def test_verdict_world_b_delta_collapses():
    res = sweep_verdict(aggregate_sweep(_WORLD_B))
    hard = [p for _l, p, is_hard in res if is_hard]
    soft = [p for _l, p, is_hard in res if not is_hard]
    assert all(hard)  # still monotonic at every ambiguity
    assert not any(soft)  # delta collapses -> World B (reposition)


def test_verdict_monotonic_hard_fails_when_none_wins():
    res = sweep_verdict(aggregate_sweep(_NON_MONOTONIC))
    hard = [p for _l, p, is_hard in res if is_hard]
    assert not all(hard)  # the HARD monotonic assertion fails


def test_delta_hold_frac_is_the_knob():
    # A delta that lands exactly on the boundary passes; just under fails.
    sw = aggregate_sweep(_WORLD_A)
    d_lo = sw.delta["oracle"][0.0]
    d_hi = sw.delta["oracle"][1.0]
    assert d_hi >= DELTA_HOLD_FRAC * d_lo  # World A fixture sits above the boundary


def test_render_sweep_md_has_table_and_verdict():
    md = render_sweep_md(aggregate_sweep(_WORLD_A), model="gpt-4o-mini")
    assert "gpt-4o-mini" in md
    # dial rows + an ambiguity column header + a delta row + a verdict tag
    for d in _DIALS:
        assert d in md
    assert "delta" in md.lower()
    assert "0" in md and "1" in md  # ambiguity columns
    assert "[PASS]" in md or "[WARN]" in md
