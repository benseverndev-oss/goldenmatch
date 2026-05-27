"""Field-level provenance for the vectorized batch golden builder.

`build_golden_records_batch(..., provenance=True)` adds `source_row_id` to
each field dict -- the `__row_id__` of the record whose value won survivorship
for that field -- while preserving the single-group_by-per-column
vectorization. These tests pin that the reported row is the actual winner
across the fast path (most_complete / first_non_null) and the slow path
(field_rules force the per-cluster merge_field loop).
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import GoldenFieldRule, GoldenRulesConfig
from goldenmatch.core.golden import build_golden_records_batch


def _one(results: list[dict], cid: int) -> dict:
    return next(r for r in results if r["__cluster_id__"] == cid)


def test_fast_path_most_complete_source_row_id():
    # name: longest non-null is "Bobby" at row 11.
    # email: both non-null identical ("a@x") -> winner is the first, row 10.
    # note: all-null -> source_row_id None.
    df = pl.DataFrame({
        "__row_id__": [10, 11, 12],
        "__cluster_id__": [1, 1, 1],
        "name": ["Bob", "Bobby", None],
        "email": ["a@x", None, "a@x"],
        "note": [None, None, None],
    })
    rules = GoldenRulesConfig(default_strategy="most_complete")
    res = build_golden_records_batch(df, rules, provenance=True)
    rec = _one(res, 1)
    assert rec["name"]["value"] == "Bobby"
    assert rec["name"]["source_row_id"] == 11
    assert rec["email"]["value"] == "a@x"
    assert rec["email"]["source_row_id"] == 10
    assert rec["note"]["value"] is None
    assert rec["note"]["source_row_id"] is None


def test_fast_path_first_non_null_source_row_id():
    # first non-null phone is "555" at row 21.
    df = pl.DataFrame({
        "__row_id__": [20, 21, 22],
        "__cluster_id__": [5, 5, 5],
        "phone": [None, "555", "999"],
    })
    rules = GoldenRulesConfig(default_strategy="first_non_null")
    rec = _one(build_golden_records_batch(df, rules, provenance=True), 5)
    assert rec["phone"]["value"] == "555"
    assert rec["phone"]["source_row_id"] == 21


def test_slow_path_source_row_id():
    # field_rules forces the per-cluster merge_field loop (not the fast path).
    # most_complete winner is "Alice" at row 31.
    df = pl.DataFrame({
        "__row_id__": [30, 31],
        "__cluster_id__": [7, 7],
        "name": ["Al", "Alice"],
    })
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"name": GoldenFieldRule(strategy="most_complete")},
    )
    rec = _one(build_golden_records_batch(df, rules, provenance=True), 7)
    assert rec["name"]["value"] == "Alice"
    assert rec["name"]["source_row_id"] == 31


def test_provenance_requires_row_id():
    df = pl.DataFrame({"__cluster_id__": [1, 1], "name": ["a", "b"]})
    rules = GoldenRulesConfig(default_strategy="most_complete")
    with pytest.raises(ValueError, match="__row_id__"):
        build_golden_records_batch(df, rules, provenance=True)


def test_provenance_off_is_unchanged():
    # Default (provenance=False): field dicts carry only value + confidence.
    df = pl.DataFrame({
        "__row_id__": [1, 2],
        "__cluster_id__": [1, 1],
        "name": ["Al", "Alice"],
    })
    rules = GoldenRulesConfig(default_strategy="most_complete")
    rec = _one(build_golden_records_batch(df, rules), 1)
    assert set(rec["name"]) == {"value", "confidence"}
    assert "source_row_id" not in rec["name"]
