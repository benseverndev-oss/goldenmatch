"""Applied catalog (PR-8) — write the namer's VERIFIED names into the emitted YAML.

`apply_names` turns the advisory `ProposedModel.naming` layer into an applied catalog:
entity/measure business names land in native MetricFlow `label:` fields; dimension +
value-glossary names land in `meta.goldenmatch.glossary`. It is opt-in, post-
certification, and cosmetic — only `verified=True` names are written, `apply_names=False`
is byte-identical to today, and the certification verdict is untouched. Driven by a
`FakeNamerBackend` (no real LLM) so the tests are deterministic.
"""
from __future__ import annotations

import json

import pyarrow as pa
import yaml
from goldenmatch.semantic import discover_semantic_model


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


class FakeNamerBackend:
    """Names every target; verifies all EXCEPT `value:orders.status=C` (unsupported),
    so the applied catalog must omit that one glossary value."""

    UNSUPPORTED = "value:orders.status=C"

    def propose(self, prompt: str) -> str:
        from goldenmatch.semantic.discovery.namer import _targets_in_prompt

        targets = _targets_in_prompt(prompt)
        if "VERIFY" in prompt.upper():
            return json.dumps({"verdicts": [
                {"target": t, "supported": t != self.UNSUPPORTED,
                 "confidence": 0.2 if t == self.UNSUPPORTED else 0.9}
                for t in targets
            ]})
        return json.dumps({"names": [
            {"target": t, "name": _canned(t), "evidence": "e"} for t in targets
        ]})


def _canned(target: str) -> str:
    return {
        "entity:entity": "Customers",
        "entity:entity_2": "Orders",
        "measure:orders.amount": "Total Revenue",
        "dimension:orders.status": "Order Status",
        "dimension:customers.region": "Region",
        "value:orders.status=A": "Active",
        "value:orders.status=C": "Churned",
    }.get(target, target.split(":")[-1])


def _sm_by_name(model_yaml: str) -> dict:
    doc = yaml.safe_load(model_yaml)
    return {sm["name"]: sm for sm in doc["semantic_models"]}


# --- entity + measure names land in native label: -------------------------------


def test_apply_names_writes_entity_and_measure_labels():
    from goldenmatch.semantic.discovery.namer import apply_names

    model = discover_semantic_model(_fixture(), name=True, namer_backend=FakeNamerBackend())
    labeled = apply_names(model)

    sms = _sm_by_name(labeled)
    assert sms["orders"]["label"] == "Orders"
    assert sms["customers"]["label"] == "Customers"
    amount = next(m for m in sms["orders"]["measures"] if m["name"] == "amount")
    assert amount["label"] == "Total Revenue"


# --- dimension + value glossary land in meta.goldenmatch.glossary ---------------


def test_apply_names_writes_dimension_and_value_glossary():
    from goldenmatch.semantic.discovery.namer import apply_names

    model = discover_semantic_model(_fixture(), name=True, namer_backend=FakeNamerBackend())
    gloss = _sm_by_name(apply_names(model))["orders"]["meta"]["goldenmatch"]["glossary"]

    assert gloss["dimensions"]["status"] == "Order Status"
    assert gloss["values"]["status"]["A"] == "Active"


def test_apply_names_omits_unverified_names():
    from goldenmatch.semantic.discovery.namer import apply_names

    model = discover_semantic_model(_fixture(), name=True, namer_backend=FakeNamerBackend())
    gloss = _sm_by_name(apply_names(model))["orders"]["meta"]["goldenmatch"]["glossary"]

    # value:orders.status=C failed self-critique -> never written.
    assert "C" not in gloss["values"].get("status", {})


# --- non-authoritative: default off, cosmetic, verdict untouched ----------------


def test_apply_names_false_is_byte_identical():
    base = discover_semantic_model(_fixture())
    applied = discover_semantic_model(
        _fixture(), name=True, apply_names=True, namer_backend=FakeNamerBackend()
    )
    # apply_names=True changes the yaml; the structural (no-apply) baseline does not.
    assert base.yaml != applied.yaml
    assert discover_semantic_model(_fixture()).yaml == base.yaml


def test_apply_names_preserves_certification_and_key_integrity():
    base = discover_semantic_model(_fixture())
    applied = discover_semantic_model(
        _fixture(), name=True, apply_names=True, namer_backend=FakeNamerBackend()
    )
    # Labels/meta are cosmetic: the certification verdict is unchanged, and the existing
    # meta.goldenmatch.key_integrity block still round-trips alongside the new glossary.
    assert applied.certification == base.certification
    orders = _sm_by_name(applied.yaml)["orders"]
    assert "key_integrity" in orders["meta"]["goldenmatch"]
    assert "glossary" in orders["meta"]["goldenmatch"]


def test_to_dict_includes_applied_yaml():
    applied = discover_semantic_model(
        _fixture(), name=True, apply_names=True, namer_backend=FakeNamerBackend()
    )
    d = applied.to_dict()
    assert d["yaml"] == applied.yaml
    assert "Total Revenue" in d["yaml"]


def test_apply_names_graceful_without_backend():
    base = discover_semantic_model(_fixture())
    # apply_names requested but no backend resolves -> nothing to apply, yaml unchanged.
    applied = discover_semantic_model(_fixture(), name=True, apply_names=True, namer_backend=None)
    assert applied.yaml == base.yaml
