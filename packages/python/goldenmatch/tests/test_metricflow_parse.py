"""Tests for goldenmatch.semantic.parse_semantic_models (dbt/MetricFlow reader)."""
from __future__ import annotations

from goldenmatch.semantic import DeclaredKeySpec, parse_semantic_models

_YAML = """
semantic_models:
  - name: orders
    model: ref('orders')
    defaults:
      agg_time_dimension: order_date
    entities:
      - name: order
        type: primary
        expr: order_id
      - name: customer
        type: foreign
        expr: customer_id
    measures:
      - name: order_total
        agg: sum
        expr: amount
      - name: order_count
        agg: count
  - name: customers
    model: ref('customers')
    entities:
      - name: customer
        type: primary
    measures: []
  - name: no_primary
    model: ref('events')
    entities:
      - name: session
        type: foreign
        expr: session_id
"""


def test_parses_primary_entity_measures_and_grain():
    specs = parse_semantic_models(_YAML)
    by_model = {s.model: s for s in specs}
    # the model with no primary/natural entity is skipped
    assert set(by_model) == {"orders", "customers"}

    orders = by_model["orders"]
    assert orders.key == ["order_id"]          # entity expr wins over name
    assert orders.measures == ["amount", "order_count"]  # expr, else name
    assert orders.grain == ["order_date"]
    assert orders.foreign_keys == ["customer_id"]


def test_entity_name_used_when_no_expr():
    specs = parse_semantic_models(_YAML)
    customers = next(s for s in specs if s.model == "customers")
    assert customers.key == ["customer"]       # falls back to entity name
    assert customers.grain is None
    assert customers.measures == []


def test_accepts_dict_and_path(tmp_path):
    p = tmp_path / "sm.yml"
    p.write_text(_YAML, encoding="utf-8")
    from_path = parse_semantic_models(p)
    from_str = parse_semantic_models(_YAML)
    assert [s.model for s in from_path] == [s.model for s in from_str]
    assert isinstance(from_path[0], DeclaredKeySpec)


def test_empty_or_missing_semantic_models():
    assert parse_semantic_models("version: 2") == []
    assert parse_semantic_models({}) == []
