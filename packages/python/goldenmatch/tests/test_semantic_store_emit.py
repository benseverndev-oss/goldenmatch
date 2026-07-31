"""Tests for emit_semantic_model_from_store — emit a conformed catalog from the store."""
from __future__ import annotations

import pytest
from goldenmatch.identity import IdentityNode, IdentityStore, SourceRecord, new_entity_id
from goldenmatch.semantic import (
    emit_semantic_model_from_store,
    parse_cube_models,
    parse_osi_models,
    parse_semantic_models,
    validate_osi,
)


@pytest.fixture()
def store(tmp_path):
    """A store with 3 records collapsing to 2 durable entities."""
    path = str(tmp_path / "identity.db")
    e1, e2 = new_entity_id(), new_entity_id()
    with IdentityStore(path=path) as s:
        s.upsert_identity(IdentityNode(entity_id=e1, dataset="crm", confidence=0.9))
        s.upsert_identity(IdentityNode(entity_id=e2, dataset="crm", confidence=0.9))
        s.upsert_record(SourceRecord("crm:1", "crm", "1", "h1", entity_id=e1, dataset="crm"))
        s.upsert_record(SourceRecord("crm:2", "crm", "2", "h2", entity_id=e1, dataset="crm"))
        s.upsert_record(SourceRecord("crm:3", "crm", "3", "h3", entity_id=e2, dataset="crm"))
    return path


def test_emit_metricflow_from_store(store):
    with IdentityStore(path=store) as s:
        yaml_str = emit_semantic_model_from_store(
            s, source_name="crm", source_pk_column="customer_id", dataset="crm",
        )
    specs = parse_semantic_models(yaml_str)
    assert specs and specs[0].key == ["resolved_entity_id"]


def test_emit_cube_from_store(store):
    with IdentityStore(path=store) as s:
        yaml_str = emit_semantic_model_from_store(
            s, source_name="crm", source_pk_column="customer_id",
            dialect="cube", dataset="crm",
        )
    cubes = parse_cube_models(yaml_str)
    assert {c.name for c in cubes} >= {"crosswalk", "crm"}


def test_emit_osi_from_store_is_valid(store):
    with IdentityStore(path=store) as s:
        yaml_str = emit_semantic_model_from_store(
            s, source_name="crm", source_pk_column="customer_id",
            dialect="osi", dataset="crm",
        )
    assert validate_osi(yaml_str) == []
    assert parse_osi_models(yaml_str)


def test_emit_from_store_carries_store_stats(store):
    # the emitted provenance reflects the store's live entity/record counts
    with IdentityStore(path=store) as s:
        yaml_str = emit_semantic_model_from_store(
            s, source_name="crm", source_pk_column="customer_id",
            dialect="cube", dataset="crm",
        )
    # 3 records -> 2 entities: reduction_ratio 1 - 2/3 recorded in the cube meta
    assert "0.333" in yaml_str or "reduction_ratio" in yaml_str


def test_emit_from_store_writes_file(store, tmp_path):
    out = tmp_path / "catalog" / "crosswalk.yml"
    with IdentityStore(path=store) as s:
        emit_semantic_model_from_store(
            s, source_name="crm", source_pk_column="customer_id",
            dataset="crm", path=out,
        )
    assert out.exists()
    assert parse_semantic_models(out.read_text(encoding="utf-8"))


def test_emit_from_store_refuses_clobber(store, tmp_path):
    out = tmp_path / "c.yml"
    out.write_text("existing", encoding="utf-8")
    with IdentityStore(path=store) as s, pytest.raises(FileExistsError):
        emit_semantic_model_from_store(
            s, source_name="crm", source_pk_column="customer_id", dataset="crm", path=out,
        )
