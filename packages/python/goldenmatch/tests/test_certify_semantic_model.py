"""Tests for the zero-config front door: certify_semantic_model (all dialects)."""
from __future__ import annotations

import pyarrow as pa
import pytest
from goldenmatch.semantic import (
    KeyCertification,
    SemanticCertification,
    certify_semantic_model,
    detect_dialect,
)

_METRICFLOW = """
semantic_models:
  - name: orders
    model: ref('orders')
    entities:
      - name: order
        type: primary
        expr: order_id
    measures:
      - name: revenue
        agg: sum
        expr: amount
"""

_CUBE = """
cubes:
  - name: orders
    sql_table: public.orders
    joins:
      - name: customers
        relationship: many_to_one
        sql: "{CUBE}.customer_id = {customers.id}"
  - name: customers
    sql_table: public.customers
    dimensions:
      - name: id
        sql: id
        primary_key: true
"""

_OSI = """
version: 0.2.0.dev0
semantic_model:
  - name: sales
    datasets:
      - name: customers
        primary_key: [id]
    relationships:
      - name: orders_to_customers
        from: orders
        to: customers
        from_columns: [customer_id]
        to_columns: [id]
"""


def test_detect_dialect():
    assert detect_dialect({"cubes": []}) == "cube"
    assert detect_dialect({"semantic_models": []}) == "metricflow"
    assert detect_dialect({"semantic_model": []}) == "osi"
    with pytest.raises(ValueError):
        detect_dialect({"version": 2})


def test_metricflow_front_door_certifies_and_flags_fanout():
    # order_id has a duplicate -> the key a metric joins on is unsafe
    frames = {"orders": pa.table({"order_id": [1, 1, 2], "amount": [10.0, 10.0, 5.0]})}
    rep = certify_semantic_model(_METRICFLOW, frames)
    assert isinstance(rep, SemanticCertification)
    assert rep.dialect == "metricflow"
    assert rep.n_certified == 1
    entry = rep.entries[0]
    assert isinstance(entry, KeyCertification)
    assert entry.target == "orders" and entry.key == ["order_id"]
    assert entry.certificate.max_fan_out == 2.0
    # revenue SUM double-counts across the duplicate key
    assert entry.certificate.measure_fan_out["amount"] > 1.0
    assert rep.untrustworthy == [entry]
    assert rep.all_trustworthy is False


def test_clean_key_is_trustworthy():
    frames = {"orders": pa.table({"order_id": [1, 2, 3], "amount": [1.0, 2.0, 3.0]})}
    rep = certify_semantic_model(_METRICFLOW, frames)
    assert rep.all_trustworthy is True
    assert rep.untrustworthy == []


def test_missing_frame_is_skipped_not_errored():
    rep = certify_semantic_model(_METRICFLOW, frames={})
    assert rep.n_certified == 0
    assert rep.skipped == ["orders"]


def test_cube_dialect_front_door():
    frames = {"customers": pa.table({"id": [1, 1, 2]})}
    rep = certify_semantic_model(_CUBE, frames)
    assert rep.dialect == "cube"
    assert rep.n_certified == 1
    entry = rep.entries[0]
    assert entry.target == "customers" and entry.key == ["id"]
    assert entry.certificate.estimate == 0.5      # duplicate id
    assert "join from orders" in entry.context


def test_osi_dialect_front_door():
    frames = {"customers": pa.table({"id": [1, 2, 3]})}
    rep = certify_semantic_model(_OSI, frames)
    assert rep.dialect == "osi"
    assert rep.n_certified == 1
    entry = rep.entries[0]
    assert entry.target == "customers" and entry.key == ["id"]
    assert entry.certificate.is_unique_at_grain is True
    assert "relationship orders_to_customers" in entry.context


def test_resolve_threads_to_all_dialects():
    # resolve is now applied uniformly across dialects (no "not applied" note).
    # A key-only frame has no attribute columns to resolve on, so the resolution
    # tier records that on the certificate and leaves its fields None (fail-open).
    frames = {"customers": pa.table({"id": [1, 2, 3], "name": ["a", "b", "c"]})}
    rep = certify_semantic_model(_OSI, frames, resolve=True)
    assert rep.note == ""                       # dialect-level note is gone
    assert rep.n_certified == 1
    # the resolution tier ran (or fail-open'd) on the certificate itself
    cert = rep.entries[0].certificate
    assert cert.resolved_entities is None or isinstance(cert.resolved_entities, int)


def test_accepts_path(tmp_path):
    p = tmp_path / "sm.yml"
    p.write_text(_METRICFLOW, encoding="utf-8")
    frames = {"orders": pa.table({"order_id": [1, 2], "amount": [1.0, 2.0]})}
    rep = certify_semantic_model(str(p), frames)
    assert rep.dialect == "metricflow" and rep.n_certified == 1
