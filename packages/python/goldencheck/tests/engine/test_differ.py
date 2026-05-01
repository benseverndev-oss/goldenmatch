import polars as pl

from goldencheck.engine.differ import diff_files, format_diff_report, DiffReport
from goldencheck.models.finding import Finding, Severity


def _make_finding(column: str, check: str, severity: Severity, rows: int = 10) -> Finding:
    return Finding(
        severity=severity,
        column=column,
        check=check,
        message=f"{check} issue in {column}",
        affected_rows=rows,
    )


# --- Schema changes ---

def test_schema_added_column():
    old = pl.DataFrame({"a": [1, 2]})
    new = pl.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    report = diff_files(old, new, [], [])
    added = [c for c in report.schema_changes if c.change_type == "added"]
    assert len(added) == 1
    assert added[0].column == "b"


def test_schema_removed_column():
    old = pl.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    new = pl.DataFrame({"a": [1, 2]})
    report = diff_files(old, new, [], [])
    removed = [c for c in report.schema_changes if c.change_type == "removed"]
    assert len(removed) == 1
    assert removed[0].column == "b"


def test_schema_type_changed():
    old = pl.DataFrame({"a": pl.Series([1, 2], dtype=pl.Int64)})
    new = pl.DataFrame({"a": pl.Series([1.0, 2.0], dtype=pl.Float64)})
    report = diff_files(old, new, [], [])
    changed = [c for c in report.schema_changes if c.change_type == "type_changed"]
    assert len(changed) == 1
    assert changed[0].column == "a"
    assert "Int64" in changed[0].old_type
    assert "Float64" in changed[0].new_type


def test_schema_no_changes():
    df = pl.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    report = diff_files(df, df, [], [])
    assert report.schema_changes == []


# --- Finding changes ---

def test_new_finding():
    old_findings: list[Finding] = []
    new_findings = [_make_finding("col", "null_check", Severity.ERROR)]
    report = diff_files(pl.DataFrame({"col": [1]}), pl.DataFrame({"col": [1]}), old_findings, new_findings)
    new_changes = [c for c in report.finding_changes if c.change_type == "new"]
    assert len(new_changes) == 1
    assert new_changes[0].column == "col"


def test_resolved_finding():
    old_findings = [_make_finding("col", "null_check", Severity.WARNING)]
    new_findings: list[Finding] = []
    report = diff_files(pl.DataFrame({"col": [1]}), pl.DataFrame({"col": [1]}), old_findings, new_findings)
    resolved = [c for c in report.finding_changes if c.change_type == "resolved"]
    assert len(resolved) == 1


def test_worsened_finding_severity():
    old_findings = [_make_finding("col", "chk", Severity.WARNING, rows=10)]
    new_findings = [_make_finding("col", "chk", Severity.ERROR, rows=10)]
    report = diff_files(pl.DataFrame({"col": [1]}), pl.DataFrame({"col": [1]}), old_findings, new_findings)
    worsened = [c for c in report.finding_changes if c.change_type == "worsened"]
    assert len(worsened) == 1


def test_worsened_finding_rows():
    """Rows increase by more than 50% triggers worsened."""
    old_findings = [_make_finding("col", "chk", Severity.WARNING, rows=10)]
    new_findings = [_make_finding("col", "chk", Severity.WARNING, rows=20)]
    report = diff_files(pl.DataFrame({"col": [1]}), pl.DataFrame({"col": [1]}), old_findings, new_findings)
    worsened = [c for c in report.finding_changes if c.change_type == "worsened"]
    assert len(worsened) == 1


def test_improved_finding_severity():
    old_findings = [_make_finding("col", "chk", Severity.ERROR, rows=10)]
    new_findings = [_make_finding("col", "chk", Severity.WARNING, rows=10)]
    report = diff_files(pl.DataFrame({"col": [1]}), pl.DataFrame({"col": [1]}), old_findings, new_findings)
    improved = [c for c in report.finding_changes if c.change_type == "improved"]
    assert len(improved) == 1


def test_improved_finding_rows():
    """Rows decrease by more than 50% triggers improved."""
    old_findings = [_make_finding("col", "chk", Severity.WARNING, rows=20)]
    new_findings = [_make_finding("col", "chk", Severity.WARNING, rows=5)]
    report = diff_files(pl.DataFrame({"col": [1]}), pl.DataFrame({"col": [1]}), old_findings, new_findings)
    improved = [c for c in report.finding_changes if c.change_type == "improved"]
    assert len(improved) == 1


def test_info_findings_ignored():
    """INFO findings are not tracked in finding changes."""
    old_findings = [_make_finding("col", "chk", Severity.INFO)]
    new_findings = [_make_finding("col", "chk", Severity.INFO)]
    report = diff_files(pl.DataFrame({"col": [1]}), pl.DataFrame({"col": [1]}), old_findings, new_findings)
    assert report.finding_changes == []


# --- Stat changes ---

def test_row_count_change():
    old = pl.DataFrame({"a": [1, 2, 3]})
    new = pl.DataFrame({"a": [1, 2, 3, 4, 5]})
    report = diff_files(old, new, [], [])
    row_stats = [c for c in report.stat_changes if c.metric == "Rows"]
    assert len(row_stats) == 1
    assert row_stats[0].old_value == "3"
    assert row_stats[0].new_value == "5"


def test_column_count_change():
    old = pl.DataFrame({"a": [1], "b": [2]})
    new = pl.DataFrame({"a": [1], "b": [2], "c": [3]})
    report = diff_files(old, new, [], [])
    col_stats = [c for c in report.stat_changes if c.metric == "Columns"]
    assert len(col_stats) == 1
    assert col_stats[0].delta == "+1"


def test_no_stat_changes():
    df = pl.DataFrame({"a": [1, 2]})
    report = diff_files(df, df, [], [])
    assert report.stat_changes == []


# --- format_diff_report ---

def test_format_empty_report():
    report = DiffReport()
    text = format_diff_report(report)
    assert "No changes detected" in text


def test_format_with_label():
    report = DiffReport()
    text = format_diff_report(report, label="v1 vs v2")
    assert "v1 vs v2" in text


def test_format_report_with_all_sections():
    old = pl.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    new = pl.DataFrame({"a": [1.0, 2.0, 3.0], "c": ["p", "q", "r"]})
    old_f = [_make_finding("a", "range", Severity.ERROR, 5)]
    new_f = [_make_finding("a", "range", Severity.WARNING, 5)]
    report = diff_files(old, new, old_f, new_f)
    text = format_diff_report(report, label="test")
    assert "Schema changes:" in text
    assert "Finding changes:" in text
    assert "Stats:" in text
