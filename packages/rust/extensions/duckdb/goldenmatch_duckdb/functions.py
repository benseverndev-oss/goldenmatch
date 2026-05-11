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

    # AutoConfig + telemetry (v1.7-v1.12 surface)
    con.create_function(
        "goldenmatch_autoconfig",
        lambda table_name: _autoconfig_table(con, table_name),
        ["VARCHAR"], "VARCHAR",
    )
    con.create_function(
        "goldenmatch_autoconfig_telemetry",
        lambda table_name: _autoconfig_telemetry_table(con, table_name),
        ["VARCHAR"], "VARCHAR",
    )
    con.create_function(
        "goldenmatch_dedupe_full",
        lambda table_name, config_json: _dedupe_full_table(con, table_name, config_json),
        ["VARCHAR", "VARCHAR"], "VARCHAR",
    )
    con.create_function(
        "gm_telemetry",
        lambda name: _gm_telemetry(con, name),
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

    # Capture controller telemetry (v1.7-v1.12). Stays None when the caller
    # supplied an explicit config and dedupe_df bypassed auto-config.
    job["telemetry"] = _capture_telemetry(getattr(result, "config", None))

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


def _gm_telemetry(con: duckdb.DuckDBPyConnection, job_name: str) -> str:
    """Return the most-recent run's controller telemetry for a job.

    Returns the unavailable sentinel when the job hasn't run, used an
    explicit config (controller never fired), or was created on an older
    install that didn't capture telemetry.
    """
    state = _get_state(con)
    if job_name not in state["jobs"]:
        return json.dumps({"available": False, "error": f"Job '{job_name}' not found"})
    telemetry = state["jobs"][job_name].get("telemetry")
    if telemetry is None:
        return json.dumps({"available": False})
    return telemetry


# ── AutoConfig + telemetry (v1.7-v1.12) ──────────────────────────────────


def _autoconfig_table(con: duckdb.DuckDBPyConnection, table_name: str) -> str:
    """Run AutoConfigController on a DuckDB table; return committed config JSON."""
    cfg = _run_autoconfig(con, table_name)
    return json.dumps(cfg.model_dump(mode="json", exclude_none=True))


def _autoconfig_telemetry_table(
    con: duckdb.DuckDBPyConnection, table_name: str,
) -> str:
    """Run AutoConfigController and return the telemetry JSON.

    Re-runs the controller. For a single-shot autoconfig+telemetry flow,
    call ``goldenmatch_autoconfig`` once, store the result, and use the
    paired telemetry from the same call site in Python — but the SQL
    surface is two functions because DuckDB UDFs can only return scalars.
    """
    from goldenmatch.core.autoconfig import _LAST_CONTROLLER_RUN
    cfg = _run_autoconfig(con, table_name)
    state = _LAST_CONTROLLER_RUN.get()
    if state is None:
        return json.dumps({"available": False, "source": "autoconfig"})
    profile, history = state
    return _serialize_telemetry(profile, history, cfg, source="autoconfig")


def _dedupe_full_table(
    con: duckdb.DuckDBPyConnection, table_name: str, config_json: str,
) -> str:
    """Deduplicate using a full GoldenMatchConfig JSON (supports NE / Path Y)."""
    from goldenmatch import dedupe_df
    from goldenmatch.config.schemas import GoldenMatchConfig

    _validate_table_name(table_name)
    cursor = con.cursor()
    df = cursor.sql(f"SELECT * FROM {table_name}").pl()
    cursor.close()

    cfg = GoldenMatchConfig.model_validate_json(config_json)
    result = dedupe_df(df, config=cfg)
    if result.golden is not None:
        return result.golden.write_json()
    return json.dumps(result.stats)


def _run_autoconfig(con: duckdb.DuckDBPyConnection, table_name: str):
    """Shared helper: read table, run auto_configure_df, return the config."""
    from goldenmatch.core.autoconfig import auto_configure_df
    _validate_table_name(table_name)
    cursor = con.cursor()
    df = cursor.sql(f"SELECT * FROM {table_name}").pl()
    cursor.close()
    return auto_configure_df(df)


def _capture_telemetry(committed_config) -> Optional[str]:
    """Read the AutoConfigController ContextVar and serialise to JSON.

    Returns ``None`` when the controller didn't run on the current thread
    (the typical signal that the caller passed an explicit config).
    """
    try:
        from goldenmatch.core.autoconfig import _LAST_CONTROLLER_RUN
        state = _LAST_CONTROLLER_RUN.get()
    except Exception:
        return None
    if state is None:
        return None
    profile, history = state
    return _serialize_telemetry(profile, history, committed_config, source="gm_run")


def _serialize_telemetry(profile, history, committed_config, *, source: str) -> str:
    """Delegate to ``goldenmatch.web.controller_telemetry`` when available.

    Falls back to a minimal hand-rolled JSON when the ``[web]`` extra isn't
    installed so the SQL contract still resolves to something parseable.
    """
    try:
        from goldenmatch.web.controller_telemetry import serialize_telemetry
        return json.dumps(serialize_telemetry(
            profile=profile,
            history=history,
            committed_config=committed_config,
            source=source,
            run_name=None,
            recorded_at=None,
        ))
    except Exception:
        # Minimal fallback — surface at least stop_reason + health.
        out: dict = {"available": profile is not None or history is not None, "source": source}
        try:
            if history is not None and getattr(history, "stop_reason", None) is not None:
                out["stop_reason"] = history.stop_reason.value
        except Exception:
            pass
        try:
            if profile is not None:
                out["health"] = profile.health().value
        except Exception:
            pass
        return json.dumps(out)


# Global pipeline state (shared across all UDF calls)
_pipeline_state: dict = {"jobs": {}}


def _get_state(con: duckdb.DuckDBPyConnection) -> dict:
    """Get the global pipeline state."""
    return _pipeline_state
