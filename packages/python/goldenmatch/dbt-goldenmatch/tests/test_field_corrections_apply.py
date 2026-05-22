"""Tests for dbt-goldenmatch field-correction override (Phase 3 of v1.18
surface-sync roadmap).

Spec: docs/superpowers/specs/2026-05-22-phase-3-a2a-dbt-design.md

The dbt-goldenmatch package today is a thin Python wrapper around the
core run_dedupe pipeline. Phase 3 adds optional `memory_db_path` +
`dataset` params so dbt models can apply field-level corrections from
MemoryStore on top of the dedupe output.

This test exercises the override logic in isolation (no DuckDB / no
full pipeline) by patching `run_dedupe` with a stub return.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

import polars as pl
import pytest

# dbt-goldenmatch is a separate optional package not installed in the
# core pytest CI lane. Skip the whole module when it's missing rather
# than hitting ModuleNotFoundError per test.
pytest.importorskip("dbt_goldenmatch")


def _seed_field_correction_store(db_path: str, *, dataset: str) -> None:
    """Seed a MemoryStore with one field-level correction."""
    from goldenmatch.core.memory.store import Correction, MemoryStore

    store = MemoryStore(backend="sqlite", path=db_path)
    store.add_correction(Correction(
        id=str(uuid.uuid4()),
        id_a=42,        # cluster_id
        id_b=0,
        decision="field_correct",
        source="steward",
        trust=1.0,
        field_hash="",
        record_hash="",
        original_score=0.0,
        matchkey_name=None,
        reason="USPS lookup",
        dataset=dataset,
        created_at=datetime.now(),
        field_name="address1",
        original_value="1 Elm St",
        corrected_value="1 Elm Street, Apt 4B",
    ))
    store.close()


def test_dbt_dedupe_applies_field_corrections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """When memory_db_path is set, field-level corrections override the
    golden output rows whose __cluster_id__ matches."""
    from dbt_goldenmatch import materialize

    db_path = str(tmp_path / "memory.db")
    _seed_field_correction_store(db_path, dataset="customers")

    # Stub the heavy pipeline. Returns a golden output where cluster 42
    # currently holds the pre-correction address.
    golden_df = pl.DataFrame({
        "__cluster_id__": [42, 7],
        "address1": ["1 Elm St", "Different Address"],
        "name": ["Alice", "Bob"],
    })

    def _stub_run_dedupe(specs, cfg):
        return {"golden": golden_df, "stats": {"total_clusters": 2}}

    monkeypatch.setattr(materialize, "run_dedupe", _stub_run_dedupe)
    monkeypatch.setattr(
        materialize, "load_config", lambda p: object(),
    )

    # DuckDB looks up Python locals at execution time; `seed_df` IS
    # used by the CREATE TABLE statement below despite ruff's analysis.
    import duckdb
    seed_df = pl.DataFrame({"name": ["Alice", "Bob"], "address1": ["x", "y"]})  # noqa: F841 -- referenced by CREATE TABLE statement
    db_file = str(tmp_path / "duck.db")
    con = duckdb.connect(db_file)
    con.execute("CREATE TABLE customers AS SELECT * FROM seed_df")
    con.close()

    result = materialize.run_goldenmatch_dedupe(
        input_table="customers",
        config_path="ignored-by-stub",
        output_table="golden_customers",
        database=db_file,
        memory_db_path=db_path,
        dataset="customers",
    )

    assert result["applied_corrections"] == 1
    assert result["stale_corrections"] == 0
    # Verify the override landed in the output table.
    con = duckdb.connect(db_file)
    rows = con.execute(
        "SELECT __cluster_id__, address1 FROM golden_customers "
        "ORDER BY __cluster_id__",
    ).fetchall()
    con.close()
    # Cluster 7 unchanged; cluster 42 overridden.
    assert rows == [(7, "Different Address"), (42, "1 Elm Street, Apt 4B")]


def test_dbt_dedupe_without_memory_path_unchanged_behavior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """memory_db_path=None -> existing behavior; applied=0."""
    from dbt_goldenmatch import materialize

    golden_df = pl.DataFrame({
        "__cluster_id__": [42],
        "address1": ["original value"],
    })
    monkeypatch.setattr(
        materialize, "run_dedupe",
        lambda specs, cfg: {"golden": golden_df, "stats": {}},
    )
    monkeypatch.setattr(materialize, "load_config", lambda p: object())

    import duckdb
    db_file = str(tmp_path / "duck.db")
    con = duckdb.connect(db_file)
    seed_df = pl.DataFrame({"x": [1]})  # noqa: F841 -- registered into duckdb local scope
    con.execute("CREATE TABLE customers AS SELECT * FROM seed_df")
    con.close()

    result = materialize.run_goldenmatch_dedupe(
        input_table="customers",
        config_path="ignored",
        output_table="golden_customers",
        database=db_file,
    )
    assert result["applied_corrections"] == 0
    assert result["stale_corrections"] == 0


def test_dbt_dedupe_stale_correction_when_column_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Correction's field_name not in golden output -> counts as stale,
    not applied."""
    from dbt_goldenmatch import materialize

    db_path = str(tmp_path / "memory.db")
    _seed_field_correction_store(db_path, dataset="customers")
    # The seeded correction targets 'address1' but golden output has
    # only 'name'.
    golden_df = pl.DataFrame({
        "__cluster_id__": [42],
        "name": ["Alice"],
    })
    monkeypatch.setattr(
        materialize, "run_dedupe",
        lambda specs, cfg: {"golden": golden_df, "stats": {}},
    )
    monkeypatch.setattr(materialize, "load_config", lambda p: object())

    import duckdb
    db_file = str(tmp_path / "duck.db")
    con = duckdb.connect(db_file)
    seed_df = pl.DataFrame({"x": [1]})  # noqa: F841
    con.execute("CREATE TABLE customers AS SELECT * FROM seed_df")
    con.close()

    result = materialize.run_goldenmatch_dedupe(
        input_table="customers",
        config_path="ignored",
        output_table="golden_customers",
        database=db_file,
        memory_db_path=db_path,
        dataset="customers",
    )
    assert result["applied_corrections"] == 0
    assert result["stale_corrections"] == 1


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
