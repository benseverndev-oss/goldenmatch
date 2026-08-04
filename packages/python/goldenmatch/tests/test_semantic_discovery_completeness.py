"""Model completeness / trust score (PR-16).

A headline self-assessment of the discovered model: what fraction of tables have a
certified grain, sum-safe measures, and a certified join — a grain-weighted 0..1 score —
plus an explicit list of the gaps. No new detection; it aggregates the existing signals.
"""
from __future__ import annotations

import pyarrow as pa
from goldenmatch.semantic import discover_semantic_model


def _clean() -> dict[str, pa.Table]:
    customers = pa.table({"customer_id": ["c1", "c2", "c3"], "region": ["w", "e", "w"]})
    orders = pa.table({"order_id": ["o1", "o2", "o3"], "customer_id": ["c1", "c1", "c2"],
                       "amount": [1.0, 2.0, 3.0]})
    return {"customers": customers, "orders": orders}


# --- scoring --------------------------------------------------------------------


def test_clean_model_scores_high_and_lists_measure_gap():
    m = discover_semantic_model(_clean())
    c = m.completeness
    assert c.n_tables == 2
    assert c.tables_with_grain == 2
    # both grains certified + connected via the FK join + orders has a sum-safe measure;
    # customers has none -> grain 0.5*1 + conn 0.25*1 + measure 0.25*0.5 = 0.875.
    assert abs(c.score - 0.875) < 1e-6
    assert any(g.table == "customers" and g.kind == "no_measures" for g in c.gaps)


def test_no_clean_key_is_a_named_gap_and_tanks_the_score():
    # a duplicated row -> no unique key at any arity -> no certified grain.
    keyless = pa.table({"a": ["x", "x", "y"], "b": ["1", "1", "2"]})
    m = discover_semantic_model({"keyless": keyless})
    c = m.completeness
    assert c.tables_with_grain == 0
    assert any(g.kind == "no_grain" for g in c.gaps)
    # grain 0.5*0 + conn 0.25*1 (single table, trivially connected) + measure 0.25*0 = 0.25.
    assert abs(c.score - 0.25) < 1e-6


def test_isolated_table_is_flagged():
    # two unrelated tables -> each is isolated (no certified join between them).
    tables = {
        "a": pa.table({"a_id": ["1", "2", "3"], "v": [1.0, 2.0, 3.0]}),
        "b": pa.table({"b_id": ["x", "y", "z"], "w": [4.0, 5.0, 6.0]}),
    }
    m = discover_semantic_model(tables)
    assert any(g.kind == "isolated" for g in m.completeness.gaps)


# --- integration ----------------------------------------------------------------


def test_completeness_in_to_dict():
    d = discover_semantic_model(_clean()).to_dict()
    assert "completeness" in d
    assert d["completeness"]["tables_with_grain"] == 2
    assert isinstance(d["completeness"]["gaps"], list)
