"""Measure & dimension proposal (semantic-model discovery, Phase 4).

`discover_measures` proposes measures + dimensions over a table and gates each
measure's SUM-safety on the grain key's certificate — a fanned-out grain withholds
SUM (it would double-count). Design:
`docs/superpowers/specs/2026-08-03-semantic-model-discovery-design.md`.
"""
from __future__ import annotations

import pyarrow as pa
from goldenmatch.semantic import (
    Dimension,
    Measure,
    TableMeasures,
    discover_keys,
    discover_measures,
)


def _orders() -> pa.Table:
    return pa.table(
        {
            "order_id": ["o1", "o2", "o3", "o4"],
            "customer_id": ["c1", "c1", "c2", "c3"],
            "status": ["new", "new", "done", "new"],
            "amount": [10.0, 20.0, 30.0, 40.0],
        }
    )


def _clean_key():
    return [c for c in discover_keys(_orders()) if c.columns == ["order_id"]][0]


def _fanned_key():
    return [c for c in discover_keys(_orders()) if c.columns == ["customer_id"]][0]


def test_sum_is_safe_on_a_certified_clean_grain() -> None:
    tm = discover_measures(_orders(), key=_clean_key(), table_name="orders")
    assert isinstance(tm, TableMeasures)
    assert tm.grain == ["order_id"]
    assert tm.grain_trustworthy is True
    amount = [m for m in tm.measures if m.column == "amount"]
    assert amount, "amount (numeric) should be proposed as a measure"
    m = amount[0]
    assert isinstance(m, Measure)
    assert m.safe_to_sum is True
    assert "sum" in m.aggregations
    assert "avg" in m.aggregations and "count" in m.aggregations


def test_sum_is_withheld_on_a_fanned_out_grain() -> None:
    # Grouped on the fanned-out customer_id grain, SUM(amount) would double-count.
    tm = discover_measures(_orders(), key=_fanned_key(), table_name="orders")
    assert tm.grain_trustworthy is False
    m = [m for m in tm.measures if m.column == "amount"][0]
    assert m.safe_to_sum is False
    assert "sum" not in m.aggregations
    # the always-safe aggregations are still proposed.
    assert "count" in m.aggregations and "avg" in m.aggregations


def test_no_grain_is_conservative() -> None:
    tm = discover_measures(_orders(), table_name="orders")
    m = [m for m in tm.measures if m.column == "amount"][0]
    assert m.safe_to_sum is False
    assert "sum" not in m.aggregations


def test_low_cardinality_categorical_is_a_dimension() -> None:
    tm = discover_measures(_orders(), key=_clean_key(), table_name="orders")
    dims = {d.column for d in tm.dimensions}
    # status (low-card categorical) is a dimension; amount (numeric) is not.
    assert "status" in dims
    assert "amount" not in dims
    assert all(isinstance(d, Dimension) for d in tm.dimensions)


def test_grain_and_identifiers_are_not_measures_or_dimensions() -> None:
    tm = discover_measures(_orders(), key=_clean_key(), table_name="orders")
    all_cols = {m.column for m in tm.measures} | {d.column for d in tm.dimensions}
    # order_id is the grain; customer_id is an identifier/FK — neither is proposed.
    assert "order_id" not in all_cols
    assert "customer_id" not in all_cols


def test_measures_rank_sum_safe_first() -> None:
    t = pa.table(
        {
            "id": ["a", "b", "c"],
            "x": [1.0, 2.0, 3.0],
            "y": [4.0, 5.0, 6.0],
        }
    )
    key = [c for c in discover_keys(t) if c.columns == ["id"]][0]
    tm = discover_measures(t, key=key, table_name="t")
    assert all(m.safe_to_sum for m in tm.measures)
