"""Cube/OSI discovery emit (PR-9) — discover_semantic_model beyond metricflow.

`discover_semantic_model(dialect="cube"|"osi")` emits a Cube / OSI draft (grain ->
primary key, sum-safe measures, the certified trustworthy join graph) and re-certifies
it end-to-end, exactly like the metricflow path. The metricflow output stays
byte-identical. Driven on the customers/orders fixture.
"""
from __future__ import annotations

import json

import pyarrow as pa
from goldenmatch.semantic import discover_semantic_model
from goldenmatch.semantic.cube import parse_cube_models
from goldenmatch.semantic.osi import parse_osi_models


class _FakeBackend:
    """Names every target and verifies all (for the apply-names dialect tests)."""

    def propose(self, prompt: str) -> str:
        from goldenmatch.semantic.discovery.namer import _targets_in_prompt

        targets = _targets_in_prompt(prompt)
        if "VERIFY" in prompt.upper():
            return json.dumps({"verdicts": [
                {"target": t, "supported": True, "confidence": 0.9} for t in targets
            ]})
        return json.dumps({"names": [
            {"target": t, "name": _canned(t), "evidence": "e"} for t in targets
        ]})


def _canned(target: str) -> str:
    return {
        "entity:entity_2": "Orders",
        "measure:orders.amount": "Total Revenue",
        "dimension:orders.status": "Order Status",
        "value:orders.status=A": "Active",
    }.get(target, target.split(":")[-1])


def _fixture() -> dict[str, pa.Table]:
    customers = pa.table({
        "customer_id": ["c1", "c2", "c3"],
        "region": ["west", "east", "west"],
    })
    orders = pa.table({
        "order_id": ["o1", "o2", "o3", "o4"],
        "customer_id": ["c1", "c1", "c2", "c3"],
        "amount": [10.0, 20.0, 30.0, 40.0],
        "status": ["A", "C", "A", "C"],
    })
    return {"customers": customers, "orders": orders}


# --- metricflow unchanged -------------------------------------------------------


def test_metricflow_still_default_and_unchanged():
    m = discover_semantic_model(_fixture())
    assert m.dialect == "metricflow"
    # dialect="metricflow" is byte-identical to the default.
    assert discover_semantic_model(_fixture(), dialect="metricflow").yaml == m.yaml


# --- cube structural emit + certification ---------------------------------------


def test_cube_emit_grain_measures_and_certification():
    m = discover_semantic_model(_fixture(), dialect="cube")
    assert m.dialect == "cube"

    cubes = {c.name: c for c in parse_cube_models(m.yaml)}
    assert {"customers", "orders"} <= set(cubes)

    orders = cubes["orders"]
    # grain -> a composite-ready primary key.
    assert orders.primary_key == ["order_id"]
    # sum-safe measure carried.
    assert any(mm.name == "amount" for mm in orders.measures)
    # key-integrity verdict embedded in meta.goldenmatch.
    assert orders.meta and "key_integrity" in orders.meta.get("goldenmatch", {})

    # Re-certified end-to-end.
    assert m.certification.get("all_trustworthy") is True


# --- osi structural emit + certification ----------------------------------------


def test_osi_emit_grain_measures_and_certification():
    m = discover_semantic_model(_fixture(), dialect="osi")
    assert m.dialect == "osi"

    datasets = {d.name: d for model in parse_osi_models(m.yaml) for d in model.datasets}
    assert {"customers", "orders"} <= set(datasets)

    orders = datasets["orders"]
    assert orders.primary_key == ["order_id"]
    # a metric referencing the sum-safe amount column.
    metrics = [mt for model in parse_osi_models(m.yaml) for mt in model.metrics]
    assert any("amount" in mt.expression for mt in metrics)

    assert m.certification.get("all_trustworthy") is True


# --- unknown dialect still rejected ---------------------------------------------


def test_unknown_dialect_still_raises():
    import pytest

    with pytest.raises(ValueError, match="unsupported emit dialect"):
        discover_semantic_model(_fixture(), dialect="nonsense")


# --- certified join graph emitted natively --------------------------------------


def test_cube_emits_certified_join():
    m = discover_semantic_model(_fixture(), dialect="cube")
    orders = {c.name: c for c in parse_cube_models(m.yaml)}["orders"]
    join = next((j for j in orders.joins if j.name == "customers"), None)
    assert join is not None
    assert join.relationship == "many_to_one"
    assert "customer_id" in join.sql


def test_osi_emits_certified_relationship():
    m = discover_semantic_model(_fixture(), dialect="osi")
    rels = [r for model in parse_osi_models(m.yaml) for r in model.relationships]
    rel = next((r for r in rels if r.from_dataset == "orders" and r.to_dataset == "customers"), None)
    assert rel is not None
    assert rel.from_columns == ["customer_id"]
    assert rel.to_columns == ["customer_id"]


# --- dialect-aware apply-names --------------------------------------------------


def test_cube_apply_names_writes_title_and_glossary():
    import yaml as _yaml

    m = discover_semantic_model(
        _fixture(), dialect="cube", name=True, apply_names=True, namer_backend=_FakeBackend()
    )
    orders = {c["name"]: c for c in _yaml.safe_load(m.yaml)["cubes"]}["orders"]
    # entity business name -> the cube's title; measure name -> the measure's title.
    assert orders["title"] == "Orders"
    amount = next(mm for mm in orders["measures"] if mm["name"] == "amount")
    assert amount["title"] == "Total Revenue"
    # dimension + value glossary -> meta.goldenmatch.glossary (sibling of key_integrity).
    gloss = orders["meta"]["goldenmatch"]["glossary"]
    assert gloss["dimensions"]["status"] == "Order Status"
    assert gloss["values"]["status"]["A"] == "Active"


def test_osi_apply_names_writes_field_label_and_glossary():
    import yaml as _yaml

    m = discover_semantic_model(
        _fixture(), dialect="osi", name=True, apply_names=True, namer_backend=_FakeBackend()
    )
    model0 = _yaml.safe_load(m.yaml)["semantic_model"][0]
    datasets = {d["name"]: d for d in model0["datasets"]}
    status = next(f for f in datasets["orders"]["fields"] if f["name"] == "status")
    # dimension column -> the OSI field's native label.
    assert status["label"] == "Order Status"
    # value glossary -> custom_extensions.goldenmatch.glossary.
    gloss = model0["custom_extensions"]["goldenmatch"]["glossary"]
    assert gloss["values"]["status"]["A"] == "Active"
