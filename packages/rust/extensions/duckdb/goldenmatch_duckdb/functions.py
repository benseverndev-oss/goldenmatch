"""DuckDB UDF registration for GoldenMatch functions.

Registers the same functions available in the Postgres extension:
- goldenmatch_score(a, b, scorer) -> DOUBLE
- goldenmatch_score_pair(rec_a, rec_b, config) -> DOUBLE
- goldenmatch_explain(rec_a, rec_b, config) -> VARCHAR
- goldenmatch_dedupe(rows_json, config) -> VARCHAR
- goldenmatch_dedupe_table(table_name, config) -> VARCHAR
- goldenmatch_match(target_json, ref_json, config) -> VARCHAR
- goldenmatch_match_tables(target_table, ref_table, config) -> VARCHAR
"""
from __future__ import annotations

import json
from typing import Optional

import duckdb


def register(con: duckdb.DuckDBPyConnection) -> None:
    """Register all GoldenMatch functions on a DuckDB connection.

    Args:
        con: DuckDB connection to register functions on.
    """
    # Scalar functions
    con.create_function(
        "goldenmatch_score", _score,
        ["VARCHAR", "VARCHAR", "VARCHAR"], "DOUBLE",
    )
    con.create_function(
        "goldenmatch_score_pair", _score_pair,
        ["VARCHAR", "VARCHAR", "VARCHAR"], "DOUBLE",
    )
    con.create_function(
        "goldenmatch_explain", _explain,
        ["VARCHAR", "VARCHAR", "VARCHAR"], "VARCHAR",
    )

    # JSON-based functions
    con.create_function(
        "goldenmatch_dedupe", _dedupe_json,
        ["VARCHAR", "VARCHAR"], "VARCHAR",
    )
    con.create_function(
        "goldenmatch_match", _match_json,
        ["VARCHAR", "VARCHAR", "VARCHAR"], "VARCHAR",
    )

    # Table-based functions (read from DuckDB tables)
    con.create_function(
        "goldenmatch_dedupe_table",
        lambda table_name, config: _dedupe_table(con, table_name, config),
        ["VARCHAR", "VARCHAR"], "VARCHAR",
    )
    con.create_function(
        "goldenmatch_match_tables",
        lambda target, reference, config: _match_tables(con, target, reference, config),
        ["VARCHAR", "VARCHAR", "VARCHAR"], "VARCHAR",
    )

    # Pipeline functions (job management via DuckDB tables)
    _ensure_pipeline_tables(con)
    con.create_function(
        "gm_configure",
        lambda name, config: _gm_configure(con, name, config),
        ["VARCHAR", "VARCHAR"], "VARCHAR",
    )
    con.create_function(
        "gm_run",
        lambda name, table: _gm_run(con, name, table),
        ["VARCHAR", "VARCHAR"], "VARCHAR",
    )
    con.create_function(
        "gm_jobs", lambda: _gm_jobs(con),
        [], "VARCHAR",
    )
    con.create_function(
        "gm_golden",
        lambda name: _gm_golden(con, name),
        ["VARCHAR"], "VARCHAR",
    )
    con.create_function(
        "gm_drop",
        lambda name: _gm_drop(con, name),
        ["VARCHAR"], "VARCHAR",
    )


# ── Implementation ──────────────────────────────────────────────────────


def _validate_table_name(name: str) -> str:
    """Validate table name to prevent SQL injection."""
    import re
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_.]*$', name):
        raise ValueError(f"Invalid table name: {name}")
    return name


def _score(value_a: str, value_b: str, scorer: str) -> float:
    from goldenmatch import score_strings
    return score_strings(value_a, value_b, scorer)


def _score_pair(record_a: str, record_b: str, config: str) -> float:
    from goldenmatch import score_pair_df
    rec_a = json.loads(record_a)
    rec_b = json.loads(record_b)
    cfg = json.loads(config)
    return score_pair_df(rec_a, rec_b, **cfg)


def _explain(record_a: str, record_b: str, config: str) -> str:
    from goldenmatch import explain_pair_df
    rec_a = json.loads(record_a)
    rec_b = json.loads(record_b)
    cfg = json.loads(config)
    return explain_pair_df(rec_a, rec_b, **cfg)


def _dedupe_json(rows_json: str, config_json: str) -> str:
    import polars as pl
    from goldenmatch import dedupe_df
    rows = json.loads(rows_json)
    df = pl.DataFrame(rows)
    cfg = json.loads(config_json)
    result = dedupe_df(df, **cfg)
    if result.golden is not None:
        return result.golden.write_json()
    return json.dumps(result.stats)


def _match_json(target_json: str, ref_json: str, config_json: str) -> str:
    import polars as pl
    from goldenmatch import match_df
    target = pl.DataFrame(json.loads(target_json))
    ref_df = pl.DataFrame(json.loads(ref_json))
    cfg = json.loads(config_json)
    result = match_df(target, ref_df, **cfg)
    if result.matched is not None:
        return result.matched.write_json()
    return "[]"


def _dedupe_table(con: duckdb.DuckDBPyConnection, table_name: str, config_json: str) -> str:
    import polars as pl
    from goldenmatch import dedupe_df

    _validate_table_name(table_name)

    # Use a cursor to avoid deadlock (UDF can't query the same connection)
    cursor = con.cursor()
    df = cursor.sql(f"SELECT * FROM {table_name}").pl()
    cursor.close()

    cfg = json.loads(config_json)
    result = dedupe_df(df, **cfg)
    if result.golden is not None:
        return result.golden.write_json()
    return json.dumps(result.stats)


def _match_tables(
    con: duckdb.DuckDBPyConnection,
    target_table: str,
    ref_table: str,
    config_json: str,
) -> str:
    import polars as pl
    from goldenmatch import match_df

    _validate_table_name(target_table)
    _validate_table_name(ref_table)

    cursor = con.cursor()
    target = cursor.sql(f"SELECT * FROM {target_table}").pl()
    ref_df = cursor.sql(f"SELECT * FROM {ref_table}").pl()
    cursor.close()

    cfg = json.loads(config_json)
    result = match_df(target, ref_df, **cfg)
    if result.matched is not None:
        return result.matched.write_json()
    return "[]"


# ── Pipeline functions (job management) ─────────────────────────────────


def _ensure_pipeline_tables(con: duckdb.DuckDBPyConnection) -> None:
    """Initialize pipeline state for this connection."""
    _get_state(con)  # Creates state dict if not exists


def _gm_configure(con: duckdb.DuckDBPyConnection, job_name: str, config_json: str) -> str:
    # Pipeline functions use an in-memory dict to avoid DuckDB UDF transaction isolation issues.
    # The _gm_state dict is shared across all pipeline UDF calls on this connection.
    state = _get_state(con)
    state["jobs"][job_name] = {
        "config_json": config_json,
        "status": "configured",
        "golden": None,
    }
    return f"Job '{job_name}' configured"


def _gm_run(con: duckdb.DuckDBPyConnection, job_name: str, table_name: str) -> str:
    import polars as pl
    from goldenmatch import dedupe_df

    state = _get_state(con)
    if job_name not in state["jobs"]:
        return json.dumps({"error": f"Job '{job_name}' not found"})

    job = state["jobs"][job_name]
    job["status"] = "running"

    _validate_table_name(table_name)

    # Read table via cursor (avoids UDF deadlock)
    cursor = con.cursor()
    df = cursor.sql(f"SELECT * FROM {table_name}").pl()
    cursor.close()

    cfg = json.loads(job["config_json"])
    try:
        result = dedupe_df(df, **cfg)
    except Exception as e:
        job["status"] = "failed"
        return json.dumps({"error": str(e)})

    # Store golden records in memory
    if result.golden is not None:
        job["golden"] = json.loads(result.golden.write_json())
    else:
        job["golden"] = []

    job["status"] = "completed"
    return json.dumps(result.stats)


def _gm_jobs(con: duckdb.DuckDBPyConnection) -> str:
    state = _get_state(con)
    jobs = [
        {"name": name, "status": info["status"]}
        for name, info in state["jobs"].items()
    ]
    return json.dumps(jobs)


def _gm_golden(con: duckdb.DuckDBPyConnection, job_name: str) -> str:
    state = _get_state(con)
    if job_name not in state["jobs"]:
        return "[]"
    golden = state["jobs"][job_name].get("golden", [])
    return json.dumps(golden) if golden else "[]"


def _gm_drop(con: duckdb.DuckDBPyConnection, job_name: str) -> str:
    state = _get_state(con)
    if job_name in state["jobs"]:
        del state["jobs"][job_name]
    return f"Job '{job_name}' dropped"


# Global pipeline state (shared across all UDF calls)
_pipeline_state: dict = {"jobs": {}}


def _get_state(con: duckdb.DuckDBPyConnection) -> dict:
    """Get the global pipeline state."""
    return _pipeline_state
