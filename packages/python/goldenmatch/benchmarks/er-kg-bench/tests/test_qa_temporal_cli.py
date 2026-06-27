"""The temporal CLI parses; gate_exit_code reflects the hard verdicts. The full
run_temporal_deterministic needs the wheel (covered by the gate lane)."""
from __future__ import annotations

from erkgbench.qa_e2e import run_temporal
from erkgbench.qa_e2e.temporal import TemporalResult, gate_exit_code


def test_parser_defaults():
    args = run_temporal._parser().parse_args([])
    assert args.seed == 7 and args.n_facts == 40 and args.ambiguity == 0.6


def test_gate_exit_code_zero_when_gg_beats_floor_on_past():
    res = TemporalResult(gg_acc={"past": 1.0, "current": 1.0},
                         floor_acc={"past": 0.0, "current": 1.0})
    assert gate_exit_code(res) == 0


def test_gate_exit_code_one_when_floor_right_on_past():
    res = TemporalResult(gg_acc={"past": 1.0, "current": 1.0},
                         floor_acc={"past": 1.0, "current": 1.0})  # no past gap
    assert gate_exit_code(res) == 1
