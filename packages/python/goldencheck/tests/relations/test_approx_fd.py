"""Tests for the approximate-FD violation profiler."""
from __future__ import annotations

import polars as pl
import pytest
from goldencheck.relations.approx_fd import ApproximateFDProfiler


def _near_fd_df(n: int = 300) -> pl.DataFrame:
    # zip -> city holds except for a few injected typos -> those rows are the
    # violations the profiler should surface.
    zip_to_city = {i: f"city_{i}" for i in range(10)}
    zips = [i % 10 for i in range(n)]
    cities = [zip_to_city[z] for z in zips]
    # inject 3 violations (indices kept in range for small n)
    for bad in {7 % n, 50 % n, 123 % n}:
        cities[bad] = "WRONGCITY"
    return pl.DataFrame({"zip": zips, "city": cities, "amt": [(i * 13) % 97 for i in range(n)]})


def test_surfaces_near_fd_violations() -> None:
    findings = ApproximateFDProfiler().profile(_near_fd_df())
    fd = [f for f in findings if f.metadata.get("determinant") == "zip"
          and f.metadata.get("dependent") == "city"]
    assert fd, "expected a zip->city near-FD violation finding"
    f = fd[0]
    assert f.check == "fd_violation"
    assert f.metadata["violation_count"] == 3
    assert f.metadata["fd_confidence"] >= 0.95


def test_strict_fd_not_reported_here() -> None:
    # Perfect zip->city: zero violations -> this profiler is silent (the strict
    # FunctionalDependencyProfiler handles it instead).
    zips = [i % 10 for i in range(300)]
    df = pl.DataFrame({"zip": zips, "city": [f"c{z}" for z in zips]})
    assert ApproximateFDProfiler().profile(df) == []


def test_near_unique_determinant_guarded() -> None:
    # A near-unique 'id' would spuriously "determine" everything (singleton
    # groups); the avg-group-size guard must suppress it.
    n = 300
    df = pl.DataFrame({"id": list(range(n)), "grp": [i % 4 for i in range(n)]})
    findings = ApproximateFDProfiler().profile(df)
    assert all(f.metadata.get("determinant") != "id" for f in findings)


def test_below_min_rows_silent() -> None:
    assert ApproximateFDProfiler().profile(_near_fd_df(n=40)) == []


def test_native_and_python_agree(monkeypatch: pytest.MonkeyPatch) -> None:
    df = _near_fd_df()

    def summary() -> set:
        return {
            (f.metadata["determinant"], f.metadata["dependent"], f.metadata["violation_count"])
            for f in ApproximateFDProfiler().profile(df)
        }

    monkeypatch.setenv("GOLDENCHECK_NATIVE", "0")
    py = summary()
    monkeypatch.setenv("GOLDENCHECK_NATIVE", "1")
    try:
        nat = summary()
    except RuntimeError:
        pytest.skip("native extension not built")
    assert py == nat
