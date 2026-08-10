"""Tests for the fixer module."""
import polars as pl
import pytest
from goldencheck.engine.fixer import (
    FixEntry,
    FixReport,
    _fix_smart_quotes,
    _normalize_unicode,
    _remove_invisible_chars,
    _trim_whitespace,
    apply_fixes,
)


def test_trim_whitespace():
    s = pl.Series("col", ["  hello ", "world  ", " foo "])
    result = _trim_whitespace(s)
    assert result.to_list() == ["hello", "world", "foo"]


def test_per_cell_fixes_short_circuit_returns_same_object_when_clean():
    """The invisible/unicode/smart-quote fixes vectorize-guard the slow per-cell
    pass: on a column with nothing to fix they return the SAME Series object, so
    `apply_fixes` can skip the full-frame comparison. (Perf-critical: this is
    what keeps the safe-fix pass off a per-cell `map_elements` over clean data.)"""
    clean = pl.Series("col", ["plain", "ascii", "text", None])
    for fix in (_remove_invisible_chars, _normalize_unicode, _fix_smart_quotes):
        assert fix(clean) is clean, f"{fix.__name__} should short-circuit on clean ASCII"


def test_per_cell_fixes_still_run_when_dirty():
    """The guard must NOT suppress a real fix: a single dirty cell triggers the
    per-cell pass for the whole column."""
    assert _remove_invisible_chars(pl.Series("c", ["a", "x​y"])).to_list() == ["a", "xy"]
    assert _normalize_unicode(pl.Series("c", ["a", "é"])).to_list() == ["a", "é"]
    assert _fix_smart_quotes(pl.Series("c", ["a", "“q”"])).to_list() == ["a", '"q"']


def test_clean_frame_safe_fix_makes_no_changes_and_no_report():
    """End-to-end: a clean frame yields zero fix entries (and, via the guards,
    pays no per-cell pass)."""
    df = pl.DataFrame({"name": ["Alice", "Bob", "Carol"], "n": [1, 2, 3]})
    fixed, report = apply_fixes(df, [], mode="safe")
    assert report.entries == []
    assert fixed["name"].to_list() == ["Alice", "Bob", "Carol"]


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


# ── #2448: the scan/fix asymmetry must name its cause ────────────────────────


class TestPolarsFrameGuard:
    """`scan_dataframe` is arrow-native, so `scan_dataframe(pa.Table)` succeeds
    and reasonably implies `apply_fixes(pa.Table, findings)` will too. It did
    not -- `df.clone()` raised `AttributeError: 'pyarrow.lib.Table' object has
    no attribute 'clone'`, naming neither polars nor the requirement (#2448)."""

    def _arrow(self):
        import pyarrow as pa
        return pa.table({"city": ["  St. Louis  ", None]})

    def test_arrow_table_raises_a_typed_error_naming_polars(self):
        from goldencheck.engine.fixer import apply_fixes

        with pytest.raises(TypeError) as ei:
            apply_fixes(self._arrow(), [])
        msg = str(ei.value)
        assert "requires a polars DataFrame" in msg
        assert "pyarrow.lib.Table" in msg
        assert "goldencheck[polars]" in msg
        assert "from_arrow" in msg

    def test_the_fix_functions_are_polars_typed_not_frame_agnostic(self):
        """#2448 reads the failure as 'the fixes themselves look frame-agnostic;
        it is the copy-then-mutate that is polars-shaped', and proposes skipping
        the clone for an immutable Arrow table. Pinning why that does not work:
        every fix takes and returns a pl.Series, so `.clone()` is the first of
        dozens of polars calls, not the only one. Skipping it would move the
        AttributeError to `result[col_name]`."""
        import inspect

        from goldencheck.engine.fixer import _SAFE_FIXES

        for name, fn in _SAFE_FIXES:
            sig = inspect.signature(fn)
            ann = [p.annotation for p in sig.parameters.values()]
            assert any("Series" in str(a) for a in ann), f"{name} is not Series-typed"
            assert "Series" in str(sig.return_annotation), name

    def test_polars_frame_still_works(self):
        import polars as pl
        from goldencheck.engine.fixer import apply_fixes

        fixed, report = apply_fixes(pl.DataFrame({"city": ["  St. Louis  "]}), [])
        assert fixed["city"][0] == "St. Louis"
        assert any(e.fix_type == "trim_whitespace" for e in report.entries)
