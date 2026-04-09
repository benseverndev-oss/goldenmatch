"""Tests for benchmark/check_regression.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[5]
BENCHMARK_DIR = REPO_ROOT / "benchmark"
if str(BENCHMARK_DIR) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_DIR))

import check_regression  # noqa: E402


def _delta(python_f1: float = 0.0, ts_f1: float = 0.0) -> dict:
    return {
        "python": {"overall": {"f1": python_f1, "top1": 0, "mrr": 0, "ece": 0}},
        "typescript": {"overall": {"f1": ts_f1, "top1": 0, "mrr": 0, "ece": 0}},
    }


class TestCheckRegression:
    def test_no_regression(self, tmp_path):
        p = tmp_path / "delta.json"
        p.write_text(json.dumps(_delta(python_f1=0.01, ts_f1=0.02)), encoding="utf-8")
        result = check_regression.main_impl(p, fail_over=0.02)
        assert result == 0

    def test_small_regression_within_threshold(self, tmp_path):
        p = tmp_path / "delta.json"
        p.write_text(json.dumps(_delta(python_f1=-0.01, ts_f1=-0.01)), encoding="utf-8")
        result = check_regression.main_impl(p, fail_over=0.02)
        assert result == 0

    def test_large_regression_exceeds_threshold(self, tmp_path):
        p = tmp_path / "delta.json"
        p.write_text(json.dumps(_delta(python_f1=-0.05, ts_f1=0.0)), encoding="utf-8")
        result = check_regression.main_impl(p, fail_over=0.02)
        assert result == 1

    def test_either_language_can_trip(self, tmp_path):
        p = tmp_path / "delta.json"
        p.write_text(json.dumps(_delta(python_f1=0.0, ts_f1=-0.10)), encoding="utf-8")
        result = check_regression.main_impl(p, fail_over=0.02)
        assert result == 1

    def test_missing_file_is_graceful(self, tmp_path):
        p = tmp_path / "nonexistent.json"
        result = check_regression.main_impl(p, fail_over=0.02)
        assert result == 0
