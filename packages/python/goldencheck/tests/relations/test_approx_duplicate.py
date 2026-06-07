"""Tests for the exact + near-duplicate row profiler."""
from __future__ import annotations

import polars as pl
from goldencheck.relations.approx_duplicate import ApproxDuplicateProfiler


def _checks(findings) -> set[str]:
    return {f.check for f in findings}


def test_detects_exact_duplicate_rows() -> None:
    df = pl.DataFrame({
        "name": ["Acme", "Beta", "Acme", "Gamma"],
        "city": ["NYC", "LA", "NYC", "SF"],
    })
    findings = ApproxDuplicateProfiler().profile(df)
    assert "duplicate_rows" in _checks(findings)
    f = next(f for f in findings if f.check == "duplicate_rows")
    assert f.affected_rows == 2  # the two "Acme/NYC" rows
    assert f.metadata["duplicate_groups"] == 1


def test_detects_near_duplicate_rows() -> None:
    df = pl.DataFrame({
        "name": ["Acme, Inc.", "acme inc", "Beta LLC"],
        "city": ["New York", "new york", "Boston"],
    })
    findings = ApproxDuplicateProfiler().profile(df)
    assert "near_duplicate_rows" in _checks(findings)
    f = next(f for f in findings if f.check == "near_duplicate_rows")
    assert f.affected_rows == 2  # the two Acme rows normalize equal but differ raw


def test_exact_dupes_are_not_also_counted_as_near() -> None:
    df = pl.DataFrame({"name": ["Acme", "Acme", "Beta"], "city": ["NYC", "NYC", "LA"]})
    findings = ApproxDuplicateProfiler().profile(df)
    # The Acme rows are byte-identical -> exact only, not near.
    assert "duplicate_rows" in _checks(findings)
    assert "near_duplicate_rows" not in _checks(findings)


def test_clean_data_no_findings() -> None:
    df = pl.DataFrame({"id": [1, 2, 3], "name": ["a", "b", "c"]})
    assert ApproxDuplicateProfiler().profile(df) == []


def test_trivial_frames() -> None:
    assert ApproxDuplicateProfiler().profile(pl.DataFrame({"a": [1]})) == []
    assert ApproxDuplicateProfiler().profile(pl.DataFrame()) == []
