"""dbt-goldensuite field-correction application (#437 Phase 3 surface sync).

Wraps Learning Memory's field-level Corrections + applies them on
top of a goldenmatch dedupe output table. Used by the
``apply_field_corrections`` macro.

Usage in dbt models::

    {{ config(materialized='table') }}
    {{ apply_field_corrections(
        golden_table=ref('goldenmatch_dedupe_output'),
        memory_db_path='.goldenmatch/memory.db',
        dataset='my_run',
        cluster_id_column='__cluster_id__',
    ) }}

The macro is implemented in Python rather than pure Jinja SQL because
Learning Memory is a SQLite file (not a dbt source), and we need to
attach + query it to LEFT JOIN against the golden output.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb


def apply_field_corrections(
    duckdb_conn: duckdb.DuckDBPyConnection,
    golden_table: str,
    memory_db_path: str,
    dataset: str,
    output_table: str,
    cluster_id_column: str = "__cluster_id__",
) -> dict[str, Any]:
    """Materialize a golden table with field-level corrections applied.

    Reads ``decision='field_correct'`` rows from the SQLite Learning
    Memory store at ``memory_db_path`` (filtered to ``dataset``). For
    each correction, overrides the golden record's ``field_name``
    column with ``corrected_value`` at the row identified by
    ``cluster_id_column``.

    Args:
        duckdb_conn: open DuckDB connection
        golden_table: source table name (the dedupe output)
        memory_db_path: SQLite file path to Learning Memory
        dataset: dataset filter for corrections
        output_table: destination table name (CREATE OR REPLACE)
        cluster_id_column: column linking golden rows to cluster_id;
            defaults to the v1.18.x internal ``__cluster_id__``

    Returns:
        Summary dict: corrections_applied (int), unanchorable (int),
        output_table (str), corrected_fields (list[str])
    """
    if not Path(memory_db_path).exists():
        # MemoryStore-disabled run -- macro is a no-op; pass-through.
        duckdb_conn.execute(
            f"CREATE OR REPLACE TABLE {output_table} AS "
            f"SELECT * FROM {golden_table}"
        )
        return {
            "corrections_applied": 0,
            "unanchorable": 0,
            "output_table": output_table,
            "corrected_fields": [],
            "memory_store_present": False,
        }

    # Load field-level corrections via the Python MemoryStore (handles the
    # schema migration + decoding consistently with the main package).
    from goldenmatch.core.memory.store import MemoryStore

    store = MemoryStore(backend="sqlite", path=memory_db_path)
    try:
        all_corrections = store.get_corrections(dataset=dataset)
    finally:
        store.close()
    field_corrections = [
        c for c in all_corrections
        if c.decision == "field_correct"
        and c.field_name
        and c.corrected_value is not None
    ]

    if not field_corrections:
        duckdb_conn.execute(
            f"CREATE OR REPLACE TABLE {output_table} AS "
            f"SELECT * FROM {golden_table}"
        )
        return {
            "corrections_applied": 0,
            "unanchorable": 0,
            "output_table": output_table,
            "corrected_fields": [],
            "memory_store_present": True,
        }

    # Identify golden table column names so we can build the SELECT.
    cols_row = duckdb_conn.execute(
        f"SELECT * FROM {golden_table} LIMIT 0"
    ).fetchdf().columns.tolist()
    if cluster_id_column not in cols_row:
        raise ValueError(
            f"cluster_id_column {cluster_id_column!r} not in {golden_table} "
            f"(found: {cols_row})"
        )

    # Group corrections by (cluster_id, field_name) -- last-write-wins.
    # Memory store's add_correction upserts on trust + recency so we
    # already have the canonical value per (id, dataset).
    overrides: dict[tuple[int, str], str] = {}
    corrected_fields: set[str] = set()
    unanchorable = 0
    valid_cluster_ids = {
        row[0] for row in duckdb_conn.execute(
            f"SELECT DISTINCT {cluster_id_column} FROM {golden_table}"
        ).fetchall()
    }
    for c in field_corrections:
        if c.id_a not in valid_cluster_ids:
            unanchorable += 1
            continue
        if c.field_name not in cols_row:
            # Field correction references a column that doesn't exist in
            # the golden output (schema drift). Skip + count as unanchorable.
            unanchorable += 1
            continue
        overrides[(c.id_a, c.field_name)] = c.corrected_value or ""
        corrected_fields.add(c.field_name)

    # Build the CASE expressions per corrected field.
    case_clauses = []
    for col in cols_row:
        if col not in corrected_fields:
            case_clauses.append(f"{col}")
            continue
        whens = []
        for (cid, field), value in overrides.items():
            if field == col:
                escaped = value.replace("'", "''")
                whens.append(
                    f"WHEN {cluster_id_column} = {cid} THEN '{escaped}'"
                )
        if whens:
            case_clauses.append(
                f"CASE {' '.join(whens)} ELSE {col} END AS {col}"
            )
        else:
            case_clauses.append(col)

    duckdb_conn.execute(
        f"CREATE OR REPLACE TABLE {output_table} AS "
        f"SELECT {', '.join(case_clauses)} FROM {golden_table}"
    )
    return {
        "corrections_applied": len(overrides),
        "unanchorable": unanchorable,
        "output_table": output_table,
        "corrected_fields": sorted(corrected_fields),
        "memory_store_present": True,
    }
