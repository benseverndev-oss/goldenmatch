"""Tests for the fuzzy near-duplicate value profiler."""
from __future__ import annotations

import polars as pl
import pytest
from goldencheck.profilers.fuzzy_values import FuzzyValuesProfiler


def _state_df(n: int = 120) -> pl.DataFrame:
    # 'state' has near-duplicate encodings of California; 'clean' does not.
    variants = ["California", "Californa", "CALIFORNIA", "Texas", "New York"]
    states = [variants[i % len(variants)] for i in range(n)]
    return pl.DataFrame({
        "state": states,
        "clean": [["apple", "banana", "cherry"][i % 3] for i in range(n)],
    })


def test_flags_fuzzy_value_variants() -> None:
    findings = FuzzyValuesProfiler().profile(_state_df(), "state")
    assert findings
    f = findings[0]
    assert f.check == "fuzzy_duplicate_values"
    variants = set(f.metadata["variants"])
    assert {"California", "Californa", "CALIFORNIA"} <= variants


def test_clean_column_no_findings() -> None:
    assert FuzzyValuesProfiler().profile(_state_df(), "clean") == []


def test_non_string_skipped() -> None:
    df = pl.DataFrame({"n": list(range(100))})
    assert FuzzyValuesProfiler().profile(df, "n") == []


def test_below_min_rows_skipped() -> None:
    df = _state_df(n=10)
    assert FuzzyValuesProfiler().profile(df, "state") == []


def test_native_and_python_agree(monkeypatch: pytest.MonkeyPatch) -> None:
    df = _state_df()
    monkeypatch.setenv("GOLDENCHECK_NATIVE", "0")
    py = {tuple(sorted(f.metadata["variants"])) for f in FuzzyValuesProfiler().profile(df, "state")}
    monkeypatch.setenv("GOLDENCHECK_NATIVE", "1")
    try:
        nat = {tuple(sorted(f.metadata["variants"])) for f in FuzzyValuesProfiler().profile(df, "state")}
    except RuntimeError:
        pytest.skip("native extension not built")
    assert py == nat
