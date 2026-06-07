"""Tests for the strict functional-dependency discovery profiler."""
from __future__ import annotations

import polars as pl
import pytest
from goldencheck.relations.functional_dependency import FunctionalDependencyProfiler


def _lookup_df(n: int = 120) -> pl.DataFrame:
    # zip -> city is a strict FD (each zip maps to one city); city -> zip is not
    # (city 0 is shared by zips 0 and 1). amt is independent noise.
    zips = [i % 6 for i in range(n)]
    zip_to_city = {0: 0, 1: 0, 2: 1, 3: 2, 4: 3, 5: 4}
    return pl.DataFrame({
        "zip": zips,
        "city": [zip_to_city[z] for z in zips],
        "amt": [(i * 7) % 50 for i in range(n)],
    })


def test_discovers_strict_fd() -> None:
    findings = FunctionalDependencyProfiler().profile(_lookup_df())
    fds = {(f.metadata["determinant"], tuple(f.metadata["dependents"])) for f in findings}
    assert ("zip", ("city",)) in fds
    f = next(f for f in findings if f.metadata["determinant"] == "zip")
    assert f.check == "functional_dependency"
    assert f.severity.name == "INFO"


def test_no_fd_when_independent() -> None:
    df = pl.DataFrame({
        "a": [i % 5 for i in range(120)],
        "b": [(i * 3) % 7 for i in range(120)],
    })
    # a and b are independent residues -> no strict FD either way.
    findings = FunctionalDependencyProfiler().profile(df)
    assert findings == []


def test_requires_minimum_support() -> None:
    # Below _MIN_ROWS, stay silent (a strict FD on a handful of rows is a fluke).
    df = _lookup_df(n=10)
    assert FunctionalDependencyProfiler().profile(df) == []


def test_native_and_python_agree(monkeypatch: pytest.MonkeyPatch) -> None:
    df = _lookup_df()
    monkeypatch.setenv("GOLDENCHECK_NATIVE", "0")
    py = {
        (f.metadata["determinant"], tuple(f.metadata["dependents"]))
        for f in FunctionalDependencyProfiler().profile(df)
    }
    monkeypatch.setenv("GOLDENCHECK_NATIVE", "1")
    try:
        nat = {
            (f.metadata["determinant"], tuple(f.metadata["dependents"]))
            for f in FunctionalDependencyProfiler().profile(df)
        }
    except RuntimeError:
        pytest.skip("native extension not built")
    assert py == nat
