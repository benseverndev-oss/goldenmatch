"""Tests for benchmark/aggregate.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[5]
BENCHMARK_DIR = REPO_ROOT / "benchmark"
if str(BENCHMARK_DIR) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_DIR))

import aggregate  # noqa: E402


def _report(language: str, f1: float = 0.5, top1: float = 0.5, mrr: float = 0.5, ece: float = 0.1,
            per_case: list | None = None, failed_cases: list | None = None) -> dict:
    return {
        "version": 1,
        "language": language,
        "infermap_version": "0.1.0",
        "runner_version": "0.1.0",
        "ran_at": "2026-04-08T00:00:00Z",
        "duration_seconds": 1.0,
        "scorecard": {
            "overall": {"f1": f1, "top1": top1, "mrr": mrr, "ece": ece, "n": 10},
            "by_difficulty": {},
            "by_category": {},
            "by_tag": {},
        },
        "per_case": per_case or [],
        "failed_cases": failed_cases or [],
    }


def _baseline(py_f1: float = 0.5, ts_f1: float = 0.5, commit: str = "abcdef1234") -> dict:
    return {
        "version": 1,
        "updated_at": "2026-04-01T00:00:00Z",
        "commit": commit,
        "python": _report("python", f1=py_f1),
        "typescript": _report("typescript", f1=ts_f1),
    }


def _write(tmp_path: Path, name: str, data: dict) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


class TestVerdictClassification:
    def test_improved(self):
        delta = {"f1": 0.01, "top1": 0.01, "mrr": 0.01, "ece": -0.01}
        assert aggregate.classify_verdict(delta, threshold=0.02) == "✅ improved"

    def test_no_change(self):
        delta = {"f1": 0.001, "top1": 0.001, "mrr": 0.001, "ece": 0.001}
        assert aggregate.classify_verdict(delta, threshold=0.02) == "🟢 no change"

    def test_small_regression(self):
        delta = {"f1": -0.01, "top1": 0.0, "mrr": 0.0, "ece": 0.0}
        assert aggregate.classify_verdict(delta, threshold=0.02) == "🔻 regressed"

    def test_large_f1_regression(self):
        delta = {"f1": -0.05, "top1": 0.0, "mrr": 0.0, "ece": 0.0}
        assert aggregate.classify_verdict(delta, threshold=0.02) == "🛑 large regression"

    def test_mixed(self):
        delta = {"f1": 0.01, "top1": -0.01, "mrr": 0.0, "ece": 0.0}
        assert aggregate.classify_verdict(delta, threshold=0.02) == "⚠️ mixed"


class TestRenderComment:
    def test_first_run_no_baseline(self, tmp_path):
        py = _write(tmp_path, "py.json", _report("python", f1=0.75))
        ts = _write(tmp_path, "ts.json", _report("typescript", f1=0.72))
        md_out = tmp_path / "comment.md"

        result = aggregate.main_impl(
            python_path=py,
            ts_path=ts,
            baseline_path=None,
            markdown_path=md_out,
            output_path=None,
            regression_threshold=0.02,
            fail_over=None,
        )
        assert result == 0
        md = md_out.read_text(encoding="utf-8")
        assert md.startswith("<!-- infermap-benchmark comment-schema-version=1 -->")
        assert "infermap benchmark" in md
        assert "First run" in md or "first run" in md
        assert "0.750" in md
        assert "0.720" in md

    def test_headline_with_baseline(self, tmp_path):
        py = _write(tmp_path, "py.json", _report("python", f1=0.80))
        ts = _write(tmp_path, "ts.json", _report("typescript", f1=0.78))
        baseline = _write(tmp_path, "baseline.json", _baseline(py_f1=0.75, ts_f1=0.72))
        md_out = tmp_path / "comment.md"

        aggregate.main_impl(
            python_path=py,
            ts_path=ts,
            baseline_path=baseline,
            markdown_path=md_out,
            output_path=None,
            regression_threshold=0.02,
            fail_over=None,
        )
        md = md_out.read_text(encoding="utf-8")
        assert "+0.050" in md
        assert "+0.060" in md
        assert "✅ improved" in md
        assert "abcdef1" in md

    def test_runner_failure_shown(self, tmp_path):
        ts = _write(tmp_path, "ts.json", _report("typescript", f1=0.75))
        md_out = tmp_path / "comment.md"

        aggregate.main_impl(
            python_path=tmp_path / "nonexistent_py.json",
            ts_path=ts,
            baseline_path=None,
            markdown_path=md_out,
            output_path=None,
            regression_threshold=0.02,
            fail_over=None,
        )
        md = md_out.read_text(encoding="utf-8")
        assert "runner failed" in md
        assert "0.750" in md

    def test_crashed_cases_section(self, tmp_path):
        py = _write(tmp_path, "py.json", _report(
            "python",
            per_case=[{"id": "crashed_case", "f1": 0, "top1": 0, "mrr": 0,
                       "expected_n": 1, "predicted_n": 0, "true_positives": 0,
                       "false_positives": 0, "false_negatives": 1,
                       "failed": True, "failure_reason": "ScorerException: boom"}],
            failed_cases=["crashed_case"],
        ))
        ts = _write(tmp_path, "ts.json", _report("typescript"))
        md_out = tmp_path / "comment.md"

        aggregate.main_impl(
            python_path=py, ts_path=ts, baseline_path=None,
            markdown_path=md_out, output_path=None,
            regression_threshold=0.02, fail_over=None,
        )
        md = md_out.read_text(encoding="utf-8")
        assert "crashed_case" in md
        assert "ScorerException" in md


class TestDeltaOutput:
    def test_writes_delta_json(self, tmp_path):
        py = _write(tmp_path, "py.json", _report("python", f1=0.80))
        ts = _write(tmp_path, "ts.json", _report("typescript", f1=0.78))
        baseline = _write(tmp_path, "baseline.json", _baseline(py_f1=0.75, ts_f1=0.72))
        md_out = tmp_path / "comment.md"
        delta_out = tmp_path / "delta.json"

        aggregate.main_impl(
            python_path=py, ts_path=ts, baseline_path=baseline,
            markdown_path=md_out, output_path=delta_out,
            regression_threshold=0.02, fail_over=None,
        )
        delta = json.loads(delta_out.read_text(encoding="utf-8"))
        assert "python" in delta
        assert "typescript" in delta
        assert abs(delta["python"]["overall"]["f1"] - 0.05) < 1e-9


class TestFailOver:
    def test_fail_over_triggers(self, tmp_path):
        py = _write(tmp_path, "py.json", _report("python", f1=0.60))
        ts = _write(tmp_path, "ts.json", _report("typescript", f1=0.80))
        baseline = _write(tmp_path, "baseline.json", _baseline(py_f1=0.75, ts_f1=0.78))
        md_out = tmp_path / "comment.md"

        result = aggregate.main_impl(
            python_path=py, ts_path=ts, baseline_path=baseline,
            markdown_path=md_out, output_path=None,
            regression_threshold=0.02, fail_over=0.05,
        )
        assert result == 1

    def test_fail_over_clean_under_threshold(self, tmp_path):
        py = _write(tmp_path, "py.json", _report("python", f1=0.74))
        ts = _write(tmp_path, "ts.json", _report("typescript", f1=0.77))
        baseline = _write(tmp_path, "baseline.json", _baseline(py_f1=0.75, ts_f1=0.78))
        md_out = tmp_path / "comment.md"

        result = aggregate.main_impl(
            python_path=py, ts_path=ts, baseline_path=baseline,
            markdown_path=md_out, output_path=None,
            regression_threshold=0.02, fail_over=0.05,
        )
        assert result == 0
