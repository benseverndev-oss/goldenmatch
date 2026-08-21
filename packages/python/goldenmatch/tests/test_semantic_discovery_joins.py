"""Certified join / FK discovery (semantic-model discovery, Phase 3).

`discover_joins` proposes foreign-key joins across tables via value-subset
containment and PROVES each join's one-side cardinality with `certify_cube_joins`, so
a `JoinCandidate` is pre-graded. Design:
`docs/superpowers/specs/2026-08-03-semantic-model-discovery-design.md`.
"""
from __future__ import annotations

import pyarrow as pa
from goldenmatch.semantic import (
    JoinCandidate,
    KeyCandidate,
    discover_joins,
    discover_keys,
)


def _customers() -> pa.Table:
    # customer_id is the clean primary key (the "one" side).
    return pa.table(
        {
            "customer_id": ["c1", "c2", "c3"],
            "name": ["Acme", "Globex", "Initech"],
            "region": ["west", "east", "west"],
        }
    )


def _orders() -> pa.Table:
    # customer_id here is a foreign key into customers (a subset, many rows/customer).
    return pa.table(
        {
            "order_id": ["o1", "o2", "o3", "o4"],
            "customer_id": ["c1", "c1", "c2", "c3"],
            "amount": [10.0, 20.0, 30.0, 40.0],
        }
    )


def _tables() -> dict[str, pa.Table]:
    return {"customers": _customers(), "orders": _orders()}


def _keys() -> dict[str, list[KeyCandidate]]:
    return {name: discover_keys(t) for name, t in _tables().items()}


def test_foreign_key_join_is_discovered_and_certified() -> None:
    joins = discover_joins(_tables(), _keys())
    assert joins, "should discover at least one join"
    # orders.customer_id -> customers.customer_id, many_to_one, certified trustworthy
    # (customers.customer_id is a clean grain).
    match = [
        j for j in joins
        if j.from_table == "orders" and j.from_column == "customer_id"
        and j.to_table == "customers" and j.to_column == "customer_id"
    ]
    assert match, f"expected orders->customers FK, got {[(j.from_table, j.from_column, j.to_table, j.to_column) for j in joins]}"
    j = match[0]
    assert isinstance(j, JoinCandidate)
    assert j.relationship == "many_to_one"
    assert j.is_trustworthy is True
    assert j.certificate.max_fan_out == 1.0
    assert "value_subset" in j.signals


def test_join_onto_fanned_out_key_is_flagged_untrustworthy() -> None:
    # If we (wrongly) declare orders.customer_id as the referenced key, the "one" side
    # fans out (c1 appears twice) -> a join across it double-counts.
    tables = _tables()
    keys = {"orders": ["customer_id"]}  # a plain declared (untrustworthy) key
    joins = discover_joins(tables, keys)
    onto_orders = [j for j in joins if j.to_table == "orders" and j.to_column == "customer_id"]
    assert onto_orders, "a join onto orders.customer_id should still be proposed"
    assert all(not j.is_trustworthy for j in onto_orders)
    assert any(j.certificate.max_fan_out > 1.0 for j in onto_orders)


def test_untrustworthy_target_key_candidate_is_skipped() -> None:
    # A KeyCandidate that is NOT trustworthy must not be used as a join target.
    orders = _orders()
    cust_key = [c for c in discover_keys(orders) if c.columns == ["customer_id"]][0]
    assert cust_key.is_trustworthy is False
    joins = discover_joins({"orders": orders, "customers": _customers()}, {"orders": [cust_key]})
    assert all(j.to_column != "customer_id" or j.to_table != "orders" for j in joins)


def test_measure_column_is_not_a_foreign_key() -> None:
    joins = discover_joins(_tables(), _keys())
    # `amount` is numeric (a measure) — never proposed as a foreign key.
    assert all(j.from_column != "amount" for j in joins)


def test_no_join_when_values_do_not_overlap() -> None:
    a = pa.table({"a_id": ["a1", "a2", "a3"]})
    b = pa.table({"b_id": ["z9", "z8", "z7"]})  # disjoint from a_id
    joins = discover_joins({"a": a, "b": b}, {"a": ["a_id"]})
    assert joins == []


def test_score_and_signals_are_populated() -> None:
    joins = discover_joins(_tables(), _keys())
    top = joins[0]
    assert 0.0 <= top.score <= 1.0
    assert top.signals == sorted(top.signals) or "value_subset" in top.signals
    assert top.is_trustworthy is True


def test_max_candidates_caps_output() -> None:
    joins = discover_joins(_tables(), _keys(), max_candidates=1)
    assert len(joins) <= 1
