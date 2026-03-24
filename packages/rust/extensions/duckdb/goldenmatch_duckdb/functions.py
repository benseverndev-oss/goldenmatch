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
    # These capture the connection for SPI-style table reading
    con.create_function(
        "goldenmatch_dedupe_table",
        lambda table_name, config: _dedupe_table(con, table_name, config),
        ["VARCHAR", "VARCHAR"], "VARCHAR",
    )
    con.create_function(
        "goldenmatch_match_tables",
        lambda target, ref, config: _match_tables(con, target, ref, config),
        ["VARCHAR", "VARCHAR", "VARCHAR"], "VARCHAR",
    )


# ── Implementation ──────────────────────────────────────────────────────


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
    ref = pl.DataFrame(json.loads(ref_json))
    cfg = json.loads(config_json)
    result = match_df(target, ref, **cfg)
    if result.matched is not None:
        return result.matched.write_json()
    return "[]"


def _dedupe_table(con: duckdb.DuckDBPyConnection, table_name: str, config_json: str) -> str:
    import polars as pl
    from goldenmatch import dedupe_df

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

    cursor = con.cursor()
    target = cursor.sql(f"SELECT * FROM {target_table}").pl()
    ref = cursor.sql(f"SELECT * FROM {ref_table}").pl()
    cursor.close()

    cfg = json.loads(config_json)
    result = match_df(target, ref, **cfg)
    if result.matched is not None:
        return result.matched.write_json()
    return "[]"
