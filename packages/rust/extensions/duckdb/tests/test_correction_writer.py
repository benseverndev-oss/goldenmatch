"""Tests for DuckDB correction CRUD UDFs (Phase 6B of #437 surface sync).

Spec: docs/superpowers/specs/2026-05-22-phase-6-sql-extensions-correction-crud-design.md

Exercises:
- goldenmatch_correction_add pair-level + field-level shapes
- goldenmatch_correction_list filter by dataset
- Validation errors (missing fields per shape)
- Round-trip via Python MemoryStore on the same SQLite file
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

# Phase 6B UDFs depend on the goldenmatch Python package being installed.
pytest.importorskip("goldenmatch")

from goldenmatch_duckdb.functions import register


@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    register(c)
    try:
        yield c
    finally:
        c.close()


def test_correction_add_pair_level(con, tmp_path: Path):
    memory_path = str(tmp_path / "memory.db")
    args = json.dumps({"id_a": 42, "id_b": 99})
    sql = (
        "SELECT goldenmatch_correction_add(?, ?, ?, ?)"
    )
    result_json = con.execute(sql, ["approve", "customers", memory_path, args]).fetchone()[0]
    result = json.loads(result_json)
    assert result["status"] == "ok"
    assert result["id_a"] == 42
    assert result["id_b"] == 99
    assert result["decision"] == "approve"
    assert result["source"] == "duckdb"
    assert result["trust"] == 0.7

    # Round-trip via Python MemoryStore.
    from goldenmatch.core.memory.store import MemoryStore
    store = MemoryStore(backend="sqlite", path=memory_path)
    rows = list(store.get_corrections(dataset="customers"))
    store.close()
    assert len(rows) == 1
    assert rows[0].id_a == 42
    assert rows[0].decision == "approve"


def test_correction_add_field_level(con, tmp_path: Path):
    memory_path = str(tmp_path / "memory.db")
    args = json.dumps({
        "cluster_id": 42,
        "field_name": "address1",
        "original_value": "1 Elm St",
        "corrected_value": "1 Elm Street, Apt 4B",
    })
    sql = "SELECT goldenmatch_correction_add(?, ?, ?, ?)"
    result_json = con.execute(
        sql, ["field_correct", "customers", memory_path, args],
    ).fetchone()[0]
    result = json.loads(result_json)
    assert result["status"] == "ok"
    assert result["cluster_id"] == 42
    assert result["field_name"] == "address1"
    assert result["decision"] == "field_correct"

    from goldenmatch.core.memory.store import MemoryStore
    store = MemoryStore(backend="sqlite", path=memory_path)
    rows = list(store.get_corrections(dataset="customers"))
    store.close()
    assert len(rows) == 1
    assert rows[0].decision == "field_correct"
    assert rows[0].field_name == "address1"
    assert rows[0].corrected_value == "1 Elm Street, Apt 4B"


def test_correction_add_field_correct_missing_field_name(con, tmp_path: Path):
    memory_path = str(tmp_path / "memory.db")
    args = json.dumps({"corrected_value": "X"})  # no field_name
    sql = "SELECT goldenmatch_correction_add(?, ?, ?, ?)"
    result_json = con.execute(
        sql, ["field_correct", "customers", memory_path, args],
    ).fetchone()[0]
    result = json.loads(result_json)
    assert "error" in result
    assert "field_name" in result["error"]


def test_correction_add_invalid_decision(con, tmp_path: Path):
    memory_path = str(tmp_path / "memory.db")
    sql = "SELECT goldenmatch_correction_add(?, ?, ?, ?)"
    result_json = con.execute(
        sql, ["unknown", "customers", memory_path, "{}"],
    ).fetchone()[0]
    result = json.loads(result_json)
    assert "error" in result
    assert "Invalid decision" in result["error"]


def test_correction_add_missing_dataset(con, tmp_path: Path):
    memory_path = str(tmp_path / "memory.db")
    sql = "SELECT goldenmatch_correction_add(?, ?, ?, ?)"
    args = json.dumps({"id_a": 1, "id_b": 2})
    result_json = con.execute(
        sql, ["approve", "", memory_path, args],
    ).fetchone()[0]
    result = json.loads(result_json)
    assert "error" in result
    assert "dataset" in result["error"]


def test_correction_list_round_trip(con, tmp_path: Path):
    memory_path = str(tmp_path / "memory.db")
    # Add 2 corrections.
    con.execute(
        "SELECT goldenmatch_correction_add(?, ?, ?, ?)",
        ["approve", "ds-a", memory_path, json.dumps({"id_a": 1, "id_b": 2})],
    ).fetchone()
    con.execute(
        "SELECT goldenmatch_correction_add(?, ?, ?, ?)",
        ["approve", "ds-b", memory_path, json.dumps({"id_a": 3, "id_b": 4})],
    ).fetchone()

    # List by dataset filter.
    list_json_a = con.execute(
        "SELECT goldenmatch_correction_list(?, ?)",
        ["ds-a", memory_path],
    ).fetchone()[0]
    rows_a = json.loads(list_json_a)
    assert len(rows_a) == 1
    assert rows_a[0]["dataset"] == "ds-a"

    # List all (empty dataset filter).
    list_json_all = con.execute(
        "SELECT goldenmatch_correction_list(?, ?)",
        ["", memory_path],
    ).fetchone()[0]
    rows_all = json.loads(list_json_all)
    assert len(rows_all) == 2


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
