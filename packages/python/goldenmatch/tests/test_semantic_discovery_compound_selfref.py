"""Compound + self-referential keys (PR-10).

Lifts the single-column restriction on the discovery side:
- `discover_keys` proposes a certified COMPOUND key (fallback pairs) when no single
  column is unique at grain.
- `discover_joins` proposes a SELF-REFERENTIAL single-column FK (a column referencing
  the table's own certified key).

The certifier, `KeyCandidate.columns`, and all three emit paths already carry
multi-column keys, so this is a discovery-side change.
"""
from __future__ import annotations

import pyarrow as pa
from goldenmatch.semantic import discover_semantic_model
from goldenmatch.semantic.discovery.joins import discover_joins
from goldenmatch.semantic.discovery.keys import discover_keys


def _order_items() -> pa.Table:
    """A classic composite grain: neither order_id nor product_id is unique, but the
    pair (order_id, product_id) is."""
    return pa.table({
        "order_id": ["o1", "o1", "o2", "o2", "o3", "o3"],
        "product_id": ["p1", "p2", "p1", "p2", "p1", "p2"],
        "qty": [1, 2, 3, 4, 5, 6],
    })


def _employees() -> pa.Table:
    """employee_id is the PK; manager_id is a self-referential FK into employee_id."""
    return pa.table({
        "employee_id": ["e1", "e2", "e3", "e4", "e5"],
        "manager_id": ["e1", "e1", "e2", "e2", "e3"],
        "name": ["Ann", "Bob", "Cy", "Dee", "Ed"],
    })


# --- compound key discovery -----------------------------------------------------


def test_discover_keys_proposes_certified_compound_key():
    cands = discover_keys(_order_items())
    # No single column is trustworthy...
    singles = [c for c in cands if len(c.columns) == 1]
    assert all(not c.is_trustworthy for c in singles)
    # ...but a trustworthy compound (order_id, product_id) is proposed, ranked first.
    compound = next((c for c in cands if len(c.columns) == 2 and c.is_trustworthy), None)
    assert compound is not None
    assert set(compound.columns) == {"order_id", "product_id"}
    assert cands[0] is compound  # trustworthy compound ranks ahead of the untrustworthy singles


def test_single_column_key_suppresses_compound_search():
    # customers.customer_id is a clean single key -> the fallback compound search never fires.
    customers = pa.table({"customer_id": ["c1", "c2", "c3"], "region": ["w", "e", "w"]})
    cands = discover_keys(customers)
    assert any(c.is_trustworthy for c in cands if len(c.columns) == 1)
    assert all(len(c.columns) == 1 for c in cands)


def test_compound_grain_flows_through_discover_semantic_model():
    from goldenmatch.semantic.cube import parse_cube_models

    # The compound grain is discovered and certified (dialect-agnostic).
    m = discover_semantic_model({"order_items": _order_items()}, dialect="cube")
    pt = next(p for p in m.tables if p.table == "order_items")
    assert pt.key is not None
    assert set(pt.key.columns) == {"order_id", "product_id"}
    assert pt.grain_trustworthy  # the discovery certificate is trustworthy

    # Cube expresses the full COMPOSITE primary key natively (MetricFlow, which has no
    # composite primary entity, can only declare the first column — a documented limit).
    oi = {c.name: c for c in parse_cube_models(m.yaml)}["order_items"]
    assert set(oi.primary_key) == {"order_id", "product_id"}


# --- self-referential FK discovery ----------------------------------------------


def test_discover_joins_finds_self_referential_fk():
    tables = {"employees": _employees()}
    keys = {"employees": discover_keys(_employees())}
    joins = discover_joins(tables, keys)

    self_ref = next(
        (j for j in joins
         if j.from_table == "employees" and j.to_table == "employees"), None
    )
    assert self_ref is not None
    assert self_ref.from_column == "manager_id"
    assert self_ref.to_column == "employee_id"
    assert self_ref.is_trustworthy  # employee_id is unique -> the "one" side certifies


def test_self_referential_join_flows_through_discover_semantic_model():
    m = discover_semantic_model({"employees": _employees()})
    self_ref = next(
        (j for j in m.joins
         if j.from_table == "employees" and j.to_table == "employees"), None
    )
    assert self_ref is not None
    assert self_ref.from_column == "manager_id"


def test_no_spurious_self_join_on_the_key_itself():
    # A table's key column must never be proposed as a self-FK onto itself.
    m = discover_semantic_model({"employees": _employees()})
    assert not any(
        j.from_table == j.to_table and j.from_column == j.to_column for j in m.joins
    )
