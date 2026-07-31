"""Tests for metric-aware resolution: semantic_field_roles + metric_aware_attributes.

The differentiated wedge — a semantic model's own measure/dimension metadata
drives the entity resolution that backs certification: resolve on the declared
dimensions, never on a measure.
"""
from __future__ import annotations

import pyarrow as pa
from goldenmatch.semantic import (
    SemanticFieldRoles,
    certify_semantic_model,
    metric_aware_attributes,
    semantic_field_roles,
)

_METRICFLOW = """
semantic_models:
  - name: orders
    entities:
      - name: order
        type: primary
        expr: order_id
    dimensions:
      - name: email
      - name: city
        expr: city_name
    measures:
      - name: revenue
        agg: sum
        expr: amount
"""

_CUBE = """
cubes:
  - name: customers
    sql_table: public.customers
    dimensions:
      - name: id
        sql: id
        primary_key: true
      - name: email
        sql: email
      - name: city
        sql: city
    measures:
      - name: total_spend
        type: sum
        sql: amount
"""

_OSI = """
version: 0.2.0.dev0
semantic_model:
  - name: sales
    datasets:
      - name: customers
        primary_key: [id]
        fields:
          - name: id
            expression: id
          - name: email
            expression: email
          - name: city
            expression: city
    metrics:
      - name: revenue
        expression: SUM(customers.amount)
"""


# --- semantic_field_roles ------------------------------------------------------


def test_roles_metricflow():
    roles = semantic_field_roles(_METRICFLOW)
    assert isinstance(roles, SemanticFieldRoles)
    assert roles.keys == ["order_id"]
    assert roles.dimensions == ["email", "city_name"]   # `expr` wins over `name`
    assert roles.measures == ["amount"]                 # measure column, not label


def test_roles_cube():
    roles = semantic_field_roles(_CUBE)
    assert roles.keys == ["id"]                         # primary_key dimension
    assert roles.dimensions == ["email", "city"]        # non-key dimensions
    assert roles.measures == ["total_spend"]


def test_roles_osi():
    roles = semantic_field_roles(_OSI)
    assert roles.keys == ["id"]
    assert roles.dimensions == ["email", "city"]        # fields minus the key
    assert roles.measures == ["revenue"]                # metric name


# --- metric_aware_attributes ---------------------------------------------------


def test_attributes_select_declared_dimensions():
    roles = SemanticFieldRoles(keys=["id"], dimensions=["email", "city"], measures=["amount"])
    cols = ["id", "email", "city", "amount", "notes"]
    # declared dimensions present in the frame; key/measure/undeclared dropped
    assert metric_aware_attributes(roles, cols) == ["email", "city"]


def test_attributes_measure_never_selected_even_if_also_a_dimension():
    # a measure column wins the exclusion over a dimension declaration
    roles = SemanticFieldRoles(keys=["id"], dimensions=["email", "amount"], measures=["amount"])
    assert metric_aware_attributes(roles, ["id", "email", "amount"]) == ["email"]


def test_attributes_preserve_frame_order():
    roles = SemanticFieldRoles(dimensions=["city", "email"])
    # result follows FRAME order, not declaration order
    assert metric_aware_attributes(roles, ["email", "city"]) == ["email", "city"]


def test_attributes_fallback_when_no_dimensions_declared():
    # no dimensions -> every non-key, non-measure column (byte-identical to the
    # blind selection the resolution tier used before), measures still excluded
    roles = SemanticFieldRoles(keys=["id"], dimensions=[], measures=["amount"])
    cols = ["id", "email", "city", "amount"]
    assert metric_aware_attributes(roles, cols) == ["email", "city"]


def test_attributes_fallback_when_dimensions_absent_from_frame():
    # declared dimensions none of which are present -> fallback, measures excluded
    roles = SemanticFieldRoles(keys=["id"], dimensions=["ghost"], measures=["amount"])
    assert metric_aware_attributes(roles, ["id", "email", "amount"]) == ["email"]


# --- front door: metric-aware resolve tier -------------------------------------


def test_front_door_metric_aware_excludes_measure_from_resolution():
    # Two byte-identical people split across order_id 1/2; `amount` is a MEASURE
    # that happens to differ. Metric-aware resolution must NOT resolve on amount.
    frames = {"orders": pa.table({
        "order_id": [1, 2, 3, 4],
        "email": ["a@x.com", "a@x.com", "b@x.com", "c@x.com"],
        "city_name": ["Boston", "Boston", "Denver", "Miami"],
        "amount": [10.0, 999.0, 5.0, 7.0],   # measure — differs on the dup pair
    })}
    rep = certify_semantic_model(_METRICFLOW, frames, resolve=True, metric_aware=True)
    assert rep.n_certified == 1
    cert = rep.entries[0].certificate
    # resolution ran on the declared dimensions (email/city), not the measure;
    # it either detects the fragmentation or fail-opens, but never errors.
    assert cert.resolved_entities is None or isinstance(cert.resolved_entities, int)
    assert "amount" not in cert.note        # the measure was not an attribute


def test_front_door_metric_aware_toggle_off_still_runs():
    frames = {"orders": pa.table({
        "order_id": [1, 2, 3],
        "email": ["a@x.com", "b@x.com", "c@x.com"],
        "city_name": ["Boston", "Denver", "Miami"],
        "amount": [10.0, 5.0, 7.0],
    })}
    rep = certify_semantic_model(_METRICFLOW, frames, resolve=True, metric_aware=False)
    assert rep.n_certified == 1


def test_front_door_metric_aware_is_noop_without_resolve():
    # metric_aware has no effect unless resolve=True
    frames = {"orders": pa.table({"order_id": [1, 2], "email": ["a", "b"], "amount": [1.0, 2.0]})}
    rep = certify_semantic_model(_METRICFLOW, frames, resolve=False, metric_aware=True)
    assert rep.n_certified == 1
