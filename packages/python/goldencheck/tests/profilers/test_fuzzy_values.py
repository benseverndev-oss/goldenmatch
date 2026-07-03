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


def test_python_clusters_rapidfuzz_metric() -> None:
    """The Python path scores candidate pairs with rapidfuzz's Levenshtein
    (`1 - dist/maxlen`) -- the identical metric to the native kernel. Verify it
    groups near-duplicate variants and leaves distinct values apart."""
    from goldencheck.profilers.fuzzy_values import _python_clusters

    values = [
        "Acme Corp", "Acme Corporation", "Acme Corp.",   # one variant group
        "Globex Inc", "Globex Incorporated",             # another
        "Initech", "Umbrella", "Wonka",                  # distinct distractors
    ]
    clusters = _python_clusters(values, 0.82)
    grouped = {tuple(sorted(values[i] for i in c)) for c in clusters}

    # The near-identical spellings cluster; the unrelated names never join one.
    assert any({"Acme Corp", "Acme Corp."} <= set(g) for g in grouped)
    assert all("Wonka" not in g for g in grouped)


def test_rapidfuzz_ratio_matches_reference_levenshtein() -> None:
    """Lock the metric: rapidfuzz's `1 - dist/maxlen` equals a plain DP
    Levenshtein ratio, so clustering can't drift from the native kernel."""
    from rapidfuzz.distance import Levenshtein as RL

    def dp(a: str, b: str) -> int:
        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a):
            cur = [i + 1]
            for j, cb in enumerate(b):
                cur.append(min(prev[j + 1] + 1, cur[j] + 1, prev[j] + (ca != cb)))
            prev = cur
        return prev[len(b)]

    for a, b in [("acme corp", "acme corp."), ("globex inc", "globex incorporated"),
                 ("initech", "wonka"), ("", "x"), ("same", "same")]:
        m = max(len(a), len(b)) or 1
        assert abs((1 - RL.distance(a, b) / m) - (1 - dp(a, b) / m)) < 1e-9
