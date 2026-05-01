"""Tests for goldencheck schema-aware mode (Phase 5)."""
from __future__ import annotations

from goldencheck import scan_file
from goldencheck_types import FieldMapping, InferredSchema


def _make_schema(domain="finance", **fields):
    fm = {col: FieldMapping(col, t, t, 0.9, {}) if t != "unknown"
          else FieldMapping(col, None, "unknown", 0.3, {})
          for col, t in fields.items()}
    return InferredSchema(domain=domain, fields=fm, confidence=0.5)


def test_unknown_column_emits_finding(tmp_path):
    p = tmp_path / "f.csv"
    p.write_text("account_number,zzz_unknown\nA1234,foo\nA5678,bar\n", encoding="utf-8")
    schema = _make_schema(account_number="account_number", zzz_unknown="unknown")
    findings, _ = scan_file(p, schema=schema)
    codes = {f.check for f in findings}
    assert "unmapped_column" in codes
    unmapped = [f for f in findings if f.check == "unmapped_column"]
    assert any("zzz_unknown" in f.column for f in unmapped)


def test_legacy_mode_still_works(tmp_path):
    p = tmp_path / "f.csv"
    p.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    findings, _ = scan_file(p)  # no schema arg
    codes = {f.check for f in findings}
    assert "unmapped_column" not in codes


def test_schema_skips_classify_for_known_cols(tmp_path):
    p = tmp_path / "f.csv"
    p.write_text("account_number,currency\nA1234,USD\nA5678,EUR\n", encoding="utf-8")
    schema = _make_schema(
        account_number="account_number",
        currency="currency_code",
    )
    findings, _ = scan_file(p, schema=schema)
    # No unmapped_column for fully-mapped schema
    codes = {f.check for f in findings}
    assert "unmapped_column" not in codes
