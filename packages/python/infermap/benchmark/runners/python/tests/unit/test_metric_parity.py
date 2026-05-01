"""Assert Python metrics agree with the shared metric_expected.json corpus."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from infermap_bench.metrics import (
    MetricInput,
    expected_calibration_error,
    extract_predictions,
    f1_per_case,
    mean_reciprocal_rank,
    top1_accuracy,
)

REPO_ROOT = Path(__file__).resolve().parents[5]
INPUTS_PATH = REPO_ROOT / "benchmark" / "tests" / "parity" / "metric_inputs.json"
EXPECTED_PATH = REPO_ROOT / "benchmark" / "tests" / "parity" / "metric_expected.json"

TOLERANCE = 1e-6


def _build_input(raw: dict) -> MetricInput:
    return MetricInput(
        source_fields=raw["source_fields"],
        target_fields=raw["target_fields"],
        expected_mappings=raw["expected_mappings"],
        expected_unmapped_source=raw["expected_unmapped_source"],
        expected_unmapped_target=raw["expected_unmapped_target"],
        actual_mappings=raw["actual_mappings"],
        score_matrix=raw["score_matrix"],
        min_confidence=raw["min_confidence"],
    )


@pytest.fixture(scope="module")
def parity_data():
    inputs = json.loads(INPUTS_PATH.read_text(encoding="utf-8"))
    expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
    expected_map = {e["name"]: e for e in expected["expected"]}
    return inputs["inputs"], expected_map


def test_corpus_files_load(parity_data):
    inputs, expected_map = parity_data
    assert len(inputs) >= 5
    assert len(expected_map) >= 5


def test_every_input_has_expected(parity_data):
    inputs, expected_map = parity_data
    for inp in inputs:
        assert inp["name"] in expected_map, f"Missing expected entry for {inp['name']}"


def test_hand_computed_floor(parity_data):
    """At least 5 entries must be hand_computed per spec §13.3."""
    _, expected_map = parity_data
    hand_computed = [e for e in expected_map.values() if e.get("hand_computed") is True]
    assert len(hand_computed) >= 5, (
        f"Only {len(hand_computed)} hand-computed entries — the anti-drift floor is 5."
    )


@pytest.mark.parametrize("name", [
    "perfect_small",
    "all_wrong_small",
    "half_right_triangle",
    "all_unmapped_perfect",
    "tie_break_test",
])
def test_metric_matches_expected(parity_data, name):
    inputs, expected_map = parity_data
    inp_raw = next(i for i in inputs if i["name"] == name)
    exp = expected_map[name]

    mi = _build_input(inp_raw["input"])

    assert abs(top1_accuracy(mi) - exp["top1"]) < TOLERANCE, (
        f"{name}: top1 mismatch — got {top1_accuracy(mi)}, expected {exp['top1']}"
    )

    tp, fp, fn = f1_per_case(mi)
    assert tp == exp["f1_per_case"]["tp"], f"{name}: tp mismatch"
    assert fp == exp["f1_per_case"]["fp"], f"{name}: fp mismatch"
    assert fn == exp["f1_per_case"]["fn"], f"{name}: fn mismatch"

    mrr = mean_reciprocal_rank(mi)
    assert abs(mrr - exp["mrr"]) < TOLERANCE, (
        f"{name}: mrr mismatch — got {mrr}, expected {exp['mrr']}"
    )

    preds = extract_predictions(mi)
    ece = expected_calibration_error(preds)
    assert abs(ece - exp["ece"]) < TOLERANCE, (
        f"{name}: ece mismatch — got {ece}, expected {exp['ece']}"
    )
