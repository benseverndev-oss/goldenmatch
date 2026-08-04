"""Cardinality (PR-14): one-to-one refinement + many-to-many bridges.

Direct joins become `one_to_one` when the FK is unique on the from side; junction
tables (a compound key of two FKs to two different tables) surface as m:n bridges.
Deterministic, default-on. Driven on users/profiles (1:1) and orders/products/
order_products (m:n) fixtures.
"""
from __future__ import annotations

import pyarrow as pa
from goldenmatch.semantic import discover_semantic_model
from goldenmatch.semantic.discovery.joins import discover_joins
from goldenmatch.semantic.discovery.keys import discover_keys


def _one_to_one() -> dict[str, pa.Table]:
    # each profile references a distinct user -> profiles.user_id is unique -> 1:1.
    users = pa.table({"user_id": ["u1", "u2", "u3"], "name": ["A", "B", "C"]})
    profiles = pa.table({"profile_id": ["p1", "p2", "p3"],
                         "user_id": ["u1", "u2", "u3"], "bio": ["x", "y", "z"]})
    return {"users": users, "profiles": profiles}


def _many_to_many() -> dict[str, pa.Table]:
    orders = pa.table({"order_id": ["o1", "o2"], "amount": [10.0, 20.0]})
    products = pa.table({"product_id": ["pa", "pb"], "sku": ["s1", "s2"]})
    # order_products bridges orders <-> products on the compound (order_id, product_id).
    order_products = pa.table({
        "order_id": ["o1", "o1", "o2"],
        "product_id": ["pa", "pb", "pa"],
    })
    return {"orders": orders, "products": products, "order_products": order_products}


# --- one-to-one refinement ------------------------------------------------------


def test_unique_fk_join_is_one_to_one():
    tables = _one_to_one()
    keys = {n: discover_keys(t) for n, t in tables.items()}
    joins = discover_joins(tables, keys)
    j = next(j for j in joins if j.from_table == "profiles" and j.to_table == "users")
    assert j.relationship == "one_to_one"


def test_non_unique_fk_stays_many_to_one():
    m = discover_semantic_model(_many_to_many())
    # order_products.order_id -> orders is many-to-one (an order has many bridge rows).
    j = next(j for j in m.joins
             if j.from_table == "order_products" and j.to_table == "orders")
    assert j.relationship == "many_to_one"


# --- many-to-many bridges -------------------------------------------------------


def test_discover_bridges_finds_the_junction():
    from goldenmatch.semantic.discovery.cardinality import discover_bridges

    tables = _many_to_many()
    keys = {n: discover_keys(t) for n, t in tables.items()}
    joins = discover_joins(tables, keys)
    from goldenmatch.semantic.discovery.model import ProposedTable, _best_key
    pts = [ProposedTable(table=n, entity_type=None, key=_best_key(keys[n])) for n in tables]

    bridges = discover_bridges(pts, joins)
    assert len(bridges) == 1
    b = bridges[0]
    assert b.bridge_table == "order_products"
    assert {b.left_table, b.right_table} == {"orders", "products"}


def test_bridge_flows_through_model_and_to_dict():
    m = discover_semantic_model(_many_to_many())
    assert any(b.bridge_table == "order_products" for b in m.bridges)
    d = m.to_dict()
    assert any(b["bridge_table"] == "order_products" for b in d["bridges"])
