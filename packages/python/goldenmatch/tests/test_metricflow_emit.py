"""Tests for the MetricFlow emitter (wedge B: emit the conformed entity declaration)."""
from __future__ import annotations

from goldenmatch.semantic import (
    emit_from_crosswalk,
    emit_metricflow_yaml,
    emit_semantic_model,
    parse_semantic_models,
)


def test_emit_declares_resolved_key_as_primary():
    sm = emit_semantic_model(
        "customers",
        resolved_key="resolved_entity_id",
        source_key="customer_id",
        measures=["revenue"],
        grain="order_date",
    )
    ents = {e["name"]: e for e in sm["entities"]}
    assert ents["customers"]["type"] == "primary"
    assert ents["customers"]["expr"] == "resolved_entity_id"
    # the original source key stays declared, but as a non-primary unique entity
    assert ents["customer_id"]["type"] == "unique"
    assert sm["defaults"]["agg_time_dimension"] == "order_date"
    assert sm["model"] == "ref('customers')"


def test_round_trips_through_parse():
    yaml_text = emit_metricflow_yaml(
        emit_semantic_model(
            "orders",
            resolved_key="resolved_entity_id",
            entity_name="customer",
            source_key="customer_id",
            measures=["amount", "order_count"],
            grain="order_date",
        )
    )
    spec = parse_semantic_models(yaml_text)[0]
    assert spec.model == "orders"
    assert spec.key == ["resolved_entity_id"]         # resolved key is the primary
    assert spec.foreign_keys == ["customer_id"]       # source key -> unique/foreign
    assert spec.measures == ["amount", "order_count"]
    assert spec.grain == ["order_date"]


def test_emit_multiple_models():
    a = emit_semantic_model("a", resolved_key="rid_a")
    b = emit_semantic_model("b", resolved_key="rid_b")
    specs = parse_semantic_models(emit_metricflow_yaml([a, b]))
    assert {s.model for s in specs} == {"a", "b"}


def test_no_source_key_or_measures_is_minimal():
    sm = emit_semantic_model("m", resolved_key="rid")
    assert [e["name"] for e in sm["entities"]] == ["m"]   # only the primary
    assert "measures" not in sm and "defaults" not in sm


def test_emit_from_crosswalk_uses_its_keys():
    class _XW:
        resolved_key = "resolved_entity_id"
        source_pk_column = "customer_id"

    spec = parse_semantic_models(
        emit_from_crosswalk(_XW(), "customers", measures=["revenue"])
    )[0]
    assert spec.key == ["resolved_entity_id"]
    assert spec.foreign_keys == ["customer_id"]
    assert spec.measures == ["revenue"]
