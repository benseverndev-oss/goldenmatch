"""Tests for Phase 3 of v1.19.x surface-sync roadmap.

Spec: docs/superpowers/specs/2026-05-22-phase-3-a2a-dbt-design.md

Covers:
- 3.1 A2A add_correction skill (pair + field shapes; validation)
- 3.2 dbt-goldenmatch apply_field_corrections (overrides golden values
      from MemoryStore field-level corrections)
"""
from __future__ import annotations

from pathlib import Path

import pytest

# Skip the whole module up-front when optional extras aren't installed:
# - aiohttp -- gated by goldenmatch[agent] extra
# - dbt_goldenmatch -- subpackage not installed in main CI lane
try:
    import aiohttp  # noqa: F401
    import dbt_goldenmatch  # noqa: F401
except ImportError:
    pytest.skip(
        "aiohttp or dbt_goldenmatch not installed",
        allow_module_level=True,
    )

# ---------------------------------------------------------------------------
# 3.1 A2A add_correction skill
# ---------------------------------------------------------------------------


def test_a2a_skill_card_lists_add_correction():
    from goldenmatch.a2a.server import build_agent_card

    card = build_agent_card("http://localhost:8080")
    skill_ids = {s["id"] for s in card["skills"]}
    assert "add_correction" in skill_ids


def test_a2a_add_correction_skill_pair_level(tmp_path: Path):
    from goldenmatch.a2a.skills import dispatch_skill
    from goldenmatch.core.memory.store import MemoryStore

    db_path = str(tmp_path / "m.db")
    result = dispatch_skill(
        "add_correction",
        {
            "decision": "approve",
            "id_a": 42,
            "id_b": 99,
            "dataset": "test_dataset",
            "path": db_path,
        },
    )
    assert result.get("status") == "ok"
    assert result["id_a"] == 42
    assert result["id_b"] == 99
    assert result["decision"] == "approve"
    assert result["source"] == "agent"
    assert result["trust"] == 0.5

    store = MemoryStore(backend="sqlite", path=db_path)
    rows = list(store.get_corrections(dataset="test_dataset"))
    store.close()
    assert len(rows) == 1
    assert rows[0].decision == "approve"


def test_a2a_add_correction_skill_field_level(tmp_path: Path):
    from goldenmatch.a2a.skills import dispatch_skill
    from goldenmatch.core.memory.store import MemoryStore

    db_path = str(tmp_path / "m.db")
    result = dispatch_skill(
        "add_correction",
        {
            "decision": "field_correct",
            "cluster_id": 42,
            "field_name": "address1",
            "corrected_value": "1 Elm Street, Apt 4B",
            "original_value": "1 Elm St",
            "dataset": "test_dataset",
            "path": db_path,
        },
    )
    assert result.get("status") == "ok"
    assert result["cluster_id"] == 42
    assert result["field_name"] == "address1"
    assert result["corrected_value"] == "1 Elm Street, Apt 4B"

    store = MemoryStore(backend="sqlite", path=db_path)
    rows = list(store.get_corrections(dataset="test_dataset"))
    store.close()
    assert len(rows) == 1
    assert rows[0].decision == "field_correct"
    assert rows[0].field_name == "address1"


def test_a2a_add_correction_field_missing_field_name(tmp_path: Path):
    from goldenmatch.a2a.skills import dispatch_skill

    result = dispatch_skill(
        "add_correction",
        {
            "decision": "field_correct",
            "corrected_value": "X",
            "dataset": "test",
            "path": str(tmp_path / "m.db"),
        },
    )
    assert "error" in result
    assert "field_name" in result["error"]


def test_a2a_add_correction_invalid_decision(tmp_path: Path):
    from goldenmatch.a2a.skills import dispatch_skill

    result = dispatch_skill(
        "add_correction",
        {"decision": "shrug", "dataset": "test", "path": str(tmp_path / "m.db")},
    )
    assert "error" in result


# ---------------------------------------------------------------------------
# 3.2 dbt-goldenmatch apply_field_corrections
# ---------------------------------------------------------------------------


def test_dbt_apply_field_corrections_no_memory_store(tmp_path: Path):
    """When memory_db_path doesn't exist, macro is a no-op pass-through."""
    import duckdb
    from dbt_goldenmatch.corrections import apply_field_corrections

    conn = duckdb.connect(":memory:")
    conn.execute(
        "CREATE TABLE golden AS SELECT 1 AS __cluster_id__, 'orig' AS name"
    )
    summary = apply_field_corrections(
        duckdb_conn=conn,
        golden_table="golden",
        memory_db_path=str(tmp_path / "nonexistent.db"),
        dataset="test",
        output_table="golden_corrected",
    )
    assert summary["memory_store_present"] is False
    assert summary["corrections_applied"] == 0
    rows = conn.execute("SELECT * FROM golden_corrected").fetchall()
    assert rows == [(1, "orig")]


def test_dbt_apply_field_corrections_overrides_field(tmp_path: Path):
    import duckdb
    from dbt_goldenmatch.corrections import apply_field_corrections
    from goldenmatch.core.memory.store import Correction, MemoryStore

    # Seed a field-level correction in MemoryStore.
    db_path = str(tmp_path / "m.db")
    store = MemoryStore(backend="sqlite", path=db_path)
    store.add_correction(Correction(
        id="test-1",
        id_a=42, id_b=0,
        decision="field_correct",
        source="steward", trust=1.0,
        field_hash="", record_hash="",
        original_score=0.0,
        dataset="my_run",
        field_name="address1",
        original_value="1 Elm St",
        corrected_value="1 Elm Street, Apt 4B",
    ))
    store.close()

    conn = duckdb.connect(":memory:")
    conn.execute(
        "CREATE TABLE golden AS SELECT * FROM (VALUES "
        "(42, 'Alice', '1 Elm St'), "
        "(99, 'Bob', '5 Oak Rd')) "
        "AS t(__cluster_id__, name, address1)"
    )
    summary = apply_field_corrections(
        duckdb_conn=conn,
        golden_table="golden",
        memory_db_path=db_path,
        dataset="my_run",
        output_table="golden_corrected",
    )
    assert summary["corrections_applied"] == 1
    assert summary["unanchorable"] == 0
    assert summary["corrected_fields"] == ["address1"]

    rows = sorted(
        conn.execute("SELECT * FROM golden_corrected").fetchall()
    )
    # Cluster 42's address1 was overridden; cluster 99 unchanged.
    by_cid = {row[0]: row for row in rows}
    assert by_cid[42] == (42, "Alice", "1 Elm Street, Apt 4B")
    assert by_cid[99] == (99, "Bob", "5 Oak Rd")


def test_dbt_apply_field_corrections_unanchorable_cluster_id(tmp_path: Path):
    """Correction with cluster_id not in the golden table -> counted as
    unanchorable, doesn't fail the run."""
    import duckdb
    from dbt_goldenmatch.corrections import apply_field_corrections
    from goldenmatch.core.memory.store import Correction, MemoryStore

    db_path = str(tmp_path / "m.db")
    store = MemoryStore(backend="sqlite", path=db_path)
    store.add_correction(Correction(
        id="orphan",
        id_a=9999, id_b=0,
        decision="field_correct",
        source="steward", trust=1.0,
        field_hash="", record_hash="",
        original_score=0.0,
        dataset="run",
        field_name="address1",
        corrected_value="X",
    ))
    store.close()
    conn = duckdb.connect(":memory:")
    conn.execute(
        "CREATE TABLE golden AS SELECT 42 AS __cluster_id__, 'a' AS address1"
    )
    summary = apply_field_corrections(
        duckdb_conn=conn,
        golden_table="golden",
        memory_db_path=db_path,
        dataset="run",
        output_table="out",
    )
    assert summary["corrections_applied"] == 0
    assert summary["unanchorable"] == 1


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
