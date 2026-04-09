"""Tests for report construction and schema validation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from infermap_bench.metrics import Prediction
from infermap_bench.report import (
    CaseResult,
    build_report,
    validate_report,
    write_report,
)

REPO_ROOT = Path(__file__).resolve().parents[5]
SCHEMA_PATH = REPO_ROOT / "benchmark" / "report.schema.json"


def _result(
    case_id: str,
    category: str = "valentine",
    difficulty: str = "easy",
    tags: list[str] | None = None,
    f1: float = 0.5,
    top1: float = 0.5,
    mrr: float = 0.5,
    tp: int = 1,
    fp: int = 1,
    fn: int = 1,
    predictions: list[Prediction] | None = None,
    failed: bool = False,
    failure_reason: str | None = None,
) -> CaseResult:
    return CaseResult(
        case_id=case_id,
        category=category,
        subcategory="test",
        difficulty=difficulty,
        tags=tags or ["alias_dominant"],
        top1=top1,
        f1=f1,
        mrr=mrr,
        tp=tp,
        fp=fp,
        fn=fn,
        predictions=predictions or [],
        failed=failed,
        failure_reason=failure_reason,
    )


class TestBuildReport:
    def test_minimal_report(self):
        results = [_result("a", f1=1.0, top1=1.0, mrr=1.0, tp=2, fp=0, fn=0)]
        report = build_report(
            results,
            language="python",
            infermap_version="0.1.0",
            runner_version="0.1.0",
            duration_seconds=1.5,
        )
        assert report["version"] == 1
        assert report["language"] == "python"
        assert report["infermap_version"] == "0.1.0"
        assert report["runner_version"] == "0.1.0"
        assert isinstance(report["ran_at"], str)
        assert report["duration_seconds"] == 1.5
        assert "scorecard" in report
        assert "per_case" in report
        assert len(report["per_case"]) == 1
        assert report["failed_cases"] == []

    def test_overall_scorecard_computed(self):
        results = [
            _result("a", f1=1.0, top1=1.0, mrr=1.0, tp=2, fp=0, fn=0),
            _result("b", f1=0.0, top1=0.0, mrr=0.0, tp=0, fp=2, fn=2),
        ]
        report = build_report(
            results, language="python", infermap_version="0.1.0",
            runner_version="0.1.0", duration_seconds=1.0,
        )
        overall = report["scorecard"]["overall"]
        assert overall["f1"] == 0.5
        assert overall["top1"] == 0.5
        assert overall["n"] == 2

    def test_by_difficulty_slice(self):
        results = [
            _result("a", difficulty="easy", f1=0.9, tp=1, fp=0, fn=0),
            _result("b", difficulty="hard", f1=0.3, tp=1, fp=2, fn=2),
        ]
        report = build_report(
            results, language="python", infermap_version="0.1.0",
            runner_version="0.1.0", duration_seconds=1.0,
        )
        sc = report["scorecard"]
        assert "easy" in sc["by_difficulty"]
        assert "hard" in sc["by_difficulty"]
        assert "medium" not in sc["by_difficulty"]
        assert sc["by_difficulty"]["easy"]["n"] == 1
        assert sc["by_difficulty"]["hard"]["n"] == 1

    def test_by_category_slice(self):
        results = [
            _result("a", category="valentine", tp=1, fp=0, fn=0),
            _result("b", category="synthetic", tp=1, fp=1, fn=1),
        ]
        report = build_report(
            results, language="python", infermap_version="0.1.0",
            runner_version="0.1.0", duration_seconds=1.0,
        )
        cats = report["scorecard"]["by_category"]
        assert cats["valentine"]["n"] == 1
        assert cats["synthetic"]["n"] == 1
        assert "real_world" not in cats

    def test_by_tag_multi_tag_case(self):
        results = [
            _result("a", tags=["alias_dominant", "small"], tp=1, fp=0, fn=0),
            _result("b", tags=["small"], tp=0, fp=0, fn=1),
        ]
        report = build_report(
            results, language="python", infermap_version="0.1.0",
            runner_version="0.1.0", duration_seconds=1.0,
        )
        tags = report["scorecard"]["by_tag"]
        assert tags["alias_dominant"]["n"] == 1
        assert tags["small"]["n"] == 2

    def test_failed_cases_listed(self):
        results = [
            _result("ok"),
            _result("broken", failed=True, failure_reason="KaboomError: nope"),
        ]
        report = build_report(
            results, language="python", infermap_version="0.1.0",
            runner_version="0.1.0", duration_seconds=1.0,
        )
        assert report["failed_cases"] == ["broken"]
        per_case_broken = next(p for p in report["per_case"] if p["id"] == "broken")
        assert per_case_broken["failed"] is True
        assert per_case_broken["failure_reason"] == "KaboomError: nope"

    def test_empty_results_produces_valid_report(self):
        report = build_report(
            [], language="python", infermap_version="0.1.0",
            runner_version="0.1.0", duration_seconds=0.0,
        )
        assert report["scorecard"]["overall"]["n"] == 0
        assert report["scorecard"]["by_difficulty"] == {}
        assert report["per_case"] == []
        assert report["failed_cases"] == []

    def test_per_case_fields_rounded(self):
        results = [_result("a", f1=0.123456789, top1=0.987654321, mrr=0.555555555)]
        report = build_report(
            results, language="python", infermap_version="0.1.0",
            runner_version="0.1.0", duration_seconds=1.0,
        )
        pc = report["per_case"][0]
        assert pc["f1"] == 0.123457
        assert pc["top1"] == 0.987654
        assert pc["mrr"] == 0.555556

    def test_per_case_expected_and_predicted_n(self):
        results = [_result("a", tp=3, fp=1, fn=2)]
        report = build_report(
            results, language="python", infermap_version="0.1.0",
            runner_version="0.1.0", duration_seconds=1.0,
        )
        pc = report["per_case"][0]
        assert pc["expected_n"] == 5
        assert pc["predicted_n"] == 4
        assert pc["true_positives"] == 3


class TestValidate:
    def test_valid_report_passes_schema(self):
        results = [_result("a")]
        report = build_report(
            results, language="python", infermap_version="0.1.0",
            runner_version="0.1.0", duration_seconds=1.0,
        )
        validate_report(report, SCHEMA_PATH)

    def test_empty_report_passes_schema(self):
        report = build_report(
            [], language="python", infermap_version="0.1.0",
            runner_version="0.1.0", duration_seconds=0.0,
        )
        validate_report(report, SCHEMA_PATH)

    def test_ts_language_passes_schema(self):
        report = build_report(
            [], language="typescript", infermap_version="0.1.0",
            runner_version="0.1.0", duration_seconds=0.0,
        )
        validate_report(report, SCHEMA_PATH)

    def test_invalid_report_fails_schema(self):
        bad = {"version": 1, "language": "python"}
        with pytest.raises(Exception):
            validate_report(bad, SCHEMA_PATH)

    def test_invalid_language_fails_schema(self):
        report = build_report(
            [], language="python", infermap_version="0.1.0",
            runner_version="0.1.0", duration_seconds=0.0,
        )
        report["language"] = "rust"
        with pytest.raises(Exception):
            validate_report(report, SCHEMA_PATH)


class TestWriteReport:
    def test_writes_valid_report(self, tmp_path):
        results = [_result("a")]
        report = build_report(
            results, language="python", infermap_version="0.1.0",
            runner_version="0.1.0", duration_seconds=1.0,
        )
        out = tmp_path / "report.json"
        write_report(report, out, SCHEMA_PATH)
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["version"] == 1

    def test_write_refuses_invalid_report(self, tmp_path):
        bad = {"version": 1}
        out = tmp_path / "report.json"
        with pytest.raises(Exception):
            write_report(bad, out, SCHEMA_PATH)
        assert not out.exists()
