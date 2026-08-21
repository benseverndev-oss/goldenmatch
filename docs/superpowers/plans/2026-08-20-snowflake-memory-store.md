# Snowflake-native MemoryStore Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `MemoryStore(backend="snowflake")` so corrections and learned adjustments persist in Snowflake tables, unblocking the `correction_add` stored procedure.

**Architecture:** Reuses the `_store_sql.py` layer built by the IdentityStore plan verbatim — connection resolution, `CaseInsensitiveRow`, `ensure_schema`, `merge_one`, `stage_and_merge`. `SnowflakeMemoryStore` implements the twelve-method surface and is reached through per-method `if self._backend == "snowflake"` early returns in `MemoryStore`.

**Tech Stack:** Python 3.12/3.13, `snowflake-connector-python>=3.0`, `fakesnow`, pytest.

**Spec:** `docs/superpowers/specs/2026-08-20-snowflake-native-stores-design.md`

**Depends on:** `docs/superpowers/plans/2026-08-20-snowflake-identity-store.md` Tasks 1-3, which create `_store_sql.py`. Do not start this plan until those three tasks are merged.

## Global Constraints

Identical to the IdentityStore plan — read its Global Constraints section and apply it here in full. The two that bite hardest in this plan:

- **Snowflake enforces only `NOT NULL`.** `add_correction` and `save_adjustment` both rely on upsert semantics; both must be a `MERGE`.
- **`table_prefix` applies on Snowflake, as it does on Postgres.** `MemoryStore.__init__` (`core/memory/store.py:164-195`) applies the validated prefix for Postgres and bare names for SQLite, because SQLite's DDL hardcodes bare names. Snowflake follows the Postgres branch: a shared warehouse schema is exactly the multi-tenant case the prefix exists for. The prefix is regex-validated at `store.py:170`; keep that validation on the Snowflake path.

---

### Task 1: `MEMORY_DDL` and `SnowflakeMemoryStore` construction

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/snowflake/_store_sql.py`
- Create: `packages/python/goldenmatch/goldenmatch/core/memory/snowflake_backend.py`
- Create: `packages/python/goldenmatch/tests/snowflake/test_memory_store_snowflake.py`
- Modify: `packages/python/goldenmatch/goldenmatch/core/memory/store.py:164-240`

**Interfaces:**
- Consumes: `resolve_connection`, `ensure_schema`, `execute`, `fetchone_row`, `fetchall_rows` from `_store_sql.py`.
- Produces:
  - `MEMORY_DDL: str` — parameterised on the table prefix via `MEMORY_DDL_TEMPLATE.format(prefix=...)`
  - `class SnowflakeMemoryStore` with `__init__(self, connection: Any = None, *, database: str = "GOLDENMATCH", schema: str = "PUBLIC", table_prefix: str = "")` and `close()`

- [ ] **Step 1: Write the failing test**

Create `packages/python/goldenmatch/tests/snowflake/test_memory_store_snowflake.py`:

```python
"""MemoryStore(backend="snowflake") against fakesnow."""
from __future__ import annotations

import pytest

fakesnow = pytest.importorskip("fakesnow")
import snowflake.connector  # noqa: E402


@pytest.fixture
def store():
    from goldenmatch.core.memory.store import MemoryStore

    with fakesnow.patch():
        conn = snowflake.connector.connect(database="GM", schema="PUB")
        s = MemoryStore(
            backend="snowflake", connection=conn, database="GM", schema="PUB",
        )
        yield s
        s.close()


def test_opens_and_creates_tables(store) -> None:
    from goldenmatch.snowflake._store_sql import fetchall_rows

    rows = fetchall_rows(
        store._sf._conn,
        "SELECT table_name FROM GM.information_schema.tables "
        "WHERE table_schema = %s",
        ("PUB",),
    )
    names = {r["table_name"].lower() for r in rows}
    assert "corrections" in names
    assert "adjustments" in names


def test_table_prefix_is_applied() -> None:
    from goldenmatch.core.memory.store import MemoryStore
    from goldenmatch.snowflake._store_sql import fetchall_rows

    with fakesnow.patch():
        conn = snowflake.connector.connect(database="GM", schema="PUB")
        s = MemoryStore(
            backend="snowflake", connection=conn, database="GM",
            schema="PUB", table_prefix="tenant1_",
        )
        rows = fetchall_rows(
            s._sf._conn,
            "SELECT table_name FROM GM.information_schema.tables "
            "WHERE table_schema = %s",
            ("PUB",),
        )
        names = {r["table_name"].lower() for r in rows}
        assert "tenant1_corrections" in names
        s.close()


def test_invalid_table_prefix_is_rejected() -> None:
    from goldenmatch.core.memory.store import MemoryStore

    with fakesnow.patch():
        conn = snowflake.connector.connect(database="GM", schema="PUB")
        with pytest.raises(ValueError, match="table_prefix"):
            MemoryStore(
                backend="snowflake", connection=conn,
                table_prefix="drop table;--",
            )
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
export PYTHONPATH="D:/show_case/gm-snowflake/packages/python/goldenmatch"
export GOLDENMATCH_NATIVE=0
/d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest \
  packages/python/goldenmatch/tests/snowflake/test_memory_store_snowflake.py -v
```

Expected: FAIL — `NotImplementedError: Backend 'snowflake' not yet implemented`

- [ ] **Step 3: Add `MEMORY_DDL_TEMPLATE` to `_store_sql.py`**

Translate `_SCHEMA` from `core/memory/store.py` (the `corrections` table at line 131 and `adjustments` at line 153) using the same type mapping as `IDENTITY_DDL`. Parameterise the names:

```python
MEMORY_DDL_TEMPLATE = """
CREATE TABLE IF NOT EXISTS {prefix}corrections (
    correction_id  STRING PRIMARY KEY,
    -- ... every column from core/memory/store.py's _SCHEMA, translated
);
CREATE TABLE IF NOT EXISTS {prefix}adjustments (
    -- ... likewise
);
"""
```

Read the real column lists from the source; do not leave the ellipsis comments in place. Run `_migrate_field_correction_columns` (line 192) and `_migrate_cluster_decision_columns` (line 219) as reading material — the Snowflake DDL must include the columns those migrations add, since a fresh Snowflake schema is created at the current version and never migrates from an older one.

- [ ] **Step 4: Write `SnowflakeMemoryStore.__init__`**

Create `core/memory/snowflake_backend.py`:

```python
"""MemoryStore backed by Snowflake tables.

Reached through ``MemoryStore(backend="snowflake")``. Shares the SQL plumbing
in ``goldenmatch.snowflake._store_sql`` with the identity backend.

Like the Postgres path, this applies ``table_prefix`` so several tenants can
share one warehouse schema. Unlike SQLite, whose DDL hardcodes bare names.
"""
from __future__ import annotations

import re
from typing import Any

from goldenmatch.snowflake._store_sql import (
    MEMORY_DDL_TEMPLATE,
    ensure_schema,
    resolve_connection,
)


class SnowflakeMemoryStore:
    def __init__(
        self,
        connection: Any = None,
        *,
        database: str = "GOLDENMATCH",
        schema: str = "PUBLIC",
        table_prefix: str = "",
    ) -> None:
        if table_prefix and not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*", table_prefix
        ):
            raise ValueError(
                f"table_prefix must match ^[A-Za-z_][A-Za-z0-9_]*$; "
                f"got {table_prefix!r}"
            )
        self._database = database
        self._schema = schema
        self._corrections = f"{table_prefix}corrections"
        self._adjustments = f"{table_prefix}adjustments"
        self._conn = resolve_connection(
            connection, database=database, schema=schema
        )
        ensure_schema(
            self._conn,
            MEMORY_DDL_TEMPLATE.format(prefix=table_prefix),
            database=database, schema=schema, version=1,
        )

    def close(self) -> None:
        self._conn.close()
```

`ensure_schema` stamps `_gm_schema_version` under the component name it is given; extend its signature to take `component: str = "identity"` and pass `component="memory"` here, so the identity and memory versions do not overwrite each other.

- [ ] **Step 5: Wire it into `MemoryStore.__init__`**

Add `database: str = "GOLDENMATCH"` and `schema: str = "PUBLIC"` to the signature. Before the `if backend == "sqlite":` branch:

```python
        self._sf: Any = None
        if backend == "snowflake":
            from goldenmatch.core.memory.snowflake_backend import (  # noqa: PLC0415
                SnowflakeMemoryStore,
            )
            self._sf = SnowflakeMemoryStore(
                connection=connection, database=database, schema=schema,
                table_prefix=table_prefix,
            )
            return
```

Note that the `table_prefix` validation now happens twice — once in `MemoryStore.__init__` at line 170 and once in the backend. That is deliberate: the backend is also constructible directly, and the check is cheap.

Extend `close()` with a `snowflake` branch.

- [ ] **Step 6: Run the test to verify it passes**

```bash
/d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest \
  packages/python/goldenmatch/tests/snowflake/test_memory_store_snowflake.py -v
```

Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/snowflake/_store_sql.py \
        packages/python/goldenmatch/goldenmatch/core/memory/snowflake_backend.py \
        packages/python/goldenmatch/goldenmatch/core/memory/store.py \
        packages/python/goldenmatch/tests/snowflake/test_memory_store_snowflake.py
git commit -m "feat(memory): Snowflake MemoryStore construction and schema"
```

---

### Task 2: Corrections

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/memory/snowflake_backend.py`
- Modify: `packages/python/goldenmatch/goldenmatch/core/memory/store.py` (methods at 351-586)
- Modify: `packages/python/goldenmatch/tests/snowflake/test_memory_store_snowflake.py`

**Interfaces:**
- Consumes: Task 1's `SnowflakeMemoryStore`.
- Produces: `add_correction(correction)`, `record_cluster_decision(...)`, `get_pair_correction(...)`, `get_pair_corrections_bulk(...)`, `get_corrections(dataset)`, `count_corrections(dataset)`, `corrections_since(since)` — signatures copied verbatim from `MemoryStore`.

- [ ] **Step 1: Write the failing test**

Append to `tests/snowflake/test_memory_store_snowflake.py`:

```python
def _correction(**kw):
    from goldenmatch.core.memory.store import Correction

    base = dict(
        record_a="crm:1", record_b="crm:2", decision="approve",
        source="steward", dataset="customers",
    )
    base.update(kw)
    return Correction(**base)


def test_add_correction_and_read_back(store) -> None:
    store.add_correction(_correction())
    assert store.count_corrections(dataset="customers") == 1
    got = store.get_corrections(dataset="customers")
    assert len(got) == 1
    assert got[0].decision == "approve"
    assert got[0].source == "steward"


def test_add_correction_is_idempotent_on_replay(store) -> None:
    """Snowflake does not enforce the correction primary key."""
    correction = _correction()
    store.add_correction(correction)
    store.add_correction(correction)
    assert store.count_corrections() == 1


def test_get_pair_correction_finds_either_order(store) -> None:
    store.add_correction(_correction())
    assert store.get_pair_correction("crm:1", "crm:2") is not None
    assert store.get_pair_correction("crm:2", "crm:1") is not None
    assert store.get_pair_correction("crm:1", "crm:9") is None


def test_get_pair_corrections_bulk(store) -> None:
    store.add_correction(_correction())
    store.add_correction(_correction(record_a="crm:3", record_b="crm:4",
                                     decision="reject"))
    got = store.get_pair_corrections_bulk([("crm:1", "crm:2"),
                                           ("crm:3", "crm:4")])
    assert len(got) == 2


def test_corrections_since_filters_by_time(store) -> None:
    from datetime import datetime, timedelta

    store.add_correction(_correction())
    future = datetime.now() + timedelta(days=1)
    past = datetime.now() - timedelta(days=1)
    assert store.corrections_since(past) != []
    assert store.corrections_since(future) == []
```

Before implementing, read `Correction` at `core/memory/store.py:61` to confirm the constructor's real field names, and `get_pair_correction` at line 514 to confirm how pair order is canonicalized. Correct the test to match the source if it differs — the source is authoritative.

- [ ] **Step 2: Run the test to verify it fails**

```bash
/d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest \
  packages/python/goldenmatch/tests/snowflake/test_memory_store_snowflake.py -v -k correction
```

Expected: FAIL — `AttributeError: 'SnowflakeMemoryStore' object has no attribute 'add_correction'`

- [ ] **Step 3: Implement the seven methods**

| Method | Source | Snowflake port |
|---|---|---|
| `add_correction(correction)` | 351 | `merge_one` on the corrections table keyed by `correction_id`, `update_cols` covering the mutable fields. JSON-valued columns go in `json_cols`. |
| `record_cluster_decision(...)` | 443 | builds a `Correction` with `decision="cluster_decision"` and delegates to `add_correction` |
| `get_pair_correction(a, b)` | 514 | `SELECT ... WHERE (record_a = %s AND record_b = %s) OR (record_a = %s AND record_b = %s) ORDER BY created_at DESC LIMIT 1` |
| `get_pair_corrections_bulk(pairs)` | 531 | chunked `OR`-of-pairs, same 900-element chunking as `lookup_entity_ids` |
| `get_corrections(dataset)` | 544 | optional dataset filter, `ORDER BY created_at` |
| `count_corrections(dataset)` | 556 | `SELECT COUNT(*) AS n` |
| `corrections_since(since)` | 570 | `WHERE created_at >= %s` |

Map rows back with `MemoryStore._row_to_correction` (line 457), imported lazily to avoid a circular import.

- [ ] **Step 4: Add the seven dispatch branches**

Each as the first statement of the corresponding `MemoryStore` method:

```python
    def add_correction(self, correction: Correction) -> None:
        if self._backend == "snowflake":
            self._sf.add_correction(correction)
            return
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
/d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest \
  packages/python/goldenmatch/tests/snowflake/test_memory_store_snowflake.py -v
```

Expected: 8 passed.

- [ ] **Step 6: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/memory/snowflake_backend.py \
        packages/python/goldenmatch/goldenmatch/core/memory/store.py \
        packages/python/goldenmatch/tests/snowflake/test_memory_store_snowflake.py
git commit -m "feat(memory): Snowflake corrections read/write path"
```

---

### Task 3: Learned adjustments, parity and dispatch coverage

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/memory/snowflake_backend.py`
- Modify: `packages/python/goldenmatch/goldenmatch/core/memory/store.py` (methods at 587-680)
- Create: `packages/python/goldenmatch/tests/snowflake/test_memory_parity.py`

**Interfaces:**
- Consumes: Tasks 1-2.
- Produces: `save_adjustment(adj)`, `get_adjustment(matchkey_name)`, `get_all_adjustments()`, `last_learn_time()`.

- [ ] **Step 1: Write the failing test**

Append to `tests/snowflake/test_memory_store_snowflake.py`:

```python
def _adjustment(**kw):
    from goldenmatch.core.memory.store import LearnedAdjustment

    base = dict(matchkey_name="name_email", threshold_delta=0.05)
    base.update(kw)
    return LearnedAdjustment(**base)


def test_save_and_get_adjustment(store) -> None:
    store.save_adjustment(_adjustment())
    got = store.get_adjustment("name_email")
    assert got is not None
    assert got.matchkey_name == "name_email"


def test_save_adjustment_overwrites_in_place(store) -> None:
    store.save_adjustment(_adjustment(threshold_delta=0.05))
    store.save_adjustment(_adjustment(threshold_delta=0.20))
    assert len(store.get_all_adjustments()) == 1
    got = store.get_adjustment("name_email")
    assert got is not None
    assert got.threshold_delta == 0.20


def test_last_learn_time_is_none_when_empty(store) -> None:
    assert store.last_learn_time() is None


def test_last_learn_time_after_save(store) -> None:
    store.save_adjustment(_adjustment())
    assert store.last_learn_time() is not None
```

Read `LearnedAdjustment` at `core/memory/store.py:115` first and correct the constructor kwargs above to its real fields.

Then create `tests/snowflake/test_memory_parity.py`, modelled on the identity parity test — seed one correction and one adjustment into both a SQLite and a Snowflake `MemoryStore`, and assert the `dataclasses.asdict` forms match after dropping timestamp and generated-id fields.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
/d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest \
  packages/python/goldenmatch/tests/snowflake/test_memory_store_snowflake.py \
  packages/python/goldenmatch/tests/snowflake/test_memory_parity.py -v
```

Expected: FAIL — `AttributeError: ... 'save_adjustment'`

- [ ] **Step 3: Implement the four methods**

| Method | Source | Snowflake port |
|---|---|---|
| `save_adjustment(adj)` | 587 | `merge_one` keyed by `matchkey_name`, `update_cols` covering every other column — this is an upsert, not insert-if-absent |
| `get_adjustment(matchkey_name)` | 623 | `SELECT * ... WHERE matchkey_name = %s` |
| `get_all_adjustments()` | 648 | `SELECT * ... ORDER BY matchkey_name` |
| `last_learn_time()` | 666 | `SELECT MAX(learned_at) AS t`; return `None` when the table is empty |

Map rows with `MemoryStore._row_to_adjustment` (line 480).

- [ ] **Step 4: Add a dispatch-coverage test**

Create the memory equivalent of the identity dispatch test, asserting every public `MemoryStore` method exists on `SnowflakeMemoryStore` with a matching signature. There are no exemptions for this store — all twelve methods must be implemented.

```python
def test_every_public_method_dispatches_to_snowflake() -> None:
    import inspect

    from goldenmatch.core.memory.snowflake_backend import SnowflakeMemoryStore
    from goldenmatch.core.memory.store import MemoryStore

    missing = [
        name
        for name, _ in inspect.getmembers(MemoryStore, inspect.isfunction)
        if not name.startswith("_")
        and name != "close"
        and not hasattr(SnowflakeMemoryStore, name)
    ]
    assert missing == [], f"SnowflakeMemoryStore is missing: {missing}"
```

- [ ] **Step 5: Add the four dispatch branches, then run everything**

```bash
/d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest \
  packages/python/goldenmatch/tests/snowflake/ \
  packages/python/goldenmatch/tests/test_memory_store.py -v
```

Expected: all pass, including the pre-existing `test_memory_store.py`.

- [ ] **Step 6: Update docs and commit**

Add a Snowflake row to the MemoryStore backend documentation, and a CHANGELOG entry:

```markdown
- **Snowflake-native `MemoryStore` backend (#2699).** `MemoryStore(backend="snowflake")`
  keeps corrections and learned adjustments in Snowflake tables, honouring
  `table_prefix` as the Postgres backend does. Unblocks the `correction_add`
  stored procedure.
```

```bash
git add -A
git commit -m "feat(memory): Snowflake learned adjustments, parity and dispatch guardrails"
```

---

## Verification

1. `tests/snowflake/` passes in full.
2. `tests/test_memory_store.py` still passes.
3. The dispatch test reports no missing methods.
4. `ruff check` clean on the touched files.

## Known limitations to state in the PR body

Same as the IdentityStore plan: verified against `fakesnow`, not live Snowflake,
and the `GOLDENMATCH_SNOWFLAKE_TEST_DSN` suite has not been run.
