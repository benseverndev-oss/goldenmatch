"""The aggregation CLI parses; gate_exit_code reflects the hard verdicts. The full
run_aggregation_deterministic needs the wheel (covered by the gate lane)."""
from __future__ import annotations

from erkgbench.qa_e2e import run_aggregation
from erkgbench.qa_e2e.aggregation import AggregationResult, gate_exit_code


def test_parser_defaults():
    args = run_aggregation._parser().parse_args([])
    assert args.seed == 7 and args.n_anchors == 60 and args.passage_k == 10


def test_gate_exit_code_zero_on_large_gap():
    res = AggregationResult(
        gg_setf1={"2-4": 1.0, "11-20": 1.0},
        floor_setf1={"2-4": 0.3, "11-20": 0.5},  # large gap every bucket
        gg_count_acc={"2-4": 1.0, "11-20": 1.0},
        floor_recall={"2-4": 0.68, "11-20": 0.40},  # collapse is soft
    )
    assert gate_exit_code(res) == 0


def test_gate_exit_code_one_on_small_gap():
    res = AggregationResult(
        gg_setf1={"2-4": 1.0, "11-20": 1.0},
        floor_setf1={"2-4": 0.9, "11-20": 0.9},  # gap only 0.1 < 0.3 -> hard fail
        gg_count_acc={"2-4": 1.0, "11-20": 1.0},
    )
    assert gate_exit_code(res) == 1
