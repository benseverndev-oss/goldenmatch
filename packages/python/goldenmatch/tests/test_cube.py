"""Tests for the Cube (cube.dev) reader + emitter (semantic-layer follow-on)."""
from __future__ import annotations

import pyarrow as pa
from goldenmatch.semantic import (
    Cube,
    certify_cube_joins,
    cube_join_keys,
    emit_cube_from_crosswalk,
    emit_cube_yaml,
    parse_cube_models,
)

# A real-shaped Cube data model: cubes with a primary_key dimension + a join
# whose relationship + sql (single-brace member refs) encode the FK->PK edge.
_DOC = """
cubes:
  - name: orders
    sql_table: public.orders
    joins:
      - name: customers
        relationship: many_to_one
        sql: "{CUBE}.customer_id = {customers.id}"
    dimensions:
      - name: id
        sql: id
        type: number
        primary_key: true
      - name: customer_id
        sql: customer_id
        type: number
    measures:
      - name: count
        type: count
      - name: total_amount
        type: sum
        sql: amount
  - name: customers
    sql_table: public.customers
    dimensions:
      - name: id
        sql: id
        type: number
        primary_key: true
      - name: name
        sql: name
        type: string
"""


def test_parse_cubes_dimensions_joins_measures():
    cubes = parse_cube_models(_DOC)
    assert [c.name for c in cubes] == ["orders", "customers"]
    orders = cubes[0]
    assert orders.sql_table == "public.orders"
    assert orders.primary_key == ["id"]
    # measure without sql (count) parses
    assert {m.name for m in orders.measures} == {"count", "total_amount"}
    j = orders.joins[0]
    assert (j.name, j.relationship) == ("customers", "many_to_one")
    assert cubes[1].primary_key == ["id"]


def test_composite_primary_key():
    doc = """
cubes:
  - name: order_lines
    sql_table: public.order_lines
    dimensions:
      - name: order_id
        sql: order_id
        type: number
        primary_key: true
      - name: line_no
        sql: line_no
        type: number
        primary_key: true
"""
    assert parse_cube_models(doc)[0].primary_key == ["order_id", "line_no"]


def test_legacy_relationship_aliases_normalize():
    doc = """
cubes:
  - name: orders
    sql_table: public.orders
    joins:
      - name: customers
        relationship: belongsTo
        sql: "{CUBE}.customer_id = {customers.id}"
"""
    cube = parse_cube_models(doc)[0]
    assert cube.joins[0].relationship == "many_to_one"
    # ...and it emits the modern snake_case form
    assert "many_to_one" in emit_cube_yaml(cube)


def test_join_keys_are_what_to_resolve():
    orders = parse_cube_models(_DOC)[0]
    jk = cube_join_keys(orders)[0]
    assert (jk["from_cube"], jk["to_cube"]) == ("orders", "customers")
    assert jk["relationship"] == "many_to_one"
    assert jk["from_columns"] == ["customer_id"] and jk["to_columns"] == ["id"]


def test_round_trips_through_parse():
    cubes = parse_cube_models(_DOC)
    cubes2 = parse_cube_models(emit_cube_yaml(cubes))
    assert [c.name for c in cubes2] == [c.name for c in cubes]
    assert cubes2[0].primary_key == ["id"]
    j = cubes2[0].joins[0]
    assert (j.name, j.relationship, j.sql) == \
        ("customers", "many_to_one", "{CUBE}.customer_id = {customers.id}")


def test_emit_from_crosswalk_declares_conformed_join():
    class _XW:
        source_pk_column = "customer_id"
        resolved_key = "resolved_entity_id"
        n_records = 12
        n_entities = 11
        reduction_ratio = 1 - 11 / 12

    y = emit_cube_from_crosswalk(_XW(), source_cube="orders",
                                 crosswalk_sql_table="analytics.customer_crosswalk")
    cubes = {c.name: c for c in parse_cube_models(y)}
    xw = cubes["crosswalk"]
    assert xw.primary_key == ["customer_id"]
    assert {d.name for d in xw.dimensions} == {"source", "customer_id", "resolved_entity_id"}
    assert xw.sql_table == "analytics.customer_crosswalk"
    # source cube carries the many_to_one join onto the crosswalk PK
    j = cubes["orders"].joins[0]
    assert (j.name, j.relationship) == ("crosswalk", "many_to_one")
    assert j.sql == "{CUBE}.customer_id = {crosswalk.customer_id}"
    # provenance rides in meta (Cube's sanctioned free-form extension point)
    gm = xw.meta["goldenmatch"]
    assert gm["resolved_key"] == "resolved_entity_id" and gm["n_entities"] == 11


def test_certify_cube_joins_bridges_wedge_a():
    cubes = parse_cube_models(_DOC)
    # customers.id has a duplicate -> the key the metrics join on is unsafe
    frames = {"customers": pa.table({"id": [1, 1, 2], "name": ["a", "a", "b"]})}
    rep = certify_cube_joins(cubes, frames)
    assert len(rep) == 1
    entry = rep[0]
    assert entry["to_cube"] == "customers" and entry["key"] == ["id"]
    assert entry["certificate"].estimate == 0.5
    assert entry["certificate"].max_fan_out == 2.0


def test_certify_cube_joins_accepts_source_and_skips_absent_frames():
    assert certify_cube_joins(_DOC, frames={}) == []


def test_cube_dataclass_primary_key_property():
    c = Cube(name="x")
    assert c.primary_key == []
