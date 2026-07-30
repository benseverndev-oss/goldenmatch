"""Tests for the OSI / Apache Ossie reader + emitter (wedge C)."""
from __future__ import annotations

import pyarrow as pa
from goldenmatch.semantic import (
    OsiModel,
    certify_osi_relationships,
    emit_osi_from_crosswalk,
    emit_osi_yaml,
    osi_join_keys,
    parse_osi_models,
)

# A real-shaped Ossie document (TPC-DS): version + semantic_model list, datasets
# with primary_key + dialect-scoped field expressions, relationships from/to.
_DOC = """
version: "0.2.0.dev0"
semantic_model:
  - name: tpcds_retail_model
    description: retail model
    datasets:
      - name: store_sales
        source: tpcds.public.store_sales
        primary_key: [ss_item_sk, ss_ticket_number]
        fields:
          - name: ss_customer_sk
            expression: {dialects: [{dialect: ANSI_SQL, expression: ss_customer_sk}]}
            datatype: Integer
      - name: customer
        source: tpcds.public.customer
        primary_key: [c_customer_sk]
        fields:
          - name: c_customer_sk
            expression: {dialects: [{dialect: ANSI_SQL, expression: c_customer_sk}]}
            datatype: Integer
    relationships:
      - name: store_sales_to_customer
        from: store_sales
        to: customer
        from_columns: [ss_customer_sk]
        to_columns: [c_customer_sk]
    metrics:
      - name: total_sales
        expression: {dialects: [{dialect: ANSI_SQL, expression: SUM(store_sales.ss_ext_sales_price)}]}
        datatype: Decimal
"""


def test_parse_datasets_relationships_metrics():
    m = parse_osi_models(_DOC)[0]
    assert isinstance(m, OsiModel)
    assert m.name == "tpcds_retail_model"
    assert [d.name for d in m.datasets] == ["store_sales", "customer"]
    customer = next(d for d in m.datasets if d.name == "customer")
    assert customer.primary_key == ["c_customer_sk"]
    assert customer.source == "tpcds.public.customer"
    # composite primary key parsed
    ss = next(d for d in m.datasets if d.name == "store_sales")
    assert ss.primary_key == ["ss_item_sk", "ss_ticket_number"]
    # dialect-scoped field expression unwrapped
    assert ss.fields[0].name == "ss_customer_sk" and ss.fields[0].expression == "ss_customer_sk"
    r = m.relationships[0]
    assert (r.from_dataset, r.to_dataset) == ("store_sales", "customer")
    assert r.from_columns == ["ss_customer_sk"] and r.to_columns == ["c_customer_sk"]
    assert m.metrics[0].expression == "SUM(store_sales.ss_ext_sales_price)"


def test_join_keys_are_what_to_resolve():
    m = parse_osi_models(_DOC)[0]
    keys = {(k["dataset"], tuple(k["columns"]), k["side"]) for k in osi_join_keys(m)}
    assert ("customer", ("c_customer_sk",), "one") in keys
    assert ("store_sales", ("ss_customer_sk",), "many") in keys


def test_round_trips_through_parse():
    m = parse_osi_models(_DOC)[0]
    m2 = parse_osi_models(emit_osi_yaml(m))[0]
    assert [d.name for d in m2.datasets] == [d.name for d in m.datasets]
    assert next(d for d in m2.datasets if d.name == "customer").primary_key == ["c_customer_sk"]
    r2 = m2.relationships[0]
    assert (r2.from_dataset, r2.to_dataset, r2.from_columns, r2.to_columns) == \
        ("store_sales", "customer", ["ss_customer_sk"], ["c_customer_sk"])
    # schema-faithful: no invented cardinality key
    assert "cardinality" not in emit_osi_yaml(m)


def test_emit_from_crosswalk_declares_conformed_join():
    class _XW:
        source_pk_column = "customer_id"
        resolved_key = "resolved_entity_id"
        n_records = 12
        n_entities = 11
        reduction_ratio = 1 - 11 / 12

    y = emit_osi_from_crosswalk(_XW(), source_dataset="store_sales",
                                crosswalk_source="analytics.customer_crosswalk")
    m = parse_osi_models(y)[0]
    xw = next(d for d in m.datasets if d.name == "crosswalk")
    assert xw.primary_key == ["customer_id"]
    assert {f.name for f in xw.fields} == {"source", "customer_id", "resolved_entity_id"}
    r = m.relationships[0]
    assert (r.from_dataset, r.to_dataset) == ("store_sales", "crosswalk")
    assert r.from_columns == ["customer_id"] and r.to_columns == ["customer_id"]
    # GoldenMatch provenance rides in custom_extensions (schema-safe)
    gm = m.custom_extensions["goldenmatch"]
    assert gm["resolved_key"] == "resolved_entity_id" and gm["n_entities"] == 11


def test_certify_osi_relationships_bridges_wedge_a():
    m = parse_osi_models(_DOC)[0]
    # customer.c_customer_sk has a duplicate -> the key metrics join on is unsafe
    frames = {"customer": pa.table({"c_customer_sk": [1, 1, 2], "name": ["a", "a", "b"]})}
    rep = certify_osi_relationships(m, frames)
    assert len(rep) == 1
    entry = rep[0]
    assert entry["dataset"] == "customer" and entry["key"] == ["c_customer_sk"]
    assert entry["certificate"].estimate == 0.5
    assert entry["certificate"].max_fan_out == 2.0


def test_certify_osi_relationships_accepts_source_and_skips_absent_frames():
    # passing the raw doc (not a parsed model) + no frames -> nothing to certify
    assert certify_osi_relationships(_DOC, frames={}) == []
