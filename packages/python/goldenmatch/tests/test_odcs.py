"""ODCS (Open Data Contract Standard) data-contract dialect: parse (v3 + v2
spelling) + certify + emit, and the front-door wiring.

A data contract declares its identity in the schema — `primaryKey: true`
(ordered by `primaryKeyPosition`) and `unique: true`. These lock the parse of both
the v3 and legacy-v2 shapes, the composite-key ordering, the bridge to wedge A
(`certify_odcs_contract`, one entry per declared key), the crosswalk emit (wedge B),
and that `certify_semantic_model` auto-detects `kind: DataContract` as "odcs".
"""
from __future__ import annotations

from types import SimpleNamespace

from goldenmatch.semantic.certify import certify_semantic_model, detect_dialect
from goldenmatch.semantic.odcs import (
    ODCSContract,
    ODCSProperty,
    ODCSSchemaObject,
    certify_odcs_contract,
    emit_odcs_from_crosswalk,
    emit_odcs_yaml,
    odcs_identity_keys,
    parse_odcs_contract,
)

# ODCS v3: an orders contract whose composite key is (region, order_id) with an
# additional standalone unique `email`, plus a numeric `amount` measure.
_CONTRACT_DOC = {
    "apiVersion": "v3.0.0",
    "kind": "DataContract",
    "id": "abc-123",
    "version": "1.1.0",
    "name": "orders_contract",
    "status": "active",
    "schema": [
        {
            "name": "orders",
            "physicalName": "orders_v1",
            "logicalType": "object",
            "properties": [
                {"name": "order_id", "logicalType": "string",
                 "primaryKey": True, "primaryKeyPosition": 2},
                {"name": "region", "logicalType": "string",
                 "primaryKey": True, "primaryKeyPosition": 1},
                {"name": "email", "logicalType": "string", "unique": True},
                {"name": "amount", "logicalType": "number"},
            ],
        },
    ],
}


def test_parse_v3_composite_key_orders_by_position():
    contract = parse_odcs_contract(_CONTRACT_DOC)
    assert contract.kind == "DataContract"
    assert contract.name == "orders_contract"
    obj = contract.object_by_name("orders")
    assert obj.physical_name == "orders_v1"
    # primaryKeyPosition orders the composite key: region (1) before order_id (2).
    assert obj.identity_key() == ["region", "order_id"]
    assert obj.unique_keys() == ["email"]
    assert obj.numeric_measures() == ["amount"]
    assert obj.dimensions() == ["email"]


def test_parse_v2_spelling_is_accepted():
    # Legacy ODCS v2: `dataset`/`columns`/`isPrimary`/`isUnique`, no `kind`.
    v2 = {
        "apiVersion": "2.2.0",
        "datasetName": "orders",
        "dataset": [
            {"table": "orders", "columns": [
                {"column": "order_id", "logicalType": "string", "isPrimary": True},
                {"column": "email", "logicalType": "string", "isUnique": True},
            ]},
        ],
    }
    contract = parse_odcs_contract(v2)
    obj = contract.object_by_name("orders")
    assert obj.identity_key() == ["order_id"]
    assert obj.unique_keys() == ["email"]


def test_identity_keys_lists_pk_and_each_unique():
    keys = odcs_identity_keys(_CONTRACT_DOC)
    assert {"object": "orders", "key": ["region", "order_id"],
            "kind": "primary_key", "measures": ["amount"]} in keys
    assert {"object": "orders", "key": ["email"],
            "kind": "unique", "measures": ["amount"]} in keys
    assert len(keys) == 2


def test_certify_trustworthy_when_key_is_unique_at_grain():
    frames = {
        "orders": {
            "region": ["us", "us", "eu"],
            "order_id": ["1", "2", "1"],   # composite (region, order_id) is unique
            "email": ["a@x", "b@x", "c@x"],
            "amount": [10, 20, 30],
        },
    }
    reps = certify_odcs_contract(_CONTRACT_DOC, frames)
    by_kind = {r["kind"]: r for r in reps}
    assert by_kind["primary_key"]["key"] == ["region", "order_id"]
    assert by_kind["primary_key"]["certificate"].is_trustworthy()
    assert by_kind["unique"]["certificate"].is_trustworthy()


def test_certify_catches_a_broken_unique_promise():
    # email is declared unique but repeats -> the contract's promise is false.
    frames = {
        "orders": {
            "region": ["us", "us", "eu"],
            "order_id": ["1", "2", "3"],
            "email": ["dup@x", "dup@x", "c@x"],
            "amount": [10, 20, 30],
        },
    }
    reps = certify_odcs_contract(_CONTRACT_DOC, frames)
    by_kind = {r["kind"]: r for r in reps}
    assert by_kind["primary_key"]["certificate"].is_trustworthy()
    email_cert = by_kind["unique"]["certificate"]
    assert not email_cert.is_unique_at_grain
    assert email_cert.max_fan_out == 2.0


def test_certify_resolves_by_physical_name_frame():
    # A frame keyed on the object's physicalName is found too.
    frames = {"orders_v1": {"region": ["us"], "order_id": ["1"],
                            "email": ["a@x"], "amount": [10]}}
    reps = certify_odcs_contract(_CONTRACT_DOC, frames)
    assert reps and reps[0]["object"] == "orders"


def test_object_with_no_frame_is_skipped():
    reps = certify_odcs_contract(_CONTRACT_DOC, {})
    assert reps == []


def test_certify_semantic_model_auto_detects_odcs():
    assert detect_dialect(_CONTRACT_DOC) == "odcs"
    frames = {
        "orders": {
            "region": ["us", "us"],
            "order_id": ["1", "1"],   # composite key fans out
            "email": ["a@x", "b@x"],
            "amount": [10, 20],
        },
    }
    report = certify_semantic_model(_CONTRACT_DOC, frames)
    assert report.dialect == "odcs"
    assert report.n_certified == 2
    pk_entry = next(e for e in report.entries if e.context == "primary key")
    assert pk_entry.target == "orders"
    assert pk_entry.key == ["region", "order_id"]
    assert not pk_entry.certificate.is_trustworthy()
    assert any(e.context == "unique: email" for e in report.entries)


def test_detect_v2_without_kind():
    v2 = {"apiVersion": "2.2.0", "dataset": [{"table": "t", "columns": []}]}
    assert detect_dialect(v2) == "odcs"


def test_emit_round_trips_through_parser():
    contract = ODCSContract(
        api_version="v3.0.0", kind="DataContract", version="1.0.0", name="c",
        schema_objects=[ODCSSchemaObject(
            name="orders", physical_name="orders_v1",
            properties=[
                ODCSProperty(name="order_id", logical_type="string",
                             primary_key=True, primary_key_position=1),
                ODCSProperty(name="amount", logical_type="number"),
            ],
        )],
    )
    back = parse_odcs_contract(emit_odcs_yaml(contract))
    obj = back.object_by_name("orders")
    assert obj.physical_name == "orders_v1"
    assert obj.identity_key() == ["order_id"]
    assert obj.numeric_measures() == ["amount"]


def test_emit_from_crosswalk_declares_resolved_key_as_pk():
    crosswalk = SimpleNamespace(
        resolved_key="resolved_entity_id",
        n_records=100, n_entities=60, reduction_ratio=0.4,
    )
    yaml_text = emit_odcs_from_crosswalk(crosswalk, object_name="resolved_entity")
    back = parse_odcs_contract(yaml_text)
    assert back.kind == "DataContract"
    obj = back.object_by_name("resolved_entity")
    assert obj.identity_key() == ["resolved_entity_id"]
    # provenance rides in customProperties
    gm = next(c for c in back.custom_properties if c["property"] == "goldenmatch")
    assert gm["value"]["resolved_key"] == "resolved_entity_id"
    assert gm["value"]["reduction_ratio"] == 0.4


def test_semantic_field_roles_reads_odcs():
    from goldenmatch.semantic.blocking import semantic_field_roles

    roles = semantic_field_roles(_CONTRACT_DOC)
    assert "region" in roles.keys
    assert "order_id" in roles.keys
    assert "amount" in roles.measures          # numeric -> measure, never identity
    assert "email" in roles.dimensions         # descriptive -> resolve on it
    assert "amount" not in roles.dimensions
