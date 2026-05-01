"""Tests for the fixer module."""
import polars as pl
import pytest
from goldencheck.engine.fixer import (
    FixReport,
    FixEntry,
    apply_fixes,
    _trim_whitespace,
    _remove_invisible_chars,
    _normalize_unicode,
    _fix_smart_quotes,
)


def test_trim_whitespace():
    s = pl.Series("col", ["  hello ", "world  ", " foo "])
    result = _trim_whitespace(s)
    assert result.to_list() == ["hello", "world", "foo"]


def test_trim_whitespace_no_change():
    s = pl.Series("col", ["hello", "world"])
    result = _trim_whitespace(s)
    assert result.to_list() == ["hello", "world"]


def test_remove_invisible_chars():
    s = pl.Series("col", ["hel\u200blo", "wor\uFEFFld", "normal"])
    result = _remove_invisible_chars(s)
    assert result.to_list() == ["hello", "world", "normal"]


def test_normalize_unicode():
    s = pl.Series("col", ["cafe\u0301", "normal"])
    result = _normalize_unicode(s)
    assert result.to_list() == ["caf\u00e9", "normal"]


def test_fix_smart_quotes():
    s = pl.Series("col", ["\u201chello\u201d", "\u2018world\u2019"])
    result = _fix_smart_quotes(s)
    assert result.to_list() == ['"hello"', "'world'"]


def test_apply_fixes_safe_mode():
    df = pl.DataFrame({"name": ["  Alice ", "Bob\u200b"], "age": [25, 30]})
    findings = []
    result_df, report = apply_fixes(df, findings, mode="safe")
    assert result_df["name"].to_list() == ["Alice", "Bob"]
    assert len(report.entries) > 0


def test_apply_fixes_no_changes():
    df = pl.DataFrame({"name": ["Alice", "Bob"], "age": [25, 30]})
    findings = []
    result_df, report = apply_fixes(df, findings, mode="safe")
    assert len(report.entries) == 0


def test_apply_fixes_aggressive_requires_force():
    df = pl.DataFrame({"name": ["Alice"]})
    with pytest.raises(ValueError, match="(?i)aggressive"):
        apply_fixes(df, [], mode="aggressive", force=False)


def test_apply_fixes_aggressive_with_force():
    df = pl.DataFrame({"name": ["Alice"]})
    result_df, report = apply_fixes(df, [], mode="aggressive", force=True)
    assert isinstance(report, FixReport)


def test_fix_report_total():
    report = FixReport(entries=[
        FixEntry(column="a", fix_type="trim", rows_affected=5),
        FixEntry(column="b", fix_type="trim", rows_affected=3),
    ])
    assert report.total_rows_fixed == 8


def test_trim_whitespace_skips_numeric():
    s = pl.Series("col", [1, 2, 3])
    result = _trim_whitespace(s)
    assert result.to_list() == [1, 2, 3]
