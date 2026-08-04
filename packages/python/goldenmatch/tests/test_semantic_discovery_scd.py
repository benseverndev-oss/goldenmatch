"""SCD / temporal dimensions (PR-15).

Flag Slowly-Changing-Dimension (Type 2) tables — validity columns / an is_current flag
NAME-propose the SCD, and the business key repeating with `(business_key, valid_from)`
unique at grain STRUCTURE-confirms it. Deterministic, default-on. Driven on a versioned
customer_dim fixture (positive) and a non-versioned contracts fixture (negative).
"""
from __future__ import annotations

import datetime as dt

import pyarrow as pa
from goldenmatch.semantic import discover_semantic_model


def _customer_dim() -> pa.Table:
    # customer_id is the business key (c1 has two versions); customer_key is the surrogate.
    return pa.table({
        "customer_key": ["k1", "k2", "k3"],
        "customer_id": ["c1", "c1", "c2"],
        "valid_from": pa.array([dt.date(2020, 1, 1), dt.date(2021, 1, 1), dt.date(2020, 1, 1)],
                               type=pa.date32()),
        "valid_to": pa.array([dt.date(2021, 1, 1), dt.date(2099, 1, 1), dt.date(2099, 1, 1)],
                             type=pa.date32()),
        "is_current": [False, True, True],
        "cust_name": ["Old", "New", "C2"],
    })


def _contracts() -> pa.Table:
    # has start_date/end_date names but each contract is a single, non-versioned row.
    return pa.table({
        "contract_id": ["ct1", "ct2", "ct3"],
        "start_date": pa.array([dt.date(2020, 1, 1)] * 3, type=pa.date32()),
        "end_date": pa.array([dt.date(2021, 1, 1)] * 3, type=pa.date32()),
    })


# --- detection ------------------------------------------------------------------


def test_discover_scd_detects_type2_dimension():
    from goldenmatch.semantic.discovery.scd import discover_scd

    scd = discover_scd(_customer_dim(), list(_customer_dim().column_names), table_name="customer_dim")
    assert scd is not None
    assert scd.business_key == "customer_id"
    assert scd.valid_from == "valid_from"
    assert scd.valid_to == "valid_to"
    assert scd.current_flag == "is_current"
    assert scd.scd_type == 2


def test_validity_names_without_versioning_are_not_scd():
    from goldenmatch.semantic.discovery.scd import discover_scd

    # start_date/end_date present, but every contract_id is unique -> not a versioned dim.
    assert discover_scd(_contracts(), list(_contracts().column_names), table_name="contracts") is None


def test_plain_table_is_not_scd():
    from goldenmatch.semantic.discovery.scd import discover_scd

    t = pa.table({"id": ["a", "b"], "amount": [1.0, 2.0]})
    assert discover_scd(t, ["id", "amount"], table_name="t") is None


# --- integration ----------------------------------------------------------------


def test_scd_flows_through_model_and_to_dict():
    m = discover_semantic_model({"customer_dim": _customer_dim()})
    assert any(s.business_key == "customer_id" for s in m.scd_dimensions)
    d = m.to_dict()
    assert any(s["business_key"] == "customer_id" for s in d["scd_dimensions"])
