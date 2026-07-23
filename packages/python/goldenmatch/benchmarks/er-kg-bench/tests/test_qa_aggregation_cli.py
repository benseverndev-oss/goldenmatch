"""The aggregation CLI parses; gate_exit_code reflects the hard verdicts. The full
run_aggregation_deterministic needs the wheel (covered by the gate lane)."""
from __future__ import annotations

import pytest
from erkgbench.qa_e2e import run_aggregation
from erkgbench.qa_e2e.aggregation import AggregationResult, gate_exit_code


def test_parser_defaults():
    args = run_aggregation._parser().parse_args([])
    assert args.seed == 7 and args.n_anchors == 60 and args.passage_k == 10
    # additive --source default keeps the synthetic path byte-identical
    assert args.source == "synthetic" and args.fixture is None


def test_parser_accepts_realworld_source():
    args = run_aggregation._parser().parse_args(["--source", "realworld", "--fixture", "f.json"])
    assert args.source == "realworld" and args.fixture == "f.json"


def test_cli_realworld_writes_bucketed_table(tmp_path):
    try:
        import goldengraph_native  # noqa: F401
    except ImportError:
        pytest.skip("goldengraph-native wheel not installed")
    from erkgbench.qa_e2e.realworld import _FIXTURE_DIR

    out = tmp_path / "AGG.md"
    rc = run_aggregation.main([
        "--source", "realworld",
        "--fixture", str(_FIXTURE_DIR / "wikidata_companies_TINY.json"),
        "--ambiguity", "1.0", "--passage-k", "2", "--out-md", str(out),
    ])
    assert rc in (0, 1)  # gate verdict, not a crash
    md = out.read_text(encoding="utf-8")
    assert "size bucket" in md and "goldengraph set-F1" in md


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
