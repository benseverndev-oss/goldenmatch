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


def test_records_to_provenance_adapter():
    from goldenmatch.core.golden import golden_records_to_provenance
    df = pl.DataFrame({
        "__row_id__": [10, 11],
        "__cluster_id__": [1, 1],
        "name": ["Bob", "Bobby"],
    })
    rules = GoldenRulesConfig(default_strategy="most_complete")
    records = build_golden_records_batch(df, rules, provenance=True)
    clusters = {1: {"members": [10, 11], "size": 2,
                    "cluster_quality": "strong", "confidence": 0.9}}
    prov = golden_records_to_provenance(records, clusters, rules)
    assert len(prov) == 1
    cp = prov[0]
    assert cp.cluster_id == 1
    assert cp.cluster_quality == "strong"
    assert cp.cluster_confidence == 0.9
    fp = cp.fields["name"]
    assert fp.value == "Bobby"
    assert fp.source_row_id == 11
    assert fp.strategy == "most_complete"
    assert fp.candidates == []  # scale tradeoff: no per-row candidate list


def test_pipeline_writes_golden_provenance_when_flag_on(tmp_path):
    """End-to-end: lineage_provenance=True -> the lineage sidecar carries a
    golden_records section with per-field source_row_id; off -> no such key."""
    import json

    from goldenmatch.config.schemas import (
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
        OutputConfig,
    )
    from goldenmatch.core.pipeline import run_dedupe_df

    # Bob/Bobby share an email -> one multi-member cluster; Carol is unique.
    df = pl.DataFrame({
        "name": ["Bob", "Bobby", "Carol"],
        "email": ["b@x", "b@x", "c@x"],
    })
    mks = [MatchkeyConfig(name="email", type="exact",
                          fields=[MatchkeyField(field="email")])]

    def _run(flag: bool, run_name: str):
        cfg = GoldenMatchConfig(
            matchkeys=mks,
            output=OutputConfig(directory=str(tmp_path), run_name=run_name,
                                lineage_provenance=flag),
        )
        run_dedupe_df(df, cfg, output_clusters=True)
        return json.loads((tmp_path / f"{run_name}_lineage.json").read_text())

    on = _run(True, "prov_on")
    assert "golden_records" in on
    name_provs = [
        f["source_row_id"]
        for rec in on["golden_records"]
        for col, f in rec["fields"].items()
        if col == "name"
    ]
    # most_complete picks "Bobby" (row index 1) over "Bob" (row 0).
    assert 1 in name_provs

    off = _run(False, "prov_off")
    assert "golden_records" not in off
