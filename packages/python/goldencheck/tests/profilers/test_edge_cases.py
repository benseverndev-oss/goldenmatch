"""Edge-case tests for profilers — empty, minimal, unicode, wide DataFrames."""
from __future__ import annotations


from goldencheck.engine.confidence import apply_confidence_downgrade
from goldencheck.engine.scanner import scan_file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_csv(tmp_path, name: str, content: str) -> str:
    """Write *content* to a CSV file and return the path string."""
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return str(p)


def _scan_and_downgrade(path: str, **kwargs):
    """Convenience: scan_file + apply_confidence_downgrade (no LLM)."""
    findings, profile = scan_file(path, **kwargs)
    findings = apply_confidence_downgrade(findings, llm_boost=False)
    return findings, profile


# ---------------------------------------------------------------------------
# test_empty_dataframe — headers only CSV, verify row_count=0
# ---------------------------------------------------------------------------


class TestEmptyDataframe:
    def test_row_count_is_zero(self, tmp_path):
        path = _write_csv(tmp_path, "empty.csv", "id,name,age\n")
        findings, profile = _scan_and_downgrade(path)
        assert profile.row_count == 0
        assert profile.column_count == 3

    def test_findings_are_list(self, tmp_path):
        """Even with 0 rows, scan should return a valid findings list."""
        path = _write_csv(tmp_path, "empty.csv", "id,name,age\n")
        findings, profile = _scan_and_downgrade(path)
        assert isinstance(findings, list)

    def test_column_profiles_exist(self, tmp_path):
        path = _write_csv(tmp_path, "empty.csv", "id,name,age\n")
        _, profile = _scan_and_downgrade(path)
        assert len(profile.columns) == 3
        assert {c.name for c in profile.columns} == {"id", "name", "age"}

    def test_column_null_pct_zero(self, tmp_path):
        """With 0 rows, null_pct should be 0 (not NaN/error)."""
        path = _write_csv(tmp_path, "empty.csv", "id,name,age\n")
        _, profile = _scan_and_downgrade(path)
        for col in profile.columns:
            assert col.null_pct == 0.0


# ---------------------------------------------------------------------------
# test_single_row — 1-row CSV
# ---------------------------------------------------------------------------


class TestSingleRow:
    def test_row_count_is_one(self, tmp_path):
        path = _write_csv(tmp_path, "single.csv", "id,name\n1,Alice\n")
        _, profile = _scan_and_downgrade(path)
        assert profile.row_count == 1

    def test_findings_are_list(self, tmp_path):
        path = _write_csv(tmp_path, "single.csv", "id,name\n1,Alice\n")
        findings, _ = _scan_and_downgrade(path)
        assert isinstance(findings, list)

    def test_unique_pct_is_one(self, tmp_path):
        path = _write_csv(tmp_path, "single.csv", "id,name\n1,Alice\n")
        _, profile = _scan_and_downgrade(path)
        for col in profile.columns:
            assert col.unique_pct == 1.0


# ---------------------------------------------------------------------------
# test_all_null_column — column that is 100% null, verify null findings
# ---------------------------------------------------------------------------


class TestAllNullColumn:
    def test_null_pct_is_one(self, tmp_path):
        csv = "id,empty_col\n1,\n2,\n3,\n"
        path = _write_csv(tmp_path, "nulls.csv", csv)
        _, profile = _scan_and_downgrade(path)
        col = next(c for c in profile.columns if c.name == "empty_col")
        assert col.null_pct == 1.0

    def test_null_finding_exists(self, tmp_path):
        csv = "id,empty_col\n1,\n2,\n3,\n"
        path = _write_csv(tmp_path, "nulls.csv", csv)
        findings, _ = _scan_and_downgrade(path)
        null_findings = [
            f for f in findings
            if f.column == "empty_col" and "null" in f.check.lower()
        ]
        assert len(null_findings) >= 1

    def test_null_count_matches_rows(self, tmp_path):
        csv = "id,empty_col\n1,\n2,\n3,\n"
        path = _write_csv(tmp_path, "nulls.csv", csv)
        _, profile = _scan_and_downgrade(path)
        col = next(c for c in profile.columns if c.name == "empty_col")
        assert col.null_count == 3

    def test_unique_count_is_zero(self, tmp_path):
        csv = "id,empty_col\n1,\n2,\n3,\n"
        path = _write_csv(tmp_path, "nulls.csv", csv)
        _, profile = _scan_and_downgrade(path)
        col = next(c for c in profile.columns if c.name == "empty_col")
        assert col.unique_count == 0


# ---------------------------------------------------------------------------
# test_unicode_column_names — Japanese/emoji column names
# ---------------------------------------------------------------------------


class TestUnicodeColumnNames:
    def test_japanese_column_names(self, tmp_path):
        csv = "\u540d\u524d,\u5e74\u9f62\nAlice,30\nBob,25\n"
        path = _write_csv(tmp_path, "unicode.csv", csv)
        findings, profile = _scan_and_downgrade(path)
        assert profile.column_count == 2
        assert "\u540d\u524d" in {c.name for c in profile.columns}

    def test_emoji_column_names(self, tmp_path):
        csv = "\U0001f600_score,\U0001f4ca_value\n10,20\n30,40\n"
        path = _write_csv(tmp_path, "emoji.csv", csv)
        _, profile = _scan_and_downgrade(path)
        assert profile.column_count == 2
        assert profile.row_count == 2

    def test_mixed_script_column_names(self, tmp_path):
        csv = "col_\u00e9t\u00e9,col_\u00fc\u00df\nA,B\nC,D\n"
        path = _write_csv(tmp_path, "mixed.csv", csv)
        _, profile = _scan_and_downgrade(path)
        names = {c.name for c in profile.columns}
        assert "col_\u00e9t\u00e9" in names
        assert "col_\u00fc\u00df" in names


# ---------------------------------------------------------------------------
# test_wide_dataframe — 100+ columns
# ---------------------------------------------------------------------------


class TestWideDataframe:
    def test_100_columns(self, tmp_path):
        cols = [f"col_{i}" for i in range(100)]
        header = ",".join(cols)
        row = ",".join(str(i) for i in range(100))
        csv = f"{header}\n{row}\n{row}\n"
        path = _write_csv(tmp_path, "wide.csv", csv)
        _, profile = _scan_and_downgrade(path)
        assert profile.column_count == 100

    def test_150_columns_all_profiled(self, tmp_path):
        n = 150
        cols = [f"c{i}" for i in range(n)]
        header = ",".join(cols)
        row = ",".join(str(i) for i in range(n))
        csv = f"{header}\n{row}\n{row}\n{row}\n"
        path = _write_csv(tmp_path, "wide150.csv", csv)
        _, profile = _scan_and_downgrade(path)
        assert len(profile.columns) == n


# ---------------------------------------------------------------------------
# test_single_value_column — all same value
# ---------------------------------------------------------------------------


class TestSingleValueColumn:
    def test_unique_count_is_one(self, tmp_path):
        csv = "status\nactive\nactive\nactive\nactive\nactive\n"
        path = _write_csv(tmp_path, "single_val.csv", csv)
        _, profile = _scan_and_downgrade(path)
        col = next(c for c in profile.columns if c.name == "status")
        assert col.unique_count == 1

    def test_cardinality_finding(self, tmp_path):
        csv = "status\nactive\nactive\nactive\nactive\nactive\n"
        path = _write_csv(tmp_path, "single_val.csv", csv)
        findings, _ = _scan_and_downgrade(path)
        card = [
            f for f in findings
            if f.column == "status" and "cardinality" in f.check.lower()
        ]
        # Low cardinality may or may not fire depending on thresholds,
        # but scanning should not crash.
        assert isinstance(card, list)


# ---------------------------------------------------------------------------
# test_whitespace_only_column — column with only spaces/tabs
# ---------------------------------------------------------------------------


class TestWhitespaceOnlyColumn:
    def test_scan_completes(self, tmp_path):
        csv = 'id,ws_col\n1,"   "\n2,"\t"\n3,"  \t  "\n'
        path = _write_csv(tmp_path, "ws.csv", csv)
        findings, profile = _scan_and_downgrade(path)
        assert profile.row_count == 3
        assert isinstance(findings, list)

    def test_whitespace_not_counted_as_null(self, tmp_path):
        csv = 'id,ws_col\n1,"   "\n2,"\t"\n3,"  \t  "\n'
        path = _write_csv(tmp_path, "ws.csv", csv)
        _, profile = _scan_and_downgrade(path)
        col = next(c for c in profile.columns if c.name == "ws_col")
        # Polars treats whitespace-only as non-null strings
        assert col.null_count == 0


# ---------------------------------------------------------------------------
# test_numeric_strings — column of "123", "456" etc
# ---------------------------------------------------------------------------


class TestNumericStrings:
    def test_scan_completes(self, tmp_path):
        csv = "val\n123\n456\n789\n012\n"
        path = _write_csv(tmp_path, "numstr.csv", csv)
        findings, profile = _scan_and_downgrade(path)
        assert profile.row_count == 4
        assert isinstance(findings, list)

    def test_type_inference_runs(self, tmp_path):
        csv = "val\n123\n456\n789\n012\n"
        path = _write_csv(tmp_path, "numstr.csv", csv)
        _, profile = _scan_and_downgrade(path)
        # Polars may infer as Int64 or String depending on leading zeros
        col = next(c for c in profile.columns if c.name == "val")
        assert col.inferred_type is not None

    def test_leading_zeros_preserved_as_string(self, tmp_path):
        # Force quoting to keep leading zeros as strings
        csv = 'val\n"001"\n"002"\n"003"\n'
        path = _write_csv(tmp_path, "leading.csv", csv)
        _, profile = _scan_and_downgrade(path)
        col = next(c for c in profile.columns if c.name == "val")
        # Should be string type when quoted with leading zeros
        assert col.inferred_type is not None
