"""Shared SQL plumbing for the Snowflake-backed IdentityStore and MemoryStore.

Snowflake differs from the SQLite/Postgres paths in three ways that every
caller here has to respect, so they are handled once, in this module:

1. Only ``NOT NULL`` is enforced. ``PRIMARY KEY`` / ``UNIQUE`` / ``FOREIGN KEY``
   are metadata, so idempotency must come from an explicit ``MERGE``.
2. Unquoted identifiers are uppercased, so a ``DictCursor`` yields
   ``{'ENTITY_ID': ...}`` while every ``_row_to_*`` helper in the stores indexes
   ``row["entity_id"]``. ``CaseInsensitiveRow`` reconciles that.
3. There is no ``?`` placeholder; the connector default is ``pyformat``.
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Iterator, Mapping, Sequence
from typing import Any


class CaseInsensitiveRow(Mapping[str, Any]):
    """One result row, indexable by either case.

    Snowflake returns ``ENTITY_ID`` for an unquoted ``entity_id`` column. The
    store row-mappers are shared with SQLite/Postgres and index lowercase, so
    every read goes through this rather than quoting identifiers in the DDL --
    quoted identifiers would force anyone hand-querying the tables to type
    ``SELECT "entity_id"``.
    """

    __slots__ = ("_data",)

    def __init__(self, data: Mapping[str, Any]) -> None:
        self._data = {k.lower(): v for k, v in data.items()}

    def __getitem__(self, key: str) -> Any:
        return self._data[key.lower()]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return f"CaseInsensitiveRow({self._data!r})"


def resolve_connection(connection: Any, *, database: str, schema: str) -> Any:
    """Return a live ``SnowflakeConnection`` from any of the accepted shapes.

    Accepts, in order of preference:

    - a Snowpark ``Session`` (anything exposing ``.connection``), which is what
      a stored procedure is handed for free;
    - a live ``SnowflakeConnection`` (has ``.cursor``), returned as-is;
    - a dict of ``snowflake.connector.connect`` kwargs;
    - an account-name string, with the rest read from ``SNOWFLAKE_*`` env vars,
      matching ``db/connector_snowflake.py``.
    """
    if connection is None:
        raise ValueError(
            "snowflake backend requires connection= (a Snowpark Session, a "
            "SnowflakeConnection, a kwargs dict, or an account name)"
        )
    # Snowpark Session -- check before .cursor so a Session that also proxies
    # cursor() still unwraps to the real connection.
    inner = getattr(connection, "connection", None)
    if inner is not None and hasattr(inner, "cursor"):
        return inner
    if hasattr(connection, "cursor"):
        return connection

    try:
        import snowflake.connector  # noqa: PLC0415
    except ImportError as e:
        raise ImportError(
            "snowflake backend requires snowflake-connector-python: "
            "pip install 'goldenmatch[snowflake]'"
        ) from e

    if isinstance(connection, dict):
        kwargs = dict(connection)
    elif isinstance(connection, str):
        kwargs = {
            "account": connection,
            "user": os.environ.get("SNOWFLAKE_USER", ""),
            "password": os.environ.get("SNOWFLAKE_PASSWORD", ""),
            "warehouse": os.environ.get("SNOWFLAKE_WAREHOUSE", ""),
            "role": os.environ.get("SNOWFLAKE_ROLE", ""),
        }
        kwargs = {k: v for k, v in kwargs.items() if v}
    else:
        raise TypeError(
            f"unsupported connection type for snowflake backend: "
            f"{type(connection).__name__}"
        )
    kwargs.setdefault("database", database)
    kwargs.setdefault("schema", schema)
    return snowflake.connector.connect(**kwargs)


def execute(conn: Any, sql: str, params: tuple = ()) -> None:
    """Run a statement, discarding any result."""
    with conn.cursor() as cur:
        cur.execute(sql, params)


def fetchone_row(
    conn: Any, sql: str, params: tuple = ()
) -> CaseInsensitiveRow | None:
    from snowflake.connector import DictCursor  # noqa: PLC0415

    with conn.cursor(DictCursor) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return CaseInsensitiveRow(row) if row else None


def fetchall_rows(
    conn: Any, sql: str, params: tuple = ()
) -> list[CaseInsensitiveRow]:
    from snowflake.connector import DictCursor  # noqa: PLC0415

    with conn.cursor(DictCursor) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [CaseInsensitiveRow(r) for r in rows]


_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS _gm_schema_version (
    component STRING NOT NULL,
    version   NUMBER NOT NULL
);
"""

# Translated from ``_SCHEMA`` in ``goldenmatch/identity/store.py`` (SCHEMA_VERSION
# = 7). Column names, nullability and ordering are identical to the SQLite
# source; only types change per the mapping in the task-2 brief (TEXT -> STRING,
# REAL -> FLOAT, INTEGER PRIMARY KEY AUTOINCREMENT -> NUMBER AUTOINCREMENT START
# 1 INCREMENT 1, TIMESTAMP DEFAULT CURRENT_TIMESTAMP -> TIMESTAMP_NTZ DEFAULT
# CURRENT_TIMESTAMP(), JSON-holding TEXT columns -> VARIANT). CREATE INDEX
# statements are omitted -- Snowflake has no secondary indexes. PRIMARY KEY /
# UNIQUE / FOREIGN KEY are kept for documentation value only; Snowflake does not
# enforce them, so no code may rely on them for deduplication.
IDENTITY_DDL = """
CREATE TABLE IF NOT EXISTS identity_nodes (
    entity_id      STRING PRIMARY KEY,
    status         STRING NOT NULL DEFAULT 'active',
    merged_into    STRING,
    golden_record  VARIANT,
    confidence     FLOAT,
    dataset        STRING,
    created_at     TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    updated_at     TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP()
);

CREATE TABLE IF NOT EXISTS source_records (
    record_id      STRING PRIMARY KEY,
    source         STRING NOT NULL,
    source_pk      STRING NOT NULL,
    record_hash    STRING NOT NULL,
    entity_id      STRING,
    payload        VARIANT,
    dataset        STRING,
    first_seen_at  TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    last_seen_at   TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    FOREIGN KEY (entity_id) REFERENCES identity_nodes(entity_id)
);

CREATE TABLE IF NOT EXISTS evidence_edges (
    edge_id              NUMBER AUTOINCREMENT START 1 INCREMENT 1,
    entity_id            STRING NOT NULL,
    record_a_id          STRING NOT NULL,
    record_b_id          STRING NOT NULL,
    kind                 STRING NOT NULL DEFAULT 'same_as',
    score                FLOAT,
    matchkey_name        STRING,
    field_scores         VARIANT,
    negative_evidence    VARIANT,
    controller_snapshot  VARIANT,
    run_name             STRING,
    dataset              STRING,
    actor                STRING,
    trust                FLOAT,
    recorded_at          TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    UNIQUE(entity_id, record_a_id, record_b_id, kind, run_name)
);

CREATE TABLE IF NOT EXISTS identity_events (
    event_id          NUMBER AUTOINCREMENT START 1 INCREMENT 1,
    entity_id         STRING NOT NULL,
    kind              STRING NOT NULL,
    payload           VARIANT,
    run_name          STRING,
    dataset           STRING,
    actor             STRING,
    trust             FLOAT,
    claim_type        STRING,
    evidence_ref      STRING,
    previous_claim_id NUMBER,
    entry_hash        STRING,
    recorded_at       TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP()
);

CREATE TABLE IF NOT EXISTS audit_seals (
    seal_id       NUMBER AUTOINCREMENT START 1 INCREMENT 1,
    dataset       STRING,
    root_hash     STRING NOT NULL,
    event_count   NUMBER NOT NULL,
    last_event_id NUMBER,
    prev_seal_id  NUMBER,
    prev_root     STRING,
    actor         STRING,
    created_at    TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP()
);

CREATE TABLE IF NOT EXISTS identity_aliases (
    alias        STRING NOT NULL,
    entity_id    STRING NOT NULL,
    kind         STRING NOT NULL DEFAULT 'external_id',
    dataset      STRING,
    recorded_at  TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    -- UNIQUE, not PRIMARY KEY: ``dataset`` is nullable, and real Snowflake's
    -- PK is metadata-only either way -- but fakesnow's duckdb translation
    -- (the test oracle for this module) enforces an implicit NOT NULL on
    -- PRIMARY KEY columns per the SQL standard, which duckdb does NOT do for
    -- UNIQUE. A PRIMARY KEY here would reject every alias with no dataset.
    UNIQUE (alias, kind, dataset)
);

CREATE TABLE IF NOT EXISTS identity_record_block_keys (
    record_id  STRING NOT NULL,
    entity_id  STRING,
    block_key  STRING NOT NULL,
    pass_sig   STRING NOT NULL DEFAULT '',
    PRIMARY KEY (record_id, pass_sig, block_key)
);

CREATE TABLE IF NOT EXISTS identity_relationships (
    entity_a_id   STRING NOT NULL,
    entity_b_id   STRING NOT NULL,
    kind          STRING NOT NULL,
    field         STRING NOT NULL,
    shared_value  STRING,
    dataset       STRING,
    recorded_at   TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    PRIMARY KEY (entity_a_id, entity_b_id, kind, shared_value)
);

CREATE TABLE IF NOT EXISTS identity_runs (
    run_name       STRING PRIMARY KEY,
    config_id      STRING,
    schema_version NUMBER,
    config_json    STRING,
    dataset        STRING,
    created_at     TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP()
);
"""


def ensure_schema(
    conn: Any,
    ddl: str,
    *,
    database: str,
    schema: str,
    version: int,
    component: str = "identity",
) -> None:
    """Create the schema and tables if absent, and stamp the version.

    Idempotent: every statement is ``IF NOT EXISTS`` and the version marker is
    written with a MERGE, so re-opening a store against a populated warehouse
    neither drops rows nor duplicates the marker. ``component`` namespaces the
    version marker row so a second store (e.g. a future Snowflake MemoryStore)
    calling this with its own ``ddl`` does not collide on the same
    ``component='identity'`` row as this store.
    """
    execute(conn, f"CREATE SCHEMA IF NOT EXISTS {database}.{schema}")
    execute(conn, f"USE SCHEMA {database}.{schema}")
    for stmt in _split_statements(ddl):
        execute(conn, stmt)
    for stmt in _split_statements(_VERSION_TABLE):
        execute(conn, stmt)
    execute(
        conn,
        "MERGE INTO _gm_schema_version t "
        "USING (SELECT %s AS component, %s AS version) s "
        "ON t.component = s.component "
        "WHEN MATCHED THEN UPDATE SET t.version = s.version "
        "WHEN NOT MATCHED THEN INSERT (component, version) "
        "VALUES (s.component, s.version)",
        (component, version),
    )


def schema_version(conn: Any, component: str = "identity") -> int | None:
    row = fetchone_row(
        conn,
        "SELECT version FROM _gm_schema_version WHERE component = %s",
        (component,),
    )
    return int(row["version"]) if row else None


def _split_statements(ddl: str) -> list[str]:
    """Split a DDL blob into statements.

    The Snowflake connector executes one statement per call by default, unlike
    ``sqlite3.executescript``. Comment-only fragments are dropped.
    """
    out = []
    for chunk in ddl.split(";"):
        lines = [
            ln for ln in chunk.splitlines()
            if ln.strip() and not ln.strip().startswith("--")
        ]
        stmt = "\n".join(lines).strip()
        if stmt:
            out.append(stmt)
    return out


def _value_sql(col: str, json_cols: Sequence[str]) -> str:
    """Placeholder for one column, wrapping VARIANT columns in PARSE_JSON."""
    return "PARSE_JSON(%s)" if col in json_cols else "%s"


def merge_one(
    conn: Any,
    table: str,
    key_cols: Sequence[str],
    row: Mapping[str, Any],
    *,
    update_cols: Sequence[str] | None = None,
    json_cols: Sequence[str] = (),
) -> None:
    """Upsert (or insert-if-absent) a single row via MERGE.

    ``update_cols=None`` omits the ``WHEN MATCHED`` branch entirely, which is
    the Snowflake replacement for ``INSERT OR IGNORE`` -- necessary because a
    UNIQUE constraint here is metadata and would not stop the duplicate.

    The ``ON`` clause compares key columns with ``IS NOT DISTINCT FROM``, not
    ``=``. Several key columns are nullable (``evidence_edges.run_name``,
    ``identity_aliases.dataset``): plain ``=`` evaluates to ``NULL`` (not
    ``TRUE``) when both sides are ``NULL``, so ``WHEN MATCHED`` would never
    fire for a replayed row whose key column is unset, silently duplicating
    it -- the same trap this MERGE exists to close, one level down.
    """
    cols = list(row.keys())
    src = ", ".join(
        f"{_value_sql(c, json_cols)} AS {c}" for c in cols
    )
    on = " AND ".join(f"t.{c} IS NOT DISTINCT FROM s.{c}" for c in key_cols)
    sql = [
        f"MERGE INTO {table} t USING (SELECT {src}) s ON {on}",
    ]
    if update_cols:
        sets = ", ".join(f"t.{c} = s.{c}" for c in update_cols)
        sql.append(f"WHEN MATCHED THEN UPDATE SET {sets}")
    insert_cols = ", ".join(cols)
    insert_vals = ", ".join(f"s.{c}" for c in cols)
    sql.append(
        f"WHEN NOT MATCHED THEN INSERT ({insert_cols}) VALUES ({insert_vals})"
    )
    execute(conn, "\n".join(sql), tuple(row[c] for c in cols))


def stage_and_merge(
    conn: Any,
    target: str,
    rows: list[Mapping[str, Any]],
    key_cols: Sequence[str],
    *,
    update_cols: Sequence[str] | None = None,
    json_cols: Sequence[str] = (),
    database: str,
    schema: str,
) -> int:
    """Bulk upsert: write_pandas into a transient stage, then MERGE.

    The Postgres analogue is ``CREATE TEMP TABLE ... COPY ... INSERT ON
    CONFLICT`` (see ``IdentityStore.bulk_upsert_identities``). Two Snowflake
    specifics:

    - the stage is a NAMED transient table, not a session TEMP table: fakesnow
      cannot ``write_pandas`` into a TEMP table, and a warehouse session is not
      somewhere to hang state in a UDF worker;
    - the stage name carries a uuid4 so two concurrent writers cannot collide,
      the same failure mode #2699 hit with a fixed extraction directory.

    ``write_pandas`` is reached through the module, never a from-import, so
    ``fakesnow.patch()`` binds.

    The stage table is created ``LIKE target``, so its JSON columns are
    already ``VARIANT`` -- but ``write_pandas`` writes them into the stage as
    plain strings, which a bare ``s.<col>`` reference would insert/update as
    a JSON-encoded string rather than a parsed VARIANT. Each ``json_cols``
    entry is therefore cast with ``PARSE_JSON`` in the ``USING`` subquery's
    own ``SELECT`` list (rather than at each ``s.<col>`` use in ``UPDATE
    SET``/``INSERT VALUES``, which triggers a MERGE-rewrite binder bug in the
    fakesnow/duckdb translation this module is tested against) so every
    later reference to ``s.<col>`` in the ``UPDATE SET`` and ``INSERT
    VALUES`` lists already sees the parsed value, mirroring what
    ``merge_one`` does with its literal ``SELECT %s AS col`` source.
    """
    if not rows:
        return 0
    import pandas as pd  # noqa: PLC0415
    import snowflake.connector.pandas_tools as pandas_tools  # noqa: PLC0415

    stage = f"_gm_stage_{target}_{uuid.uuid4().hex}"
    cols = list(rows[0].keys())
    df = pd.DataFrame(
        [[r.get(c) for c in cols] for r in rows],
        columns=[c.upper() for c in cols],
    )
    execute(conn, f"CREATE OR REPLACE TRANSIENT TABLE {stage} LIKE {target}")
    try:
        pandas_tools.write_pandas(
            conn, df, stage.upper(),
            database=database, schema=schema, auto_create_table=False,
        )
        # NULL-safe, matching merge_one -- see its docstring.
        on = " AND ".join(f"t.{c} IS NOT DISTINCT FROM s.{c}" for c in key_cols)
        using_cols = ", ".join(
            f"PARSE_JSON({c}) AS {c}" if c in json_cols else c for c in cols
        )
        sql = [
            f"MERGE INTO {target} t USING "
            f"(SELECT {using_cols} FROM {stage}) s ON {on}"
        ]
        if update_cols:
            sets = ", ".join(f"t.{c} = s.{c}" for c in update_cols)
            sql.append(f"WHEN MATCHED THEN UPDATE SET {sets}")
        insert_cols = ", ".join(cols)
        insert_vals = ", ".join(f"s.{c}" for c in cols)
        sql.append(
            f"WHEN NOT MATCHED THEN INSERT ({insert_cols}) "
            f"VALUES ({insert_vals})"
        )
        execute(conn, "\n".join(sql))
    finally:
        execute(conn, f"DROP TABLE IF EXISTS {stage}")
    return len(rows)
