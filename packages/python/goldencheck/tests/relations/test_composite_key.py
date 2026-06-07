"""Tests for the composite-key discovery relation profiler."""
from __future__ import annotations

import polars as pl
import pytest
from goldencheck.relations.composite_key import CompositeKeyProfiler


def _order_lines() -> pl.DataFrame:
    """(order_id, line_no) is a composite key; neither column is unique alone."""
    return pl.DataFrame({
        "order_id": [1, 1, 1, 2, 2, 3],
        "line_no": [1, 2, 3, 1, 2, 1],
        "sku": ["a", "b", "c", "a", "d", "e"],
        "qty": [2, 1, 5, 1, 1, 9],
    })


def test_discovers_composite_key() -> None:
    findings = CompositeKeyProfiler().profile(_order_lines())
    assert findings, "expected a composite-key finding"
    keys = {tuple(f.metadata["key_columns"]) for f in findings}
    assert ("order_id", "line_no") in keys
    f = next(f for f in findings if tuple(f.metadata["key_columns"]) == ("order_id", "line_no"))
    assert f.check == "composite_key"
    assert f.column == "order_id"  # anchored on first key column


def test_silent_when_single_column_key_exists() -> None:
    df = _order_lines().with_columns(pl.Series("pk", list(range(6))))
    assert CompositeKeyProfiler().profile(df) == []


def test_silent_on_trivial_frames() -> None:
    assert CompositeKeyProfiler().profile(pl.DataFrame({"a": [1, 2, 3]})) == []
    assert CompositeKeyProfiler().profile(pl.DataFrame({"a": [1], "b": [2]})) == []


def test_native_and_python_agree(monkeypatch: pytest.MonkeyPatch) -> None:
    """The profiler's output is identical with native forced on vs off."""
    df = _order_lines()

    monkeypatch.setenv("GOLDENCHECK_NATIVE", "0")
    py = {tuple(f.metadata["key_columns"]) for f in CompositeKeyProfiler().profile(df)}
    monkeypatch.setenv("GOLDENCHECK_NATIVE", "1")
    try:
        nat = {tuple(f.metadata["key_columns"]) for f in CompositeKeyProfiler().profile(df)}
    except RuntimeError:
        pytest.skip("native extension not built")
    assert py == nat
