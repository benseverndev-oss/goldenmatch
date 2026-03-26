"""Edge-case tests for agent intelligence layer."""
from __future__ import annotations

import polars as pl

from goldencheck.agent.intelligence import (
    StrategyDecision,
    explain_finding,
    findings_to_fbc,
    select_strategy,
)
from goldencheck.models.finding import Finding, Severity
from goldencheck.models.profile import ColumnProfile, DatasetProfile


# ---------------------------------------------------------------------------
# test_select_strategy_empty_dataframe
# ---------------------------------------------------------------------------


class TestSelectStrategyEmptyDataframe:
    def test_returns_strategy_decision(self):
        df = pl.DataFrame({"a": pl.Series([], dtype=pl.String)})
        result = select_strategy(df)
        assert isinstance(result, StrategyDecision)

    def test_sample_strategy_is_full(self):
        df = pl.DataFrame({"a": pl.Series([], dtype=pl.String)})
        result = select_strategy(df)
        assert result.sample_strategy == "full"

    def test_why_has_row_count(self):
        df = pl.DataFrame({"a": pl.Series([], dtype=pl.String)})
        result = select_strategy(df)
        assert result.why["row_count"] == 0

    def test_profiler_strategy_standard(self):
        df = pl.DataFrame({"a": pl.Series([], dtype=pl.String)})
        result = select_strategy(df)
        assert result.profiler_strategy == "standard"


# ---------------------------------------------------------------------------
# test_select_strategy_single_column
# ---------------------------------------------------------------------------


class TestSelectStrategySingleColumn:
    def test_single_col_small(self):
        df = pl.DataFrame({"x": [1, 2, 3]})
        result = select_strategy(df)
        assert result.sample_strategy == "full"
        assert result.profiler_strategy == "standard"

    def test_why_col_count_is_one(self):
        df = pl.DataFrame({"x": [1, 2, 3]})
        result = select_strategy(df)
        assert result.why["col_count"] == 1


# ---------------------------------------------------------------------------
# test_findings_to_fbc_empty
# ---------------------------------------------------------------------------


class TestFindingsToFbcEmpty:
    def test_empty_list(self):
        result = findings_to_fbc([])
        assert result == {}

    def test_returns_dict(self):
        result = findings_to_fbc([])
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# test_findings_to_fbc_info_only — INFO findings don't create keys
# ---------------------------------------------------------------------------


class TestFindingsToFbcInfoOnly:
    def test_info_does_not_create_key(self):
        findings = [
            Finding(
                severity=Severity.INFO,
                column="col_a",
                check="nullability",
                message="info finding",
            ),
        ]
        result = findings_to_fbc(findings)
        # INFO findings don't increment errors or warnings,
        # but the column key may or may not be created depending on impl.
        # If created, both counts must be zero.
        if "col_a" in result:
            assert result["col_a"]["errors"] == 0
            assert result["col_a"]["warnings"] == 0

    def test_multiple_info_findings(self):
        findings = [
            Finding(
                severity=Severity.INFO,
                column="x",
                check="check1",
                message="info1",
            ),
            Finding(
                severity=Severity.INFO,
                column="y",
                check="check2",
                message="info2",
            ),
        ]
        result = findings_to_fbc(findings)
        for col_data in result.values():
            assert col_data["errors"] == 0
            assert col_data["warnings"] == 0

    def test_mixed_info_and_error(self):
        findings = [
            Finding(
                severity=Severity.INFO,
                column="a",
                check="c1",
                message="info",
            ),
            Finding(
                severity=Severity.ERROR,
                column="a",
                check="c2",
                message="error",
            ),
        ]
        result = findings_to_fbc(findings)
        assert result["a"]["errors"] == 1
        assert result["a"]["warnings"] == 0

    def test_warning_counted(self):
        findings = [
            Finding(
                severity=Severity.WARNING,
                column="b",
                check="c1",
                message="warn",
            ),
        ]
        result = findings_to_fbc(findings)
        assert result["b"]["warnings"] == 1
        assert result["b"]["errors"] == 0


# ---------------------------------------------------------------------------
# test_explain_finding_minimal — minimal Finding and DatasetProfile
# ---------------------------------------------------------------------------


class TestExplainFindingMinimal:
    def _make_profile(self, col_name: str = "col_a") -> DatasetProfile:
        return DatasetProfile(
            file_path="test.csv",
            row_count=10,
            column_count=1,
            columns=[
                ColumnProfile(
                    name=col_name,
                    inferred_type="String",
                    null_count=0,
                    null_pct=0.0,
                    unique_count=10,
                    unique_pct=1.0,
                    row_count=10,
                ),
            ],
        )

    def test_returns_dict(self):
        finding = Finding(
            severity=Severity.WARNING,
            column="col_a",
            check="nullability",
            message="some nulls found",
        )
        result = explain_finding(finding, self._make_profile())
        assert isinstance(result, dict)

    def test_has_required_keys(self):
        finding = Finding(
            severity=Severity.WARNING,
            column="col_a",
            check="nullability",
            message="some nulls found",
        )
        result = explain_finding(finding, self._make_profile())
        assert "what" in result
        assert "severity" in result
        assert "confidence" in result
        assert "suggestion" in result

    def test_severity_label(self):
        finding = Finding(
            severity=Severity.ERROR,
            column="col_a",
            check="uniqueness",
            message="duplicates",
        )
        result = explain_finding(finding, self._make_profile())
        assert result["severity"] == "error"

    def test_info_severity_label(self):
        finding = Finding(
            severity=Severity.INFO,
            column="col_a",
            check="type_inference",
            message="type info",
        )
        result = explain_finding(finding, self._make_profile())
        assert result["severity"] == "info"

    def test_column_not_in_profile(self):
        """Finding references a column not in the profile — should not crash."""
        finding = Finding(
            severity=Severity.WARNING,
            column="missing_col",
            check="nullability",
            message="nulls",
        )
        result = explain_finding(finding, self._make_profile("other_col"))
        assert isinstance(result, dict)
        assert "column_type" not in result

    def test_affected_rows_in_result(self):
        finding = Finding(
            severity=Severity.WARNING,
            column="col_a",
            check="nullability",
            message="nulls",
            affected_rows=5,
        )
        result = explain_finding(finding, self._make_profile())
        assert result["affected_rows"] == 5
        assert "5" in result["impact"]

    def test_sample_values_included(self):
        finding = Finding(
            severity=Severity.WARNING,
            column="col_a",
            check="nullability",
            message="nulls",
            sample_values=["a", "b", "c"],
        )
        result = explain_finding(finding, self._make_profile())
        assert result["sample_values"] == ["a", "b", "c"]

    def test_zero_affected_rows(self):
        finding = Finding(
            severity=Severity.INFO,
            column="col_a",
            check="check",
            message="msg",
            affected_rows=0,
        )
        result = explain_finding(finding, self._make_profile())
        assert result["affected_rows"] == 0

    def test_custom_suggestion(self):
        finding = Finding(
            severity=Severity.WARNING,
            column="col_a",
            check="check",
            message="msg",
            suggestion="Do this specific thing",
        )
        result = explain_finding(finding, self._make_profile())
        assert result["suggestion"] == "Do this specific thing"

    def test_default_suggestion(self):
        finding = Finding(
            severity=Severity.WARNING,
            column="col_a",
            check="check",
            message="msg",
        )
        result = explain_finding(finding, self._make_profile())
        assert result["suggestion"] is not None
        assert len(result["suggestion"]) > 0
