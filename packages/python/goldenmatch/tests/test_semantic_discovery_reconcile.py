"""Catalog reconciliation (PR-18).

Diff a discovered (certified) `ProposedModel` against an existing catalog — a parsed
MetricFlow or Cube model. The point isn't a text diff: the discovered side is PROVEN, so a
grain that disagrees with the catalog's declared key, when our grain is certified, is a
provable defect in the catalog (double-counting), not a stylistic difference.
"""
from __future__ import annotations

import pyarrow as pa
from goldenmatch.semantic import (
    Reconciliation,
    discover_semantic_model,
    parse_cube_models,
    parse_semantic_models,
    reconcile_model,
)


def _clean() -> dict[str, pa.Table]:
    customers = pa.table({"customer_id": ["c1", "c2", "c3"], "region": ["w", "e", "w"]})
    orders = pa.table({"order_id": ["o1", "o2", "o3"], "customer_id": ["c1", "c1", "c2"],
                       "amount": [1.0, 2.0, 3.0]})
    return {"customers": customers, "orders": orders}


# --- table presence --------------------------------------------------------------


def test_table_only_in_model_is_reported():
    proposed = discover_semantic_model(_clean())
    # catalog only knows about customers -> orders is only_in_model.
    catalog = parse_semantic_models({
        "semantic_models": [
            {"name": "customers", "entities": [{"name": "customer_id", "type": "primary"}]},
        ]
    })
    rec = reconcile_model(proposed, catalog)
    assert isinstance(rec, Reconciliation)
    assert rec.in_sync is False
    assert any(d.table == "orders" and d.kind == "only_in_model" for d in rec.diffs)


def test_table_only_in_catalog_is_reported():
    proposed = discover_semantic_model(_clean())
    catalog = parse_semantic_models({
        "semantic_models": [
            {"name": "customers", "entities": [{"name": "customer_id", "type": "primary"}]},
            {"name": "orders", "entities": [{"name": "order_id", "type": "primary"}]},
            {"name": "ghost", "entities": [{"name": "ghost_id", "type": "primary"}]},
        ]
    })
    rec = reconcile_model(proposed, catalog)
    assert any(d.table == "ghost" and d.kind == "only_in_catalog" for d in rec.diffs)


# --- the money finding: proven grain drift ---------------------------------------


def test_grain_drift_against_certified_grain_is_proven():
    # orders' real grain is (order_id) and it's certified unique; the catalog wrongly
    # declares customer_id the key -> a PROVEN grain defect, not a style diff.
    proposed = discover_semantic_model(_clean())
    catalog = parse_semantic_models({
        "semantic_models": [
            {"name": "customers", "entities": [{"name": "customer_id", "type": "primary"}]},
            {"name": "orders", "entities": [{"name": "customer_id", "type": "primary"}]},
        ]
    })
    rec = reconcile_model(proposed, catalog)
    drift = next(d for d in rec.diffs if d.table == "orders" and d.kind == "grain_drift")
    assert drift.proven is True
    assert "order_id" in drift.detail and "customer_id" in drift.detail


def test_matching_grain_is_in_sync_for_that_table():
    proposed = discover_semantic_model(_clean())
    catalog = parse_semantic_models({
        "semantic_models": [
            {"name": "customers", "entities": [{"name": "customer_id", "type": "primary"}]},
            {"name": "orders", "entities": [{"name": "order_id", "type": "primary"}]},
        ]
    })
    rec = reconcile_model(proposed, catalog)
    assert "customers" in rec.matched_tables
    assert not any(d.table == "customers" and d.kind == "grain_drift" for d in rec.diffs)


# --- measures --------------------------------------------------------------------


def test_measure_only_in_model_is_reported():
    proposed = discover_semantic_model(_clean())
    # catalog declares orders with the right key but no `amount` measure.
    catalog = parse_semantic_models({
        "semantic_models": [
            {"name": "customers", "entities": [{"name": "customer_id", "type": "primary"}]},
            {"name": "orders", "entities": [{"name": "order_id", "type": "primary"}]},
        ]
    })
    rec = reconcile_model(proposed, catalog)
    assert any(d.table == "orders" and d.kind == "measure_only_in_model"
               and "amount" in d.detail for d in rec.diffs)


# --- Cube dialect + serialization ------------------------------------------------


def test_reconciles_against_cube_catalog():
    proposed = discover_semantic_model(_clean(), dialect="cube")
    cube = parse_cube_models({
        "cubes": [
            {"name": "customers",
             "dimensions": [{"name": "customer_id", "primary_key": True}]},
        ]
    })
    rec = reconcile_model(proposed, cube)
    assert any(d.table == "orders" and d.kind == "only_in_model" for d in rec.diffs)


def test_to_dict_shape():
    proposed = discover_semantic_model(_clean())
    catalog = parse_semantic_models({
        "semantic_models": [
            {"name": "customers", "entities": [{"name": "customer_id", "type": "primary"}]},
            {"name": "orders", "entities": [{"name": "order_id", "type": "primary"}]},
        ]
    })
    d = reconcile_model(proposed, catalog).to_dict()
    assert set(d) >= {"in_sync", "matched_tables", "diffs", "n_tables_model",
                      "n_tables_catalog"}
    assert isinstance(d["diffs"], list)
