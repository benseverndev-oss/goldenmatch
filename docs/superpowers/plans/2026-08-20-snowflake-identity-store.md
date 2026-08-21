# Snowflake-native IdentityStore Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `IdentityStore(backend="snowflake")` — a backend whose tables *are* Snowflake tables — so the identity graph survives a UDF/procedure worker, and wire it into the bulk fast path.

**Architecture:** A shared `_store_sql.py` owns connection resolution, the case-insensitive row wrapper, DDL, and the `MERGE`/staging primitives. `SnowflakeIdentityStore` implements the store surface on top of it and is reached through per-method `if self._backend == "snowflake"` early returns in `IdentityStore`, exactly as `MongoIdentityStore` already is. Singleton writes are immediate; throughput comes from the existing `bulk_*` contract, reimplemented as `write_pandas` into a transient stage plus a `MERGE`.

**Tech Stack:** Python 3.12/3.13, `snowflake-connector-python>=3.0` (existing `snowflake` extra), `fakesnow` (new dev dep, DuckDB-backed), pytest.

**Spec:** `docs/superpowers/specs/2026-08-20-snowflake-native-stores-design.md`

## Global Constraints

- **Snowflake enforces only `NOT NULL`.** `PRIMARY KEY`, `UNIQUE` and `FOREIGN KEY` are metadata. Every idempotency guarantee must be expressed as an explicit `MERGE`. Never port `INSERT OR IGNORE`, `INSERT OR REPLACE`, or `ON CONFLICT DO UPDATE` as a bare `INSERT`.
- **`write_pandas` is reached as `pandas_tools.write_pandas(...)`**, never a `from`-import. A `from`-import binds the real function before `fakesnow.patch()` runs.
- **Stage tables are named `TRANSIENT` tables with a `uuid4` suffix**, never session `TEMP` tables. `TEMP` + `write_pandas` is broken under fakesnow, and a fixed stage name collides between concurrent writers.
- **Identifiers are unquoted** in all DDL and SQL, so Snowflake uppercases them. Reads go through `CaseInsensitiveRow`.
- **Parameters use the connector default `pyformat` (`%s`)**, not the `?` placeholders the SQLite/Postgres paths use.
- **`SCHEMA_VERSION` is 7** (`identity/store.py:52`). The Snowflake DDL must match schema v7 and record that version.
- **Test environment (worktree).** Tests run against the main checkout's venv with `PYTHONPATH` pointing at the worktree, native off:
  ```bash
  export PYTHONPATH="D:/show_case/gm-snowflake/packages/python/goldenmatch"
  export GOLDENMATCH_NATIVE=0
  PY="/d/show_case/goldenmatch/.venv/Scripts/python.exe"
  ```
  `PYTHONPATH` must use the `D:/...` form, not the MSYS `/d/...` form, or Windows Python will not resolve it.
- **Commit after every task.** Do not batch.

---

### Task 1: Connection resolution and the case-insensitive row

**Files:**
- Create: `packages/python/goldenmatch/goldenmatch/snowflake/_store_sql.py`
- Create: `packages/python/goldenmatch/tests/snowflake/test_store_sql.py`
- Modify: `packages/python/goldenmatch/pyproject.toml` (add `fakesnow` to the `dev` extra)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `resolve_connection(connection: Any, *, database: str, schema: str) -> Any`
  - `class CaseInsensitiveRow(Mapping[str, Any])` — wraps one result row
  - `fetchall_rows(conn, sql: str, params: tuple = ()) -> list[CaseInsensitiveRow]`
  - `fetchone_row(conn, sql: str, params: tuple = ()) -> CaseInsensitiveRow | None`
  - `execute(conn, sql: str, params: tuple = ()) -> None`

- [ ] **Step 1: Add the dev dependency**

In `packages/python/goldenmatch/pyproject.toml`, inside `[project.optional-dependencies]`, append to the `dev` list:

```toml
    "fakesnow>=0.11",
```

Install it into the venv used for tests:

```bash
/d/show_case/goldenmatch/.venv/Scripts/python.exe -m pip install "fakesnow>=0.11" "snowflake-connector-python[pandas]>=3.0"
```

- [ ] **Step 2: Write the failing test**

Create `packages/python/goldenmatch/tests/snowflake/test_store_sql.py`:

```python
"""Unit tests for the shared Snowflake store plumbing."""
from __future__ import annotations

import pytest

fakesnow = pytest.importorskip("fakesnow")
import snowflake.connector  # noqa: E402


@pytest.fixture
def sf_conn():
    """A fakesnow-backed Snowflake connection on GM.PUB."""
    with fakesnow.patch():
        conn = snowflake.connector.connect(database="GM", schema="PUB")
        conn.cursor().execute("CREATE SCHEMA IF NOT EXISTS GM.PUB")
        yield conn
        conn.close()


def test_resolve_connection_passes_through_a_live_connection(sf_conn) -> None:
    from goldenmatch.snowflake._store_sql import resolve_connection

    assert resolve_connection(sf_conn, database="GM", schema="PUB") is sf_conn


def test_resolve_connection_unwraps_a_snowpark_session(sf_conn) -> None:
    from goldenmatch.snowflake._store_sql import resolve_connection

    class FakeSession:
        connection = sf_conn

    assert resolve_connection(
        FakeSession(), database="GM", schema="PUB"
    ) is sf_conn


def test_resolve_connection_rejects_none() -> None:
    from goldenmatch.snowflake._store_sql import resolve_connection

    with pytest.raises(ValueError, match="requires connection="):
        resolve_connection(None, database="GM", schema="PUB")


def test_case_insensitive_row_reads_lowercase_keys(sf_conn) -> None:
    from goldenmatch.snowflake._store_sql import execute, fetchone_row

    execute(sf_conn, "CREATE TABLE t (entity_id STRING, confidence FLOAT)")
    execute(sf_conn, "INSERT INTO t VALUES (%s, %s)", ("e1", 0.75))
    row = fetchone_row(sf_conn, "SELECT entity_id, confidence FROM t")
    assert row is not None
    assert row["entity_id"] == "e1"
    assert row["ENTITY_ID"] == "e1"
    assert row["confidence"] == 0.75


def test_fetchone_row_returns_none_when_empty(sf_conn) -> None:
    from goldenmatch.snowflake._store_sql import execute, fetchone_row

    execute(sf_conn, "CREATE TABLE t2 (a STRING)")
    assert fetchone_row(sf_conn, "SELECT a FROM t2") is None


def test_fetchall_rows_returns_every_row(sf_conn) -> None:
    from goldenmatch.snowflake._store_sql import execute, fetchall_rows

    execute(sf_conn, "CREATE TABLE t3 (a STRING)")
    execute(sf_conn, "INSERT INTO t3 VALUES (%s)", ("x",))
    execute(sf_conn, "INSERT INTO t3 VALUES (%s)", ("y",))
    rows = fetchall_rows(sf_conn, "SELECT a FROM t3 ORDER BY a")
    assert [r["a"] for r in rows] == ["x", "y"]
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
export PYTHONPATH="D:/show_case/gm-snowflake/packages/python/goldenmatch"
export GOLDENMATCH_NATIVE=0
/d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest \
  packages/python/goldenmatch/tests/snowflake/test_store_sql.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'goldenmatch.snowflake._store_sql'`

- [ ] **Step 4: Write the implementation**

Create `packages/python/goldenmatch/goldenmatch/snowflake/_store_sql.py`:

```python
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
from collections.abc import Iterator, Mapping
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
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
/d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest \
  packages/python/goldenmatch/tests/snowflake/test_store_sql.py -v
```

Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/snowflake/_store_sql.py \
        packages/python/goldenmatch/tests/snowflake/test_store_sql.py \
        packages/python/goldenmatch/pyproject.toml
git commit -m "feat(snowflake): connection resolution and case-insensitive rows for the store backends"
```

---

### Task 2: Schema DDL and `ensure_schema`

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/snowflake/_store_sql.py`
- Modify: `packages/python/goldenmatch/tests/snowflake/test_store_sql.py`

**Interfaces:**
- Consumes: `execute`, `fetchone_row` (Task 1)
- Produces:
  - `IDENTITY_DDL: str` — the nine identity tables
  - `ensure_schema(conn, ddl: str, *, database: str, schema: str, version: int) -> None`
  - `schema_version(conn) -> int | None`

The nine tables mirror `_SCHEMA` in `identity/store.py` at `SCHEMA_VERSION = 7`: `identity_nodes`, `source_records`, `evidence_edges`, `identity_events`, `audit_seals`, `identity_aliases`, `identity_record_block_keys`, `identity_relationships`, `identity_runs`.

- [ ] **Step 1: Write the failing test**

Append to `tests/snowflake/test_store_sql.py`:

```python
def test_ensure_schema_creates_every_identity_table(sf_conn) -> None:
    from goldenmatch.snowflake._store_sql import (
        IDENTITY_DDL,
        ensure_schema,
        fetchall_rows,
    )

    ensure_schema(sf_conn, IDENTITY_DDL, database="GM", schema="PUB", version=7)
    rows = fetchall_rows(
        sf_conn,
        "SELECT table_name FROM GM.information_schema.tables "
        "WHERE table_schema = %s",
        ("PUB",),
    )
    names = {r["table_name"].lower() for r in rows}
    for expected in (
        "identity_nodes", "source_records", "evidence_edges",
        "identity_events", "audit_seals", "identity_aliases",
        "identity_record_block_keys", "identity_relationships",
        "identity_runs",
    ):
        assert expected in names, f"{expected} missing from {sorted(names)}"


def test_ensure_schema_is_idempotent(sf_conn) -> None:
    from goldenmatch.snowflake._store_sql import (
        IDENTITY_DDL,
        ensure_schema,
        execute,
        fetchone_row,
        schema_version,
    )

    ensure_schema(sf_conn, IDENTITY_DDL, database="GM", schema="PUB", version=7)
    execute(
        sf_conn,
        "INSERT INTO identity_nodes (entity_id, status) VALUES (%s, %s)",
        ("e1", "active"),
    )
    # Re-running must not drop the row or duplicate the version marker.
    ensure_schema(sf_conn, IDENTITY_DDL, database="GM", schema="PUB", version=7)
    row = fetchone_row(sf_conn, "SELECT COUNT(*) AS n FROM identity_nodes")
    assert row is not None and row["n"] == 1
    assert schema_version(sf_conn) == 7


def test_autoincrement_ids_are_assigned(sf_conn) -> None:
    from goldenmatch.snowflake._store_sql import (
        IDENTITY_DDL,
        ensure_schema,
        execute,
        fetchall_rows,
    )

    ensure_schema(sf_conn, IDENTITY_DDL, database="GM", schema="PUB", version=7)
    for kind in ("created", "merged"):
        execute(
            sf_conn,
            "INSERT INTO identity_events (entity_id, kind) VALUES (%s, %s)",
            ("e1", kind),
        )
    rows = fetchall_rows(
        sf_conn, "SELECT event_id, kind FROM identity_events ORDER BY event_id"
    )
    assert [r["event_id"] for r in rows] == [1, 2]
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
/d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest \
  packages/python/goldenmatch/tests/snowflake/test_store_sql.py -v -k ensure_schema
```

Expected: FAIL — `ImportError: cannot import name 'IDENTITY_DDL'`

- [ ] **Step 3: Write the implementation**

Read `_SCHEMA` at `packages/python/goldenmatch/goldenmatch/identity/store.py:100-260` and translate each of the nine `CREATE TABLE` statements. Apply this mapping and nothing else — column names, nullability and ordering stay identical:

| SQLite | Snowflake |
|---|---|
| `TEXT` | `STRING` |
| `REAL` | `FLOAT` |
| `INTEGER PRIMARY KEY AUTOINCREMENT` | `NUMBER AUTOINCREMENT START 1 INCREMENT 1` |
| `TIMESTAMP ... DEFAULT CURRENT_TIMESTAMP` | `TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()` |
| `TEXT` holding JSON (`golden_record`, `payload`, `field_scores`, `negative_evidence`, `controller_snapshot`) | `VARIANT` |
| `CREATE INDEX ...` | *omit* — Snowflake has no secondary indexes |
| `UNIQUE(...)` / `PRIMARY KEY (...)` | keep as metadata, but never rely on it |

Add to `_store_sql.py`:

```python
_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS _gm_schema_version (
    component STRING NOT NULL,
    version   NUMBER NOT NULL
);
"""

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
-- ... the remaining eight tables, translated from _SCHEMA as tabulated above
"""


def ensure_schema(
    conn: Any, ddl: str, *, database: str, schema: str, version: int
) -> None:
    """Create the schema and tables if absent, and stamp the version.

    Idempotent: every statement is ``IF NOT EXISTS`` and the version marker is
    written with a MERGE, so re-opening a store against a populated warehouse
    neither drops rows nor duplicates the marker.
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
        ("identity", version),
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
```

Replace the `-- ... the remaining eight tables` comment with the eight real `CREATE TABLE IF NOT EXISTS` statements translated from `_SCHEMA`. Do not leave the comment in place.

- [ ] **Step 4: Run the test to verify it passes**

```bash
/d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest \
  packages/python/goldenmatch/tests/snowflake/test_store_sql.py -v
```

Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/snowflake/_store_sql.py \
        packages/python/goldenmatch/tests/snowflake/test_store_sql.py
git commit -m "feat(snowflake): identity schema DDL and idempotent ensure_schema"
```

---

### Task 3: `merge_one` and `stage_and_merge`

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/snowflake/_store_sql.py`
- Modify: `packages/python/goldenmatch/tests/snowflake/test_store_sql.py`

**Interfaces:**
- Consumes: `execute`, `fetchall_rows`, `ensure_schema` (Tasks 1-2)
- Produces:
  - `merge_one(conn, table: str, key_cols: Sequence[str], row: Mapping[str, Any], *, update_cols: Sequence[str] | None = None, json_cols: Sequence[str] = ()) -> None`
  - `stage_and_merge(conn, target: str, rows: list[Mapping[str, Any]], key_cols: Sequence[str], *, update_cols: Sequence[str] | None = None, json_cols: Sequence[str] = (), database: str, schema: str) -> int`

`update_cols=None` means insert-if-absent only (the `INSERT OR IGNORE` replacement); a non-empty list adds the `WHEN MATCHED THEN UPDATE` branch. `json_cols` are wrapped in `PARSE_JSON` so `VARIANT` columns round-trip.

- [ ] **Step 1: Write the failing test**

Append to `tests/snowflake/test_store_sql.py`:

```python
@pytest.fixture
def schema_conn(sf_conn):
    from goldenmatch.snowflake._store_sql import IDENTITY_DDL, ensure_schema

    ensure_schema(sf_conn, IDENTITY_DDL, database="GM", schema="PUB", version=7)
    return sf_conn


def test_merge_one_inserts_then_updates(schema_conn) -> None:
    from goldenmatch.snowflake._store_sql import fetchall_rows, merge_one

    for confidence in (0.5, 0.9):
        merge_one(
            schema_conn,
            "identity_nodes",
            ["entity_id"],
            {"entity_id": "e1", "status": "active", "confidence": confidence},
            update_cols=["status", "confidence"],
        )
    rows = fetchall_rows(
        schema_conn, "SELECT entity_id, confidence FROM identity_nodes"
    )
    assert len(rows) == 1, "MERGE must update in place, not append"
    assert rows[0]["confidence"] == 0.9


def test_merge_one_without_update_cols_is_insert_if_absent(schema_conn) -> None:
    from goldenmatch.snowflake._store_sql import fetchall_rows, merge_one

    for confidence in (0.5, 0.9):
        merge_one(
            schema_conn,
            "identity_nodes",
            ["entity_id"],
            {"entity_id": "e2", "status": "active", "confidence": confidence},
        )
    rows = fetchall_rows(
        schema_conn,
        "SELECT confidence FROM identity_nodes WHERE entity_id = %s",
        ("e2",),
    )
    assert len(rows) == 1
    assert rows[0]["confidence"] == 0.5, "second merge must not overwrite"


def test_merge_one_round_trips_a_variant_column(schema_conn) -> None:
    import json

    from goldenmatch.snowflake._store_sql import fetchone_row, merge_one

    merge_one(
        schema_conn,
        "identity_nodes",
        ["entity_id"],
        {
            "entity_id": "e3",
            "status": "active",
            "golden_record": json.dumps({"name": "Ada", "n": 1}),
        },
        update_cols=["status", "golden_record"],
        json_cols=["golden_record"],
    )
    row = fetchone_row(
        schema_conn,
        "SELECT golden_record FROM identity_nodes WHERE entity_id = %s",
        ("e3",),
    )
    assert row is not None
    assert json.loads(row["golden_record"]) == {"name": "Ada", "n": 1}


def test_stage_and_merge_upserts_a_batch(schema_conn) -> None:
    from goldenmatch.snowflake._store_sql import fetchall_rows, stage_and_merge

    first = [
        {"entity_id": "b1", "status": "active", "confidence": 0.1},
        {"entity_id": "b2", "status": "active", "confidence": 0.2},
    ]
    assert stage_and_merge(
        schema_conn, "identity_nodes", first, ["entity_id"],
        update_cols=["status", "confidence"], database="GM", schema="PUB",
    ) == 2
    second = [
        {"entity_id": "b2", "status": "retired", "confidence": 0.9},
        {"entity_id": "b3", "status": "active", "confidence": 0.3},
    ]
    stage_and_merge(
        schema_conn, "identity_nodes", second, ["entity_id"],
        update_cols=["status", "confidence"], database="GM", schema="PUB",
    )
    rows = fetchall_rows(
        schema_conn,
        "SELECT entity_id, status, confidence FROM identity_nodes "
        "ORDER BY entity_id",
    )
    assert [r["entity_id"] for r in rows] == ["b1", "b2", "b3"]
    assert rows[1]["status"] == "retired"
    assert rows[1]["confidence"] == 0.9


def test_stage_and_merge_on_empty_input_is_a_noop(schema_conn) -> None:
    from goldenmatch.snowflake._store_sql import stage_and_merge

    assert stage_and_merge(
        schema_conn, "identity_nodes", [], ["entity_id"],
        database="GM", schema="PUB",
    ) == 0


def test_stage_tables_do_not_survive(schema_conn) -> None:
    from goldenmatch.snowflake._store_sql import fetchall_rows, stage_and_merge

    stage_and_merge(
        schema_conn, "identity_nodes",
        [{"entity_id": "s1", "status": "active"}], ["entity_id"],
        database="GM", schema="PUB",
    )
    rows = fetchall_rows(
        schema_conn,
        "SELECT table_name FROM GM.information_schema.tables "
        "WHERE table_schema = %s",
        ("PUB",),
    )
    leaked = [r["table_name"] for r in rows if "STAGE" in r["table_name"].upper()]
    assert leaked == [], f"stage tables leaked: {leaked}"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
/d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest \
  packages/python/goldenmatch/tests/snowflake/test_store_sql.py -v -k "merge"
```

Expected: FAIL — `ImportError: cannot import name 'merge_one'`

- [ ] **Step 3: Write the implementation**

Add to `_store_sql.py`:

```python
import uuid
from collections.abc import Sequence


def _value_sql(col: str, json_cols: Sequence[str]) -> str:
    """Placeholder for one column, wrapping VARIANT columns in PARSE_JSON."""
    return f"PARSE_JSON(%s)" if col in json_cols else "%s"


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
    """
    cols = list(row.keys())
    src = ", ".join(
        f"{_value_sql(c, json_cols)} AS {c}" for c in cols
    )
    on = " AND ".join(f"t.{c} = s.{c}" for c in key_cols)
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
        on = " AND ".join(f"t.{c} = s.{c}" for c in key_cols)
        sql = [f"MERGE INTO {target} t USING {stage} s ON {on}"]
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
```

Note on `json_cols` in `stage_and_merge`: because the stage table is created `LIKE target`, its JSON columns are already `VARIANT`. `write_pandas` writes them as strings, so the `MERGE` must cast — add `PARSE_JSON(s.<col>)` in place of `s.<col>` for each column in `json_cols`, in both the `UPDATE SET` and the `INSERT VALUES` lists.

- [ ] **Step 4: Run the test to verify it passes**

```bash
/d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest \
  packages/python/goldenmatch/tests/snowflake/test_store_sql.py -v
```

Expected: 15 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/snowflake/_store_sql.py \
        packages/python/goldenmatch/tests/snowflake/test_store_sql.py
git commit -m "feat(snowflake): merge_one and stage_and_merge primitives"
```

---

### Task 4: `SnowflakeIdentityStore` — nodes and records

**Files:**
- Create: `packages/python/goldenmatch/goldenmatch/identity/snowflake_backend.py`
- Create: `packages/python/goldenmatch/tests/snowflake/test_identity_store_snowflake.py`
- Modify: `packages/python/goldenmatch/goldenmatch/identity/store.py` (`__init__` ~line 341, `close` ~line 388, and the node/record methods at 1360-1685)

**Interfaces:**
- Consumes: everything from Tasks 1-3.
- Produces: `class SnowflakeIdentityStore` with `__init__(self, connection: Any = None, *, database: str = "GOLDENMATCH", schema: str = "PUBLIC")`, `close()`, `supports_bulk = True`, and these twelve methods with signatures **identical to `IdentityStore`'s**: `count_nodes`, `get_node`, `upsert_identity`, `get_identity`, `get_identities`, `list_identities`, `count_identities`, `retire_identity`, `upsert_record`, `get_record`, `get_records_for_entity`, `find_entity_by_record`, `lookup_entity_ids`.

- [ ] **Step 1: Write the failing test**

Create `packages/python/goldenmatch/tests/snowflake/test_identity_store_snowflake.py`:

```python
"""IdentityStore(backend="snowflake") against fakesnow."""
from __future__ import annotations

import pytest

fakesnow = pytest.importorskip("fakesnow")
import snowflake.connector  # noqa: E402


@pytest.fixture
def store():
    from goldenmatch.identity.store import IdentityStore

    with fakesnow.patch():
        conn = snowflake.connector.connect(database="GM", schema="PUB")
        s = IdentityStore(
            backend="snowflake", connection=conn, database="GM", schema="PUB"
        )
        yield s
        s.close()


def test_upsert_identity_and_get(store) -> None:
    from goldenmatch.identity.model import IdentityNode
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    store.upsert_identity(IdentityNode(
        entity_id=eid, dataset="customers", status="active", confidence=0.99,
    ))
    node = store.get_identity(eid)
    assert node is not None
    assert node.entity_id == eid
    assert node.dataset == "customers"
    assert node.confidence == 0.99


def test_upsert_identity_is_idempotent(store) -> None:
    from goldenmatch.identity.model import IdentityNode
    from goldenmatch.identity.store import new_entity_id

    node = IdentityNode(entity_id=new_entity_id(), dataset="c", status="active")
    store.upsert_identity(node)
    store.upsert_identity(node)
    assert store.count_identities() == 1


def test_golden_record_round_trips(store) -> None:
    from goldenmatch.identity.model import IdentityNode
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    store.upsert_identity(IdentityNode(
        entity_id=eid, golden_record={"name": "Ada", "score": 1.5},
    ))
    node = store.get_identity(eid)
    assert node is not None
    assert node.golden_record == {"name": "Ada", "score": 1.5}


def test_upsert_record_and_lookup(store) -> None:
    from goldenmatch.identity.model import IdentityNode, SourceRecord
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=eid))
    store.upsert_record(SourceRecord(
        record_id="crm:1", source="crm", source_pk="1",
        record_hash="h1", entity_id=eid, payload={"email": "a@b.c"},
    ))
    rec = store.get_record("crm:1")
    assert rec is not None
    assert rec.entity_id == eid
    assert rec.payload == {"email": "a@b.c"}
    assert store.find_entity_by_record("crm:1") == eid
    assert store.lookup_entity_ids(["crm:1", "crm:missing"]) == {"crm:1": eid}


def test_get_identities_batches(store) -> None:
    from goldenmatch.identity.model import IdentityNode
    from goldenmatch.identity.store import new_entity_id

    ids = [new_entity_id() for _ in range(3)]
    for eid in ids:
        store.upsert_identity(IdentityNode(entity_id=eid, dataset="c"))
    got = store.get_identities(ids)
    assert set(got) == set(ids)


def test_list_and_count_filter_by_dataset(store) -> None:
    from goldenmatch.identity.model import IdentityNode
    from goldenmatch.identity.store import new_entity_id

    for ds in ("customers", "customers", "vendors"):
        store.upsert_identity(IdentityNode(entity_id=new_entity_id(), dataset=ds))
    assert store.count_identities(dataset="customers") == 2
    assert len(store.list_identities(dataset="customers")) == 2


def test_retire_identity_sets_status_and_merged_into(store) -> None:
    from goldenmatch.identity.model import IdentityNode
    from goldenmatch.identity.store import new_entity_id

    loser, winner = new_entity_id(), new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=loser))
    store.upsert_identity(IdentityNode(entity_id=winner))
    store.retire_identity(loser, merged_into=winner, run_name="r1")
    node = store.get_identity(loser)
    assert node is not None
    assert node.status == "merged"
    assert node.merged_into == winner
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
/d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest \
  packages/python/goldenmatch/tests/snowflake/test_identity_store_snowflake.py -v
```

Expected: FAIL — `NotImplementedError: Backend 'snowflake' not supported`

- [ ] **Step 3: Wire the backend into `IdentityStore`**

In `identity/store.py`, add `schema: str = "PUBLIC"` to the `__init__` signature (after `client`). Then immediately before the `if backend == "sqlite":` branch at line 353, insert:

```python
        # SnowflakeIdentityStore holds a warehouse connection. Delegated by the
        # per-method ``if self._backend == "snowflake"`` early returns below,
        # exactly as the Mongo backend is.
        self._sf: Any = None
        if backend == "snowflake":
            from goldenmatch.identity.snowflake_backend import (  # noqa: PLC0415
                SnowflakeIdentityStore,
            )
            self._sf = SnowflakeIdentityStore(
                connection=connection, database=database, schema=schema,
            )
            return
```

Set `self._sf = None` alongside `self._mongo: Any = None` at line 340 so non-Snowflake backends have the attribute.

Extend `close()`:

```python
    def close(self) -> None:
        if self._backend == "mongo":
            self._mongo.close()
            return
        if self._backend == "snowflake":
            self._sf.close()
            return
        self._conn.close()
```

Add the `supports_bulk` property immediately after `close()`:

```python
    @property
    def supports_bulk(self) -> bool:
        """True when the backend implements the ``bulk_*`` staged-write path.

        ``resolve_clusters`` branches on this rather than on a backend-name
        allowlist, so a new backend opts into the fast path by implementing it.
        """
        return self._backend in ("postgres", "sqlite", "snowflake")
```

- [ ] **Step 4: Write the backend**

Create `identity/snowflake_backend.py`. Every method's signature is copied verbatim from `IdentityStore`. The row-mapping helpers are reused — import `IdentityStore` lazily inside methods and call `IdentityStore._row_to_identity` / `._row_to_record`, which already tolerate `VARIANT` values arriving as JSON strings.

```python
"""Identity Store backed by Snowflake tables.

Reached through ``IdentityStore(backend="snowflake")``; see
``docs/superpowers/specs/2026-08-20-snowflake-native-stores-design.md``.

Every write is a MERGE. Snowflake does not enforce PRIMARY KEY or UNIQUE, so a
bare INSERT would silently duplicate on a replayed run instead of being the
no-op the resolver relies on.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from goldenmatch.identity.model import IdentityNode, SourceRecord
from goldenmatch.snowflake._store_sql import (
    IDENTITY_DDL,
    ensure_schema,
    execute,
    fetchall_rows,
    fetchone_row,
    merge_one,
    resolve_connection,
    stage_and_merge,
)

_NODE_UPDATE = [
    "status", "merged_into", "golden_record", "confidence", "dataset",
    "updated_at",
]
_RECORD_UPDATE = ["record_hash", "entity_id", "payload", "last_seen_at"]


class SnowflakeIdentityStore:
    supports_bulk = True

    def __init__(
        self,
        connection: Any = None,
        *,
        database: str = "GOLDENMATCH",
        schema: str = "PUBLIC",
    ) -> None:
        self._database = database
        self._schema = schema
        self._conn = resolve_connection(
            connection, database=database, schema=schema
        )
        ensure_schema(
            self._conn, IDENTITY_DDL,
            database=database, schema=schema, version=7,
        )

    def close(self) -> None:
        self._conn.close()

    # ----- identity nodes -------------------------------------------------

    def upsert_identity(self, node: IdentityNode) -> None:
        merge_one(
            self._conn, "identity_nodes", ["entity_id"],
            {
                "entity_id": node.entity_id,
                "status": node.status,
                "merged_into": node.merged_into,
                "golden_record": (
                    json.dumps(node.golden_record)
                    if node.golden_record is not None else None
                ),
                "confidence": node.confidence,
                "dataset": node.dataset,
                "created_at": node.created_at.isoformat(),
                "updated_at": node.updated_at.isoformat(),
            },
            update_cols=_NODE_UPDATE,
            json_cols=["golden_record"],
        )

    def get_identity(self, entity_id: str) -> IdentityNode | None:
        from goldenmatch.identity.store import IdentityStore  # noqa: PLC0415

        row = fetchone_row(
            self._conn,
            "SELECT * FROM identity_nodes WHERE entity_id = %s",
            (entity_id,),
        )
        return IdentityStore._row_to_identity(row) if row else None
```

Implement the remaining eleven node/record methods against `_store_sql` following that pattern. Their exact required behaviour, read from `identity/store.py`:

| Method | Source lines | SQL |
|---|---|---|
| `count_nodes()` | 1360 | delegates to `count_identities()` |
| `get_node(entity_id)` | 1364 | delegates to `get_identity()` |
| `get_identities(entity_ids)` | 1402 | chunked `IN`-list `SELECT *`, returns `dict[str, IdentityNode]` |
| `list_identities(dataset, status, limit, offset)` | 1435 | `SELECT * ... ORDER BY created_at LIMIT %s OFFSET %s`, optional filters |
| `count_identities(dataset)` | 1463 | `SELECT COUNT(*) AS n`, optional dataset filter |
| `retire_identity(entity_id, merged_into, run_name)` | 1475 | `UPDATE identity_nodes SET status='merged', merged_into=%s, updated_at=CURRENT_TIMESTAMP()` |
| `upsert_record(rec)` | 1603 | `merge_one` on `source_records` key `record_id`, `json_cols=["payload"]` |
| `get_record(record_id)` | 1627 | `SELECT * ... WHERE record_id = %s` |
| `get_records_for_entity(entity_id)` | 1635 | `SELECT * ... WHERE entity_id = %s ORDER BY first_seen_at` |
| `find_entity_by_record(record_id)` | 1644 | `SELECT entity_id ... WHERE record_id = %s` |
| `lookup_entity_ids(record_ids)` | 1652 | chunked `IN`-list, `AND entity_id IS NOT NULL` |

Keep the 900-element `IN`-list chunking from `lookup_entity_ids` (store.py:1660-1685). Snowflake's expression limit is different from SQLite's, but chunking is harmless and the behaviour must match.

- [ ] **Step 5: Add the dispatch branches**

For each of the thirteen methods, add the early return as the first statement of the `IdentityStore` method, immediately after the existing Mongo branch. For example, at `store.py:1368`:

```python
    def upsert_identity(self, node: IdentityNode) -> None:
        if self._backend == "mongo":
            self._mongo.upsert_identity(node)
            return
        if self._backend == "snowflake":
            self._sf.upsert_identity(node)
            return
```

- [ ] **Step 6: Run the test to verify it passes**

```bash
/d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest \
  packages/python/goldenmatch/tests/snowflake/test_identity_store_snowflake.py -v
```

Expected: 7 passed.

- [ ] **Step 7: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/identity/snowflake_backend.py \
        packages/python/goldenmatch/goldenmatch/identity/store.py \
        packages/python/goldenmatch/tests/snowflake/test_identity_store_snowflake.py
git commit -m "feat(identity): Snowflake backend for identity nodes and source records"
```

---

### Task 5: Edges, events, aliases — and the replay-idempotency guarantee

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/identity/snowflake_backend.py`
- Modify: `packages/python/goldenmatch/goldenmatch/identity/store.py` (methods at 1771-1870, 2265-2300, 2343-2360, 2455-2510)
- Modify: `packages/python/goldenmatch/tests/snowflake/test_identity_store_snowflake.py`

**Interfaces:**
- Consumes: Task 4's `SnowflakeIdentityStore`.
- Produces: `add_edge(edge, *, return_id=True)`, `edges_for_entity`, `edges_by_kind`, `find_conflicts`, `emit_event(event, *, return_id=True)`, `history`, `has_run_event`, `run_event_entities`, `add_alias`, `resolve_alias`.

This is the task the spec calls the highest-risk item. `add_edge` and `add_alias` are the two places where Snowflake's unenforced constraints would silently corrupt the graph.

- [ ] **Step 1: Write the failing test**

Append to `tests/snowflake/test_identity_store_snowflake.py`:

```python
def _seed_entity(store):
    from goldenmatch.identity.model import IdentityNode
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=eid, dataset="c"))
    return eid


def test_add_edge_is_idempotent_on_replay(store) -> None:
    """The constraint trap: Snowflake does NOT enforce the edge UNIQUE key.

    Replaying a run must not duplicate the edge. A bare INSERT passes on
    SQLite (UNIQUE + INSERT OR IGNORE) and silently duplicates here.
    """
    from goldenmatch.identity.model import EvidenceEdge

    eid = _seed_entity(store)
    edge = EvidenceEdge(
        entity_id=eid, record_a_id="crm:1", record_b_id="crm:2",
        kind="same_as", score=0.95, run_name="run-1",
    )
    store.add_edge(edge)
    store.add_edge(edge)
    assert len(store.edges_for_entity(eid)) == 1


def test_add_edge_separates_kinds_on_the_same_pair(store) -> None:
    from goldenmatch.identity.model import EvidenceEdge

    eid = _seed_entity(store)
    for kind in ("same_as", "conflicts_with"):
        store.add_edge(EvidenceEdge(
            entity_id=eid, record_a_id="crm:1", record_b_id="crm:2",
            kind=kind, run_name="run-1",
        ))
    assert len(store.edges_for_entity(eid)) == 2
    assert len(store.find_conflicts(dataset=None)) == 1


def test_add_edge_canonicalizes_pair_order(store) -> None:
    from goldenmatch.identity.model import EvidenceEdge

    eid = _seed_entity(store)
    store.add_edge(EvidenceEdge(
        entity_id=eid, record_a_id="crm:2", record_b_id="crm:1",
        kind="same_as", run_name="run-1",
    ))
    store.add_edge(EvidenceEdge(
        entity_id=eid, record_a_id="crm:1", record_b_id="crm:2",
        kind="same_as", run_name="run-1",
    ))
    assert len(store.edges_for_entity(eid)) == 1


def test_add_alias_replaces_rather_than_duplicating(store) -> None:
    from goldenmatch.identity.model import IdentityAlias

    first, second = _seed_entity(store), _seed_entity(store)
    store.add_alias(IdentityAlias(alias="MDM-1", entity_id=first, kind="mdm"))
    store.add_alias(IdentityAlias(alias="MDM-1", entity_id=second, kind="mdm"))
    assert store.resolve_alias("MDM-1", kind="mdm") == second


def test_emit_event_returns_an_id_and_history_reads_back(store) -> None:
    from goldenmatch.identity.model import IdentityEvent

    eid = _seed_entity(store)
    event_id = store.emit_event(IdentityEvent(
        entity_id=eid, kind="created", run_name="run-1",
        payload={"reason": "new cluster"},
    ))
    assert isinstance(event_id, int)
    events = store.history(eid)
    assert [e.kind for e in events] == ["created"]
    assert events[0].payload == {"reason": "new cluster"}


def test_has_run_event_and_run_event_entities(store) -> None:
    from goldenmatch.identity.model import IdentityEvent

    eid = _seed_entity(store)
    store.emit_event(IdentityEvent(
        entity_id=eid, kind="merged", run_name="run-42",
    ))
    assert store.has_run_event(eid, "run-42", "merged") is True
    assert store.has_run_event(eid, "run-42", "split") is False
    assert store.has_run_event(eid, "run-other", "merged") is False
    assert store.run_event_entities("run-42", "merged") == {eid}
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
/d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest \
  packages/python/goldenmatch/tests/snowflake/test_identity_store_snowflake.py -v -k "edge or alias or event"
```

Expected: FAIL — `AttributeError: 'SnowflakeIdentityStore' object has no attribute 'add_edge'`

- [ ] **Step 3: Implement `add_edge` and `add_alias`**

Add to `snowflake_backend.py`:

```python
_EDGE_KEY = ["entity_id", "record_a_id", "record_b_id", "kind", "run_name"]


def add_edge(self, edge: EvidenceEdge, *, return_id: bool = True) -> int | None:
    """Insert-if-absent on the five-column edge identity.

    The SQLite path relies on ``INSERT OR IGNORE`` against
    ``UNIQUE(entity_id, record_a_id, record_b_id, kind, run_name)``. That
    constraint is metadata-only in Snowflake, so the dedupe has to be the
    MERGE's own ``WHEN NOT MATCHED`` -- with no ``WHEN MATCHED`` branch, which
    is what makes it an ignore rather than an upsert.
    """
    a, b = canon_record_pair(edge.record_a_id, edge.record_b_id)
    merge_one(
        self._conn, "evidence_edges", _EDGE_KEY,
        {
            "entity_id": edge.entity_id,
            "record_a_id": a,
            "record_b_id": b,
            "kind": edge.kind,
            "score": edge.score,
            "matchkey_name": edge.matchkey_name,
            "field_scores": _dumps(edge.field_scores),
            "negative_evidence": _dumps(edge.negative_evidence),
            "controller_snapshot": _dumps(edge.controller_snapshot),
            "run_name": edge.run_name,
            "dataset": edge.dataset,
            "actor": edge.actor,
            "trust": edge.trust,
            "recorded_at": edge.recorded_at.isoformat(),
        },
        update_cols=None,  # insert-if-absent; see docstring
        json_cols=["field_scores", "negative_evidence", "controller_snapshot"],
    )
    if not return_id:
        return None
    row = fetchone_row(
        self._conn,
        "SELECT edge_id FROM evidence_edges WHERE entity_id = %s "
        "AND record_a_id = %s AND record_b_id = %s AND kind = %s "
        "AND COALESCE(run_name, '') = COALESCE(%s, '')",
        (edge.entity_id, a, b, edge.kind, edge.run_name),
    )
    return int(row["edge_id"]) if row else None


def add_alias(self, alias: IdentityAlias) -> None:
    """Upsert on (alias, kind, dataset) -- the SQLite INSERT OR REPLACE."""
    merge_one(
        self._conn, "identity_aliases", ["alias", "kind", "dataset"],
        {
            "alias": alias.alias,
            "entity_id": alias.entity_id,
            "kind": alias.kind,
            "dataset": alias.dataset,
            "recorded_at": alias.recorded_at.isoformat(),
        },
        update_cols=["entity_id", "recorded_at"],
    )
```

Add the helper and the imports it needs at module scope:

```python
def _dumps(value: Any) -> str | None:
    return json.dumps(value) if value else None
```

Extend the `model` import to `EvidenceEdge, IdentityAlias, IdentityEvent, IdentityNode, SourceRecord, canon_record_pair`.

Note the `COALESCE(run_name, '')` in the id read-back: it matches `store.py:1812` and is required because `run_name` is nullable and `NULL = NULL` is not true in SQL.

- [ ] **Step 4: Implement the remaining eight methods**

| Method | Source lines | SQL |
|---|---|---|
| `edges_for_entity(entity_id)` | 1822 | `SELECT * FROM evidence_edges WHERE entity_id = %s ORDER BY recorded_at` |
| `edges_by_kind(kind, dataset)` | 1831 | `WHERE kind = %s`, optional `AND dataset = %s`, `ORDER BY recorded_at DESC` |
| `find_conflicts(dataset)` | 1853 | `edges_by_kind("conflicts_with", dataset)` |
| `emit_event(event, *, return_id)` | 2265 | plain `INSERT` (events are an append-only log — no dedupe key), then `SELECT MAX(event_id) ... WHERE entity_id = %s` when `return_id` |
| `history(entity_id, limit)` | 2343 | `WHERE entity_id = %s ORDER BY event_id`, `LIMIT %s` when `limit` |
| `has_run_event(entity_id, run_name, kind)` | 2455 | `SELECT 1 ... LIMIT 1`, return `row is not None` |
| `run_event_entities(run_name, kind)` | 2465 | `SELECT DISTINCT entity_id ...`, return a `set[str]` |
| `resolve_alias(alias, kind)` | 2494 | `SELECT entity_id FROM identity_aliases WHERE alias = %s AND kind = %s` |

`emit_event` is the one write in this task that is a plain `INSERT`, not a `MERGE`: `identity_events` is an append-only log with an autoincrement key and no dedupe column. Replay-safety for events lives one level up, in `resolve_clusters`'s `has_run_event` guard.

Map rows back with `IdentityStore._row_to_edge` and `IdentityStore._row_to_event`.

- [ ] **Step 5: Add the ten dispatch branches**

As the first statement of each of the ten `IdentityStore` methods, after the
existing Mongo branch. `add_edge` at `store.py:1771` is the shape for the
methods that return a value:

```python
    def add_edge(self, edge: EvidenceEdge, *, return_id: bool = True) -> int | None:
        if self._backend == "mongo":
            return self._mongo.add_edge(edge)
        if self._backend == "snowflake":
            return self._sf.add_edge(edge, return_id=return_id)
```

and `add_alias` at `store.py:2480` is the shape for those that do not:

```python
    def add_alias(self, alias: IdentityAlias) -> None:
        if self._backend == "mongo":
            self._mongo.add_alias(alias)
            return
        if self._backend == "snowflake":
            self._sf.add_alias(alias)
            return
```

Note that the Mongo branch for `add_edge` drops `return_id` — do not copy that;
the Snowflake branch must forward it, because `resolve_clusters` passes
`return_id=False` on the hot path to avoid a read-back round-trip.

- [ ] **Step 6: Run the tests to verify they pass**

```bash
/d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest \
  packages/python/goldenmatch/tests/snowflake/test_identity_store_snowflake.py -v
```

Expected: 13 passed.

- [ ] **Step 7: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/identity/snowflake_backend.py \
        packages/python/goldenmatch/goldenmatch/identity/store.py \
        packages/python/goldenmatch/tests/snowflake/test_identity_store_snowflake.py
git commit -m "feat(identity): Snowflake edges, events and aliases with MERGE-based replay idempotency"
```

---

### Task 6: The remaining surface — relationships, seals, block keys, runs, stats

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/identity/snowflake_backend.py`
- Modify: `packages/python/goldenmatch/goldenmatch/identity/store.py`
- Create: `packages/python/goldenmatch/tests/snowflake/test_identity_extras_snowflake.py`

**Interfaces:**
- Consumes: Tasks 1-5.
- Produces: `merge_by_shared_field`, `index_record_block_keys`, `candidates_by_block_keys`, `status_counts`, `active_record_stats`, `relationship_groups`, `sample_records`, `relationship_field_stats`, `add_relationships`, `reconcile_relationships`, `get_relationships`, `count_relationships`, `list_relationships`, `record_run`, `run_config`, `export_audit_log`, `add_seal`, `latest_seal`, `list_seals`.

Nineteen methods. Signatures are copied verbatim from `identity/store.py`; the line numbers in the table below are where each one's SQL and semantics live.

- [ ] **Step 1: Write the failing test**

Create `tests/snowflake/test_identity_extras_snowflake.py` with one test per behaviour group. Reuse the `store` fixture by importing it:

```python
"""Relationships, seals, block keys and run metadata on the Snowflake backend."""
from __future__ import annotations

import pytest

fakesnow = pytest.importorskip("fakesnow")

from tests.snowflake.test_identity_store_snowflake import store  # noqa: F401,E402


def test_index_and_query_block_keys(store) -> None:  # noqa: F811
    from goldenmatch.identity.model import IdentityNode
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=eid))
    store.index_record_block_keys("crm:1", eid, [("soundex_last", "S530")])
    assert store.candidates_by_block_keys([("soundex_last", "S530")]) == {"crm:1"}
    assert store.candidates_by_block_keys([("soundex_last", "X999")]) == set()


def test_index_record_block_keys_is_idempotent(store) -> None:  # noqa: F811
    from goldenmatch.identity.model import IdentityNode
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=eid))
    for _ in range(2):
        store.index_record_block_keys("crm:1", eid, [("soundex_last", "S530")])
    assert store.candidates_by_block_keys([("soundex_last", "S530")]) == {"crm:1"}


def test_status_counts(store) -> None:  # noqa: F811
    from goldenmatch.identity.model import IdentityNode
    from goldenmatch.identity.store import new_entity_id

    for status in ("active", "active", "merged"):
        store.upsert_identity(IdentityNode(
            entity_id=new_entity_id(), status=status, dataset="c",
        ))
    assert store.status_counts(dataset="c") == {"active": 2, "merged": 1}


def test_record_run_and_run_config(store) -> None:  # noqa: F811
    store.record_run(
        "run-1", config_id="cfg1", schema_version=7,
        config_json='{"a": 1}', dataset="c",
    )
    cfg = store.run_config("run-1")
    assert cfg is not None
    assert cfg["config_id"] == "cfg1"


def test_add_and_latest_seal(store) -> None:  # noqa: F811
    from goldenmatch.identity.model import AuditSeal

    seal_id = store.add_seal(AuditSeal(
        root_hash="abc123", event_count=2, last_event_id=2, dataset="c",
    ))
    assert isinstance(seal_id, int)
    latest = store.latest_seal(dataset="c")
    assert latest is not None
    assert latest.root_hash == "abc123"
    assert len(store.list_seals(dataset="c")) == 1


def test_relationships_round_trip(store) -> None:  # noqa: F811
    from goldenmatch.identity.model import IdentityNode
    from goldenmatch.identity.store import new_entity_id

    a, b = new_entity_id(), new_entity_id()
    for eid in (a, b):
        store.upsert_identity(IdentityNode(entity_id=eid, dataset="c"))
    store.add_relationships([(a, b, "shares_address", "c", 1.0)])
    assert store.count_relationships() == 1
    assert len(store.get_relationships(a)) == 1
```

Before writing the implementation, read `store.py:2107` (`add_relationships`) to confirm the exact tuple arity and column order, and `store.py:2301` (`record_run`) for `run_config`'s returned dict keys. Match them exactly — the test above assumes the shapes at those lines and must be corrected to whatever the source actually says, not the other way round.

- [ ] **Step 2: Run the test to verify it fails**

```bash
/d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest \
  packages/python/goldenmatch/tests/snowflake/test_identity_extras_snowflake.py -v
```

Expected: FAIL — `AttributeError` on the first missing method.

- [ ] **Step 3: Implement the nineteen methods**

| Method | Source | Notes for the Snowflake port |
|---|---|---|
| `merge_by_shared_field(dataset, field, max_group)` | 1495 | Set-shaped `GROUP BY` over `source_records`; the `json_extract` on `payload` becomes `payload:<field>::string` |
| `index_record_block_keys(record_id, entity_id, keys)` | 1686 | `merge_one` per key on `(record_id, key_name, key_value)`, `update_cols=["entity_id"]` |
| `candidates_by_block_keys(keys)` | 1736 | `SELECT DISTINCT record_id` with an `OR`-of-pairs predicate; returns `set[str]` |
| `status_counts(dataset)` | 1870 | `SELECT status, COUNT(*) AS n ... GROUP BY status` |
| `active_record_stats(dataset)` | 1888 | two `GROUP BY` aggregates; returns a 2-tuple of dicts |
| `relationship_groups(field, dataset, min_entities, max_entities, transform)` | 1927 | `GROUP BY` with `HAVING COUNT(DISTINCT entity_id) BETWEEN %s AND %s` |
| `sample_records(dataset, limit)` | 1983 | `SELECT record_id, payload ... LIMIT %s`; parse the `VARIANT` |
| `relationship_field_stats(field, dataset, min_entities, max_entities, transform)` | 2011 | same grouping as `relationship_groups`, aggregated to counts |
| `add_relationships(rows)` | 2107 | `stage_and_merge` on `identity_relationships`; returns the row count |
| `reconcile_relationships(dataset, kind, desired)` | 2141 | read current, `stage_and_merge` the additions, `DELETE` the removals; returns `(added, removed, kept)` |
| `get_relationships(entity_id)` | 2220 | `WHERE entity_id_a = %s OR entity_id_b = %s` |
| `count_relationships()` | 2238 | `SELECT COUNT(*) AS n` |
| `list_relationships(dataset)` | 2244 | optional dataset filter |
| `record_run(run_name, ...)` | 2301 | `merge_one` on `identity_runs` key `run_name` |
| `run_config(run_name)` | 2328 | `SELECT * ... WHERE run_name = %s` |
| `export_audit_log(dataset, actor, since)` | 2361 | `identity_events` with three optional filters, `ORDER BY event_id` |
| `add_seal(seal)` | 2395 | plain `INSERT` (append-only) + `SELECT MAX(seal_id)` |
| `latest_seal(dataset)` | 2416 | `ORDER BY seal_id DESC LIMIT 1` |
| `list_seals(dataset)` | 2437 | `ORDER BY seal_id` |

`transform` arguments on the relationship methods are the existing SQL-expression switch at `store.py:1940-1960` — port the same branch table, substituting Snowflake string functions where the names differ.

- [ ] **Step 4: Add the nineteen dispatch branches**

Same two shapes as Task 5 Step 5 — returning and non-returning. For the three
methods that return tuples (`merge_by_shared_field`, `active_record_stats`,
`reconcile_relationships`), the branch returns directly:

```python
    def merge_by_shared_field(
        self, dataset: str | None, field: str | list[str], max_group: int = 100,
    ) -> tuple[int, int]:
        if self._backend == "snowflake":
            return self._sf.merge_by_shared_field(dataset, field, max_group)
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
/d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest \
  packages/python/goldenmatch/tests/snowflake/ -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/identity/snowflake_backend.py \
        packages/python/goldenmatch/goldenmatch/identity/store.py \
        packages/python/goldenmatch/tests/snowflake/test_identity_extras_snowflake.py
git commit -m "feat(identity): Snowflake relationships, seals, block keys and run metadata"
```

---

### Task 7: Bulk writes, batching seams, and the capability gate

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/identity/snowflake_backend.py`
- Modify: `packages/python/goldenmatch/goldenmatch/identity/store.py` (methods at 761-1360)
- Modify: `packages/python/goldenmatch/goldenmatch/identity/resolve.py:717-720`
- Create: `packages/python/goldenmatch/tests/snowflake/test_identity_bulk_snowflake.py`

**Interfaces:**
- Consumes: `stage_and_merge` (Task 3), the store from Tasks 4-6.
- Produces: `bulk_upsert_identities(df)`, `bulk_upsert_records(df)`, `bulk_add_edges(df)`, `bulk_emit_events(df)`, `bulk_writes()`, `bulk_flush_checkpoint()`, all taking the same argument types as `IdentityStore`'s.

- [ ] **Step 1: Write the failing test**

Create `tests/snowflake/test_identity_bulk_snowflake.py`:

```python
"""Bulk staged-MERGE path, and its equivalence to the singleton path."""
from __future__ import annotations

from datetime import datetime

import pytest

fakesnow = pytest.importorskip("fakesnow")
pl = pytest.importorskip("polars")

from tests.snowflake.test_identity_store_snowflake import store  # noqa: F401,E402


def _nodes_df(ids):
    now = datetime(2026, 8, 20, 12, 0, 0)
    return pl.DataFrame(
        [
            {
                "entity_id": eid, "status": "active", "merged_into": None,
                "golden_record": None, "confidence": 0.9, "dataset": "c",
                "created_at": now, "updated_at": now,
            }
            for eid in ids
        ],
        schema={
            "entity_id": pl.Utf8, "status": pl.Utf8, "merged_into": pl.Utf8,
            "golden_record": pl.Utf8, "confidence": pl.Float64,
            "dataset": pl.Utf8, "created_at": pl.Datetime,
            "updated_at": pl.Datetime,
        },
    )


def test_supports_bulk_is_true(store) -> None:  # noqa: F811
    assert store.supports_bulk is True


def test_bulk_upsert_identities_inserts(store) -> None:  # noqa: F811
    from goldenmatch.identity.store import new_entity_id

    ids = [new_entity_id() for _ in range(3)]
    store.bulk_upsert_identities(_nodes_df(ids))
    assert store.count_identities() == 3


def test_bulk_upsert_identities_is_idempotent(store) -> None:  # noqa: F811
    from goldenmatch.identity.store import new_entity_id

    ids = [new_entity_id() for _ in range(3)]
    df = _nodes_df(ids)
    store.bulk_upsert_identities(df)
    store.bulk_upsert_identities(df)
    assert store.count_identities() == 3


def test_bulk_and_singleton_agree(store) -> None:  # noqa: F811
    """The two write paths must produce identical rows."""
    from goldenmatch.identity.model import IdentityNode
    from goldenmatch.identity.store import new_entity_id

    bulk_id, single_id = new_entity_id(), new_entity_id()
    store.bulk_upsert_identities(_nodes_df([bulk_id]))
    now = datetime(2026, 8, 20, 12, 0, 0)
    store.upsert_identity(IdentityNode(
        entity_id=single_id, status="active", confidence=0.9, dataset="c",
        created_at=now, updated_at=now,
    ))
    a, b = store.get_identity(bulk_id), store.get_identity(single_id)
    assert a is not None and b is not None
    for field_name in ("status", "confidence", "dataset", "merged_into"):
        assert getattr(a, field_name) == getattr(b, field_name), field_name


def test_bulk_on_empty_frame_is_a_noop(store) -> None:  # noqa: F811
    store.bulk_upsert_identities(_nodes_df([]))
    assert store.count_identities() == 0
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
/d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest \
  packages/python/goldenmatch/tests/snowflake/test_identity_bulk_snowflake.py -v
```

Expected: FAIL — `NotImplementedError: bulk_upsert_identities requires Postgres backend`

- [ ] **Step 3: Implement the bulk methods**

Add to `snowflake_backend.py`:

```python
import contextlib

_NODE_COLS = [
    "entity_id", "status", "merged_into", "golden_record", "confidence",
    "dataset", "created_at", "updated_at",
]


def bulk_upsert_identities(self, df: Any) -> None:
    """Staged MERGE, the Snowflake analogue of the Postgres COPY fast path.

    Mirrors ``IdentityStore.bulk_upsert_identities`` (store.py:996): missing
    columns are filled with None so callers need not carry all eight.
    """
    if df.height == 0:
        return
    import polars as pl  # noqa: PLC0415

    missing = [c for c in _NODE_COLS if c not in df.columns]
    if missing:
        df = df.with_columns([pl.lit(None).alias(c) for c in missing])
    rows = df.select(_NODE_COLS).to_dicts()
    stage_and_merge(
        self._conn, "identity_nodes", rows, ["entity_id"],
        update_cols=_NODE_UPDATE, json_cols=["golden_record"],
        database=self._database, schema=self._schema,
    )


@contextlib.contextmanager
def bulk_writes(self):
    """One transaction around a batch of writes.

    Snowflake autocommits per statement otherwise, so a per-record resolve pays
    a commit per write -- the same cost ``bulk_writes`` was added to remove on
    Postgres (#1886) and SQLite (#2105).
    """
    execute(self._conn, "BEGIN")
    try:
        yield
    except BaseException:
        execute(self._conn, "ROLLBACK")
        raise
    execute(self._conn, "COMMIT")


def bulk_flush_checkpoint(self) -> None:
    """No-op: there is no client-side accumulator to flush."""
    return None
```

Implement `bulk_upsert_records`, `bulk_add_edges` and `bulk_emit_events` the same way, taking their column lists from `store.py:1090`, `1175` and `1274` respectively. `bulk_add_edges` passes `update_cols=None` (insert-if-absent, matching `add_edge`); `bulk_emit_events` appends without a key, so it uses `write_pandas` straight into `identity_events` rather than `stage_and_merge`.

- [ ] **Step 4: Route the seams and gate**

In `store.py`, add a `snowflake` branch to each of the four `bulk_*` methods and to `bulk_writes` / `bulk_flush_checkpoint`. `write_pipeline` and `bulk_copy_barrier` need no change — their existing `if self._backend == "postgres"` guard already makes them a no-op for Snowflake. `initial_load_writes` likewise no-ops via its `and self._backend == "postgres"` condition.

Change the guard at the top of each `bulk_*` method from a Postgres-only raise to allow Snowflake, e.g. at `store.py:996`:

```python
    def bulk_upsert_identities(self, df: Any) -> None:
        if self._backend == "snowflake":
            self._sf.bulk_upsert_identities(df)
            return
        if self._backend != "postgres":
            raise NotImplementedError(...)  # unchanged
```

Then in `resolve.py`, replace lines 717-720:

```python
    _bulk_backend = getattr(store, "_backend", None)
    use_bulk_fast_path = (
        _bulk_backend in ("postgres", "sqlite") and _bulk_fast_path_enabled()
    )
```

with:

```python
    # Capability, not a backend-name allowlist: a backend opts into the staged
    # fast path by implementing bulk_*. Without this a Snowflake store takes
    # the per-row path and every write is a warehouse round-trip (#2699).
    use_bulk_fast_path = (
        getattr(store, "supports_bulk", False) and _bulk_fast_path_enabled()
    )
```

- [ ] **Step 5: Run the full identity suite to check for regressions**

The gate change affects SQLite and Postgres too, so run the existing suites, not just the new ones:

```bash
/d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest \
  packages/python/goldenmatch/tests/identity/ \
  packages/python/goldenmatch/tests/snowflake/ -v
```

Expected: all pass, including the pre-existing `tests/identity/` suite.

- [ ] **Step 6: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/identity/snowflake_backend.py \
        packages/python/goldenmatch/goldenmatch/identity/store.py \
        packages/python/goldenmatch/goldenmatch/identity/resolve.py \
        packages/python/goldenmatch/tests/snowflake/test_identity_bulk_snowflake.py
git commit -m "feat(identity): Snowflake bulk staged-MERGE path and a capability-based bulk gate"
```

---

### Task 8: Parity guardrail, dispatch coverage, config and docs

**Files:**
- Create: `packages/python/goldenmatch/tests/snowflake/test_snowflake_parity.py`
- Create: `packages/python/goldenmatch/tests/snowflake/test_snowflake_dispatch.py`
- Modify: `packages/python/goldenmatch/goldenmatch/config/schemas.py:2131-2155`
- Modify: `packages/python/goldenmatch/goldenmatch/snowflake/udfs.py` (module docstring, `scan_table` docstring, 3 tracking-issue paths)
- Modify: `packages/dbt/goldensuite/docs/snowflake-handlers.md`
- Modify: `packages/python/goldenmatch/CHANGELOG.md`

**Interfaces:**
- Consumes: everything.
- Produces: no new runtime API.

- [ ] **Step 1: Write the dispatch-coverage test**

This is the guardrail that stops a future method being added to `IdentityStore` without a Snowflake branch. Modelled on `tests/identity/test_mongo_dispatch.py`.

```python
"""Every public IdentityStore method must have a Snowflake branch."""
from __future__ import annotations

import inspect

import pytest

fakesnow = pytest.importorskip("fakesnow")

# Methods that legitimately have no Snowflake branch, with the reason.
_EXEMPT = {
    "write_pipeline",        # psycopg pipeline mode; no-ops via its own guard
    "bulk_copy_barrier",     # suspends a psycopg pipeline; nothing to suspend
    "initial_load_writes",   # Postgres from-empty COPY path; no-ops via its guard
}


def test_every_public_method_dispatches_to_snowflake() -> None:
    from goldenmatch.identity.snowflake_backend import SnowflakeIdentityStore
    from goldenmatch.identity.store import IdentityStore

    missing = []
    for name, member in inspect.getmembers(IdentityStore, inspect.isfunction):
        if name.startswith("_") or name in _EXEMPT or name == "close":
            continue
        if not hasattr(SnowflakeIdentityStore, name):
            missing.append(name)
    assert missing == [], (
        f"SnowflakeIdentityStore is missing: {missing}. Add the method and its "
        f"dispatch branch, or add it to _EXEMPT with a reason."
    )


def test_signatures_match() -> None:
    from goldenmatch.identity.snowflake_backend import SnowflakeIdentityStore
    from goldenmatch.identity.store import IdentityStore

    mismatched = []
    for name, member in inspect.getmembers(IdentityStore, inspect.isfunction):
        if name.startswith("_") or name in _EXEMPT or name == "close":
            continue
        sf = getattr(SnowflakeIdentityStore, name, None)
        if sf is None:
            continue
        if inspect.signature(member) != inspect.signature(sf):
            mismatched.append(
                f"{name}: store{inspect.signature(member)} != "
                f"snowflake{inspect.signature(sf)}"
            )
    assert mismatched == [], "\n".join(mismatched)
```

- [ ] **Step 2: Write the cross-backend parity test**

```python
"""One fixture through sqlite and snowflake must produce identical objects."""
from __future__ import annotations

from dataclasses import asdict

import pytest

fakesnow = pytest.importorskip("fakesnow")
import snowflake.connector  # noqa: E402


def _seed(store, eid):
    from goldenmatch.identity.model import (
        EvidenceEdge, IdentityAlias, IdentityEvent, IdentityNode, SourceRecord,
    )

    store.upsert_identity(IdentityNode(
        entity_id=eid, dataset="c", status="active", confidence=0.91,
        golden_record={"name": "Ada Lovelace"},
    ))
    for pk in ("1", "2"):
        store.upsert_record(SourceRecord(
            record_id=f"crm:{pk}", source="crm", source_pk=pk,
            record_hash=f"h{pk}", entity_id=eid, dataset="c",
            payload={"email": f"{pk}@example.com"},
        ))
    store.add_edge(EvidenceEdge(
        entity_id=eid, record_a_id="crm:1", record_b_id="crm:2",
        kind="same_as", score=0.97, run_name="run-1", dataset="c",
    ))
    store.emit_event(IdentityEvent(
        entity_id=eid, kind="created", run_name="run-1", dataset="c",
    ))
    store.add_alias(IdentityAlias(alias="MDM-1", entity_id=eid, kind="mdm"))


def _drop_volatile(d: dict) -> dict:
    """Timestamps and generated ids differ between backends by construction."""
    return {
        k: v for k, v in d.items()
        if k not in {
            "created_at", "updated_at", "first_seen_at", "last_seen_at",
            "recorded_at", "edge_id", "event_id",
        }
    }


def test_sqlite_and_snowflake_produce_identical_objects(tmp_path) -> None:
    from goldenmatch.identity.store import IdentityStore, new_entity_id

    eid = new_entity_id()
    sqlite_store = IdentityStore(
        backend="sqlite", path=str(tmp_path / "identity.db")
    )
    _seed(sqlite_store, eid)

    with fakesnow.patch():
        conn = snowflake.connector.connect(database="GM", schema="PUB")
        sf_store = IdentityStore(
            backend="snowflake", connection=conn, database="GM", schema="PUB",
        )
        _seed(sf_store, eid)

        assert _drop_volatile(asdict(sqlite_store.get_identity(eid))) == \
               _drop_volatile(asdict(sf_store.get_identity(eid)))
        assert _drop_volatile(asdict(sqlite_store.get_record("crm:1"))) == \
               _drop_volatile(asdict(sf_store.get_record("crm:1")))
        a_edges = [_drop_volatile(asdict(e))
                   for e in sqlite_store.edges_for_entity(eid)]
        b_edges = [_drop_volatile(asdict(e))
                   for e in sf_store.edges_for_entity(eid)]
        assert a_edges == b_edges
        a_hist = [_drop_volatile(asdict(e)) for e in sqlite_store.history(eid)]
        b_hist = [_drop_volatile(asdict(e)) for e in sf_store.history(eid)]
        assert a_hist == b_hist
        assert sqlite_store.resolve_alias("MDM-1", kind="mdm") == \
               sf_store.resolve_alias("MDM-1", kind="mdm")
        sf_store.close()
    sqlite_store.close()
```

- [ ] **Step 3: Run both tests to verify they fail, then pass**

```bash
/d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest \
  packages/python/goldenmatch/tests/snowflake/test_snowflake_dispatch.py \
  packages/python/goldenmatch/tests/snowflake/test_snowflake_parity.py -v
```

If the dispatch test reports missing methods, implement them — do not add them to `_EXEMPT` unless there is a real reason like the three already listed.

- [ ] **Step 4: Update the config schema**

In `config/schemas.py`, at `IdentityConfig` (line 2131):

```python
    backend: str = Field(
        default="sqlite",
        description=(
            "Storage backend for the identity graph "
            "('sqlite', 'postgres', 'mongo' or 'snowflake')."
        ),
    )
    connection: str | dict | None = Field(
        default=None,
        description=(
            "Connection for non-sqlite backends: a DSN string for postgres, a "
            "URI for mongo, or an account name / connector-kwargs dict for "
            "snowflake. A Snowpark Session is passed programmatically, not "
            "through config."
        ),
    )
    schema_: str = Field(
        default="PUBLIC",
        alias="schema",
        description="Snowflake schema holding the identity tables.",
    )
```

`schema` collides with Pydantic's `BaseModel.schema`, hence the alias. Confirm the model has `populate_by_name=True` in its config; if not, add it.

- [ ] **Step 5: Fix the stale documentation**

In `snowflake/udfs.py`:
- Module docstring: replace "These ship in a follow-up PR once the Snowflake-native `MemoryStore` and `IdentityStore` backends land" with an accurate statement — the `IdentityStore` backend has landed, `correction_add` awaits the `MemoryStore` one, and the other five were never blocked on a store at all.
- `scan_table` docstring: delete the claim that `scanner.scan_file` "currently expects a file path" and point at `scanner.scan_dataframe`.
- All three "Tracking issue: `docs/snowflake-handlers.md`" references: correct the path to `packages/dbt/goldensuite/docs/snowflake-handlers.md`.
- Module docstring: the `goldenmatch snowflake init` CLI does not exist yet; say so rather than describing it in the present tense.

In `packages/dbt/goldensuite/docs/snowflake-handlers.md`:
- Correct "PR #553 shipped the outside" — the dbt macros shipped; `cli/snowflake.py` did not.
- Update the Phase 2 deferral table: `correction_add` waits on the MemoryStore backend; `scan_table` / `health_score` are no longer blocked; the dedupe handlers wait on the registration CLI.
- Add a Snowflake row to the identity-backend documentation.

- [ ] **Step 6: Update the CHANGELOG**

Add under the unreleased heading in `packages/python/goldenmatch/CHANGELOG.md`:

```markdown
- **Snowflake-native `IdentityStore` backend (#2699).** `IdentityStore(backend="snowflake")`
  keeps the identity graph in Snowflake tables rather than a SQLite file, so it
  survives a UDF / stored-procedure worker. Writes are `MERGE`-based: Snowflake
  does not enforce `PRIMARY KEY` or `UNIQUE`, so a replayed run would otherwise
  duplicate rows. The `bulk_*` fast path stages through `write_pandas` and
  `MERGE`, and `resolve_clusters` now selects it by capability
  (`store.supports_bulk`) rather than by backend name.
```

- [ ] **Step 7: Regenerate the codemap**

New public symbols otherwise turn two checks red.

```bash
/d/show_case/goldenmatch/.venv/Scripts/python.exe scripts/generate_agent_codemap.py
```

Confirm the script's real name first with `ls scripts/ | grep -i codemap`; run whatever it is, and commit `docs/agent-codemap.json`.

- [ ] **Step 8: Run the whole affected surface**

```bash
/d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest \
  packages/python/goldenmatch/tests/identity/ \
  packages/python/goldenmatch/tests/snowflake/ \
  packages/python/goldenmatch/tests/test_config_schemas.py -v
```

Do not run the full suite locally — it OOMs under xdist. CI covers it.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "test(snowflake): parity and dispatch guardrails; docs and config for the Snowflake backend"
```

---

### Task 9: The live-Snowflake gate

**Files:**
- Create: `packages/python/goldenmatch/tests/snowflake/conftest.py`
- Modify: every `tests/snowflake/test_*.py` written in Tasks 1-8 (delete their local fixtures)

**Interfaces:**
- Consumes: nothing.
- Produces: a shared `sf_conn` fixture and a shared `store` fixture, both of which run against `fakesnow` by default and against live Snowflake when `GOLDENMATCH_SNOWFLAKE_TEST_DSN` is set.

The spec requires this and nothing in Tasks 1-8 provides it. Doing it last, as a refactor, means the fixtures being lifted already exist and are known-good.

- [ ] **Step 1: Write the failing test**

```python
def test_live_mode_is_off_without_the_env_var(monkeypatch) -> None:
    monkeypatch.delenv("GOLDENMATCH_SNOWFLAKE_TEST_DSN", raising=False)
    from tests.snowflake.conftest import live_dsn

    assert live_dsn() is None


def test_live_mode_reads_the_env_var(monkeypatch) -> None:
    monkeypatch.setenv("GOLDENMATCH_SNOWFLAKE_TEST_DSN", "myacct")
    from tests.snowflake.conftest import live_dsn

    assert live_dsn() == "myacct"
```

- [ ] **Step 2: Run to verify it fails**

Expected: FAIL — `ModuleNotFoundError: No module named 'tests.snowflake.conftest'`

- [ ] **Step 3: Write the conftest**

```python
"""Shared Snowflake test fixtures.

Every suite here runs against ``fakesnow`` by default. Setting
``GOLDENMATCH_SNOWFLAKE_TEST_DSN`` runs the SAME suites against a live
warehouse instead -- which is the only way to check the three things a DuckDB
fake can get wrong: MERGE semantics, VARIANT round-tripping, and constraint
non-enforcement (DuckDB *does* enforce primary keys, so a duplicate-insert bug
can pass here and fail there, or the reverse).

Live runs create and drop real tables. Point the DSN at a scratch schema.
"""
from __future__ import annotations

import contextlib
import os
import uuid
from typing import Any

import pytest


def live_dsn() -> str | None:
    """The live-Snowflake account/DSN, or None for the fakesnow default."""
    return os.environ.get("GOLDENMATCH_SNOWFLAKE_TEST_DSN") or None


@contextlib.contextmanager
def _connection() -> Any:
    dsn = live_dsn()
    if dsn is None:
        fakesnow = pytest.importorskip("fakesnow")
        import snowflake.connector  # noqa: PLC0415

        with fakesnow.patch():
            conn = snowflake.connector.connect(database="GM", schema="PUB")
            try:
                yield conn, "GM", "PUB"
            finally:
                conn.close()
        return

    import snowflake.connector  # noqa: PLC0415

    from goldenmatch.snowflake._store_sql import execute, resolve_connection

    # A unique schema per session so a live run cannot collide with itself or
    # leave the previous run's rows behind -- the same reasoning as the uuid4
    # stage-table suffix.
    schema = f"GM_TEST_{uuid.uuid4().hex[:8]}".upper()
    database = os.environ.get("SNOWFLAKE_DATABASE", "GOLDENMATCH")
    conn = resolve_connection(dsn, database=database, schema=schema)
    execute(conn, f"CREATE SCHEMA IF NOT EXISTS {database}.{schema}")
    try:
        yield conn, database, schema
    finally:
        execute(conn, f"DROP SCHEMA IF EXISTS {database}.{schema} CASCADE")
        conn.close()


@pytest.fixture
def sf_conn():
    with _connection() as (conn, _db, _schema):
        yield conn


@pytest.fixture
def sf_target():
    """(conn, database, schema) for tests that need the qualified names."""
    with _connection() as triple:
        yield triple


@pytest.fixture
def store(sf_target):
    from goldenmatch.identity.store import IdentityStore

    conn, database, schema = sf_target
    s = IdentityStore(
        backend="snowflake", connection=conn,
        database=database, schema=schema,
    )
    yield s
    s.close()
```

- [ ] **Step 4: Lift the local fixtures**

Delete the `sf_conn` fixture from `test_store_sql.py` and the `store` fixture
from `test_identity_store_snowflake.py`, and delete the
`from tests.snowflake.test_identity_store_snowflake import store` imports in
`test_identity_extras_snowflake.py` and `test_identity_bulk_snowflake.py` —
pytest resolves `conftest.py` fixtures automatically. Replace every hard-coded
`database="GM", schema="PUB"` in the suites with the `sf_target` fixture's
values, or the live run will write to the wrong schema.

`test_snowflake_parity.py` opens its own connection inside a `fakesnow.patch()`
block; convert it to the `sf_target` fixture too.

- [ ] **Step 5: Run the suite both ways**

Default (fake):

```bash
/d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest \
  packages/python/goldenmatch/tests/snowflake/ -v
```

Expected: all pass, unchanged from Task 8.

Live (only if the maintainer supplies credentials — do not run this against an
account without being told to):

```bash
GOLDENMATCH_SNOWFLAKE_TEST_DSN=<account> \
/d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest \
  packages/python/goldenmatch/tests/snowflake/ -v
```

- [ ] **Step 6: Commit**

```bash
git add packages/python/goldenmatch/tests/snowflake/
git commit -m "test(snowflake): shared fixtures with a GOLDENMATCH_SNOWFLAKE_TEST_DSN live gate"
```

---

## Verification

Before opening the PR:

1. `git log --oneline origin/main..HEAD` shows nine task commits plus the spec.
2. `tests/snowflake/` passes in full.
3. `tests/identity/` still passes — Task 7 changed a gate that SQLite and Postgres also cross.
4. `ruff check packages/python/goldenmatch/goldenmatch/` is clean on the touched files.
5. The dispatch test's `_EXEMPT` set contains only the three documented entries.

## Known limitations to state in the PR body

- Verified against `fakesnow`, not live Snowflake. `MERGE` semantics, `VARIANT`
  round-tripping and constraint non-enforcement are precisely where a DuckDB
  fake can diverge — and it can diverge in both directions, since DuckDB *does*
  enforce primary keys. The `GOLDENMATCH_SNOWFLAKE_TEST_DSN` suite is the answer
  and has not been run.
- `initial_load_writes` does not engage for Snowflake; the from-empty fast path
  has no Snowflake implementation.
- No throughput measurement. Nothing in this plan justifies a performance claim.
