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


def test_rapidfuzz_and_pure_python_fallback_agree(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Python fallback uses rapidfuzz's Levenshtein when importable (~38x
    faster) and a pure-Python DP Levenshtein otherwise. Both use the identical
    `1 - dist/maxlen` metric, so the resulting clusters must be byte-identical.
    Forces the pure-Python branch by hiding rapidfuzz from the import machinery."""
    import sys

    from goldencheck.profilers.fuzzy_values import _python_clusters

    # A moderate set with clear near-duplicate variant groups + distractors.
    values = [
        "Acme Corp", "Acme Corporation", "Acme Corp.",
        "Globex Inc", "Globex Incorporated",
        "Initech", "Umbrella", "Wonka",
    ]

    rf_clusters = _python_clusters(values, 0.82)  # rapidfuzz present (venv has it)

    # Hide rapidfuzz.distance so `from rapidfuzz.distance import Levenshtein` raises.
    monkeypatch.setitem(sys.modules, "rapidfuzz.distance", None)
    py_clusters = _python_clusters(values, 0.82)  # forced pure-Python

    assert rf_clusters == py_clusters
    # And the fallback actually found the near-dup groups (not a trivial [] == []).
    assert rf_clusters, "expected at least one near-duplicate cluster"
