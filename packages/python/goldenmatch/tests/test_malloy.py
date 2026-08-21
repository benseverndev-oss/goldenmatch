"""Malloy (malloydata.dev) BI dialect: parse (structured + DSL) + certify + emit,
and the front-door wiring.

Malloy's identity is a source's `primary_key`, and `join_one`/`join_many`/
`join_cross` ride on it. These lock the structured + DSL parse, the bridge to wedge
A (`certify_malloy_joins`, one-side-key direction), the crosswalk emit (wedge B),
and that `certify_semantic_model` auto-detects a top-level `sources:` as "malloy".
"""
from __future__ import annotations

from types import SimpleNamespace

from goldenmatch.semantic.certify import certify_semantic_model, detect_dialect
from goldenmatch.semantic.malloy import (
    MalloyJoin,
    MalloyModel,
    MalloySource,
    certify_malloy_joins,
    emit_malloy_from_crosswalk,
    emit_malloy_source,
    malloy_join_keys,
    parse_malloy_models,
)

# Structured projection: an orders source that join_one's to customers on the
# customer key. The one-side (customers.id) is the identity a per-customer metric
# depends on.
_MODEL_DOC = {
    "sources": [
        {"name": "customers", "table": "customers", "primary_key": "id",
         "measures": ["lifetime_value"]},
        {"name": "orders", "table": "orders", "primary_key": "order_id",
         "joins": [{"name": "customers", "relationship": "one",
                    "on": "orders.customer_id = customers.id"}],
         "measures": ["amount"]},
    ],
}

_DSL = """
source: customers is table('customers') {
  primary_key: id
  measure: lifetime_value is sum(amount)
}

source: orders is table('orders') {
  primary_key: order_id
  join_one: customers on orders.customer_id = customers.id
  measure: amount is sum(total)
  dimension: order_month is month(created_at)
}
"""


def test_parse_structured():
    model = parse_malloy_models(_MODEL_DOC)
    assert [s.name for s in model.sources] == ["customers", "orders"]
    orders = model.source_by_name("orders")
    assert orders.primary_key == ["order_id"]
    assert orders.joins[0].name == "customers"
    assert orders.joins[0].relationship == "one"


def test_parse_dsl_text():
    model = parse_malloy_models(_DSL)
    assert {s.name for s in model.sources} == {"customers", "orders"}
    customers = model.source_by_name("customers")
    assert customers.table == "customers"
    assert customers.primary_key == ["id"]
    assert customers.measures == ["lifetime_value"]
    orders = model.source_by_name("orders")
    assert orders.primary_key == ["order_id"]
    assert len(orders.joins) == 1
    assert orders.joins[0].name == "customers"
    assert orders.joins[0].relationship == "one"
    assert "orders.customer_id = customers.id" in orders.joins[0].on
    assert orders.dimensions == ["order_month"]


def test_join_keys_parses_on_condition():
    jk = malloy_join_keys(parse_malloy_models(_MODEL_DOC))
    assert len(jk) == 1
    j = jk[0]
    assert j["from_source"] == "orders"
    assert j["to_source"] == "customers"
    assert j["relationship"] == "one"
    assert j["from_columns"] == ["customer_id"]
    assert j["to_columns"] == ["id"]


def test_certify_join_one_certifies_the_to_side_key():
    # customers.id is the one-side; it must be unique. Here it is.
    frames = {
        "customers": {"id": ["a", "b", "c"], "lifetime_value": [10, 20, 30]},
        "orders": {"order_id": [1, 2, 3, 4], "customer_id": ["a", "a", "b", "c"]},
    }
    reps = certify_malloy_joins(parse_malloy_models(_MODEL_DOC), frames)
    assert len(reps) == 1
    assert reps[0]["to_source"] == "customers"
    assert reps[0]["key"] == ["id"]
    assert reps[0]["certificate"].is_trustworthy()


def test_certify_catches_a_non_unique_one_side_key():
    # customers.id duplicated -> a per-customer metric fans out.
    frames = {
        "customers": {"id": ["a", "a", "b"], "lifetime_value": [10, 10, 20]},
        "orders": {"order_id": [1, 2, 3], "customer_id": ["a", "a", "b"]},
    }
    reps = certify_malloy_joins(parse_malloy_models(_MODEL_DOC), frames)
    cert = reps[0]["certificate"]
    assert not cert.is_unique_at_grain
    assert cert.max_fan_out == 2.0


def test_join_many_certifies_the_declaring_side():
    # join_many: the declaring (from) source is the one-side.
    doc = {"sources": [
        {"name": "customers", "table": "customers", "primary_key": "id",
         "joins": [{"name": "orders", "relationship": "many",
                    "on": "customers.id = orders.customer_id"}]},
    ]}
    frames = {"customers": {"id": ["a", "b", "c"]}}
    reps = certify_malloy_joins(parse_malloy_models(doc), frames)
    assert reps[0]["key"] == ["id"]  # the declaring source's key, not orders'
    assert reps[0]["certificate"].is_unique_at_grain


def test_join_cross_is_skipped():
    doc = {"sources": [
        {"name": "a", "table": "a", "primary_key": "id",
         "joins": [{"name": "b", "relationship": "cross"}]},
    ]}
    reps = certify_malloy_joins(parse_malloy_models(doc), {"a": {"id": [1, 2]}, "b": {"x": [1]}})
    assert reps == []


def test_certify_semantic_model_auto_detects_malloy():
    assert detect_dialect(_MODEL_DOC) == "malloy"
    frames = {
        "customers": {"id": ["a", "a", "b"], "lifetime_value": [10, 10, 20]},
        "orders": {"order_id": [1, 2, 3], "customer_id": ["a", "a", "b"]},
    }
    report = certify_semantic_model(_MODEL_DOC, frames)
    assert report.dialect == "malloy"
    assert report.n_certified == 1
    assert report.entries[0].target == "customers"
    assert report.entries[0].context == "join from orders"
    assert not report.all_trustworthy


def test_emit_source_round_trips_through_dsl_parser():
    model = MalloyModel(sources=[
        MalloySource(name="orders", table="orders", primary_key=["order_id"],
                     joins=[MalloyJoin(name="customers", relationship="one",
                                       on="orders.customer_id = customers.id")]),
    ])
    text = emit_malloy_source(model)
    back = parse_malloy_models(text)
    orders = back.source_by_name("orders")
    assert orders.table == "orders"
    assert orders.primary_key == ["order_id"]
    assert orders.joins[0].name == "customers"
    assert orders.joins[0].relationship == "one"


def test_emit_from_crosswalk_returns_text_and_provenance():
    crosswalk = SimpleNamespace(
        source_pk_column="source_pk", resolved_key="resolved_entity_id",
        n_records=100, n_entities=60, reduction_ratio=0.4,
    )
    text, provenance = emit_malloy_from_crosswalk(crosswalk, source_name="orders")
    back = parse_malloy_models(text)
    xw = back.source_by_name("crosswalk")
    assert xw.primary_key == ["source_pk"]
    orders = back.source_by_name("orders")
    assert orders.joins[0].name == "crosswalk"
    assert provenance["resolved_key"] == "resolved_entity_id"


def test_semantic_field_roles_reads_malloy():
    from goldenmatch.semantic.blocking import semantic_field_roles

    roles = semantic_field_roles(_MODEL_DOC)
    assert "id" in roles.keys
    assert "order_id" in roles.keys
    assert "lifetime_value" in roles.measures
    assert "amount" in roles.measures
