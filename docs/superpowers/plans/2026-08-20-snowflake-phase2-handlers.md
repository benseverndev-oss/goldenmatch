# Snowflake Phase 2 Handlers and Registration CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the six `NotImplementedError` scaffolds in `goldenmatch/snowflake/udfs.py` with working handlers, and ship the `goldenmatch snowflake init` registration CLI the docs already describe.

**Architecture:** Every handler takes a Snowpark `Session` as its first argument, reads its input relation through that Session, and calls the existing in-process API (`MemoryStore`, `scan_dataframe`, `dedupe_df`). `packages/dbt/goldensuite/spcs/server.py` delegates to the same six functions, so one implementation serves both the stored-procedure and the SPCS container surfaces. The registration CLI emits and optionally executes the DDL, and stages the wheels.

**Tech Stack:** Python 3.12/3.13, `snowflake-snowpark-python`, `snowflake-connector-python`, `fakesnow`, click, pytest.

**Spec:** `docs/superpowers/specs/2026-08-20-snowflake-native-stores-design.md`

**Depends on:** the MemoryStore plan (Task 2 of this plan calls `MemoryStore(backend="snowflake")`). Tasks 1 and 3-5 have no such dependency and can proceed in parallel with it.

## Global Constraints

- **Handlers are stored procedures, not UDTFs.** A UDTF handler's `process()` is never given a `Session`, which is exactly why these six were deferred. This means the DDL in `packages/dbt/goldensuite/docs/snowflake-setup.md:95-142` — which currently declares `goldenmatch_dedupe_full` / `_clusters` / `_pairs` as `CREATE OR REPLACE FUNCTION ... RETURNS TABLE(...)` — is wrong for this design and must be rewritten as `CREATE OR REPLACE PROCEDURE`. Same for `goldencheck_scan_table` and `goldencheck_health_score` at lines 149-166.
- **The SPCS route table mirrors those SQL names** (`spcs/server.py`, route-table comment). Any rename must land in both places in the same commit, or the container and the warehouse diverge.
- **No throughput claim.** A stored procedure runs on one node. Say so; do not benchmark it and do not imply distribution.
- **Snowflake compute is aarch64**, and a `.whl` is a zip from which `zipimport` cannot `dlopen` a `.so`. Wheels must be extracted to a **unique** temp dir at module scope and prepended to `sys.path`. A fixed path lets a second worker `dlopen` a half-written `.so` and take a bus error on an mmap of a truncated file (#2699).
- **Test environment (worktree):** as in the IdentityStore plan.

---

### Task 1: The Session-bound handler seam

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/snowflake/udfs.py`
- Create: `packages/python/goldenmatch/tests/snowflake/test_phase2_handlers.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `_session_frame(session: Any, relation_name: str) -> Any` — reads a relation into a Polars frame
  - `_session_from_env() -> Any` — builds a Session for the SPCS path

Both handlers and tests need a way to turn "a Session plus a relation name" into a frame, and it must be fakeable. Building it first means the five handler tasks are each a thin call.

- [ ] **Step 1: Write the failing test**

Create `packages/python/goldenmatch/tests/snowflake/test_phase2_handlers.py`:

```python
"""Phase 2 stored-procedure handlers."""
from __future__ import annotations

import pytest

pl = pytest.importorskip("polars")


class FakeSession:
    """Minimal Snowpark Session stand-in.

    Snowpark itself is not installed in CI (it drags a large dependency tree),
    and the handlers only use ``session.table(name).to_pandas()``. Faking that
    surface keeps the handler logic under test without the dependency.
    """

    def __init__(self, frames: dict):
        self._frames = frames

    def table(self, name: str):
        return _FakeTable(self._frames[name])


class _FakeTable:
    def __init__(self, pdf):
        self._pdf = pdf

    def to_pandas(self):
        return self._pdf


@pytest.fixture
def session():
    import pandas as pd

    return FakeSession({
        "CUSTOMERS": pd.DataFrame({
            "id": [1, 2, 3],
            "name": ["Ada Lovelace", "Ada Lovelace", "Grace Hopper"],
            "email": ["ada@x.com", "ada@x.com", "grace@y.com"],
        })
    })


def test_session_frame_returns_a_polars_frame(session) -> None:
    from goldenmatch.snowflake.udfs import _session_frame

    df = _session_frame(session, "CUSTOMERS")
    assert isinstance(df, pl.DataFrame)
    assert df.height == 3
    assert set(df.columns) == {"id", "name", "email"}


def test_session_frame_raises_a_clear_error_for_a_missing_relation(
    session,
) -> None:
    from goldenmatch.snowflake.udfs import _session_frame

    with pytest.raises(KeyError):
        _session_frame(session, "NOPE")
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
export PYTHONPATH="D:/show_case/gm-snowflake/packages/python/goldenmatch"
export GOLDENMATCH_NATIVE=0
/d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest \
  packages/python/goldenmatch/tests/snowflake/test_phase2_handlers.py -v
```

Expected: FAIL — `ImportError: cannot import name '_session_frame'`

- [ ] **Step 3: Implement the seam**

Add to `udfs.py`, below the existing `_import_dir` helper:

```python
def _session_frame(session: Any, relation_name: str) -> Any:
    """Read a Snowflake relation into a Polars frame via a Snowpark Session.

    Stored procedures are handed a Session; UDFs are not, which is why the
    Phase 2 handlers are procedures. Going through pandas rather than
    ``to_arrow`` keeps this working against the Snowpark versions that predate
    Arrow-native collection.
    """
    pl = _polars()
    return pl.from_pandas(session.table(relation_name).to_pandas())


def _session_from_env() -> Any:
    """Build a Snowpark Session from SNOWFLAKE_* env vars.

    Used by the SPCS container path, which has no Session handed to it. Inside
    a stored procedure the Session arrives as the first argument and this is
    never called.
    """
    from snowflake.snowpark import Session  # noqa: PLC0415

    cfg = {
        k: os.environ[f"SNOWFLAKE_{k.upper()}"]
        for k in ("account", "user", "password", "database", "schema",
                  "warehouse", "role")
        if f"SNOWFLAKE_{k.upper()}" in os.environ
    }
    return Session.builder.configs(cfg).create()
```

- [ ] **Step 4: Run the test to verify it passes**

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/snowflake/udfs.py \
        packages/python/goldenmatch/tests/snowflake/test_phase2_handlers.py
git commit -m "feat(snowflake): Session-bound relation-read seam for the Phase 2 handlers"
```

---

### Task 2: `correction_add`

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/snowflake/udfs.py:314-337`
- Modify: `packages/dbt/goldensuite/spcs/server.py:193-206`
- Modify: `packages/python/goldenmatch/tests/snowflake/test_phase2_handlers.py`

**Interfaces:**
- Consumes: `MemoryStore(backend="snowflake")` from the MemoryStore plan; `_session_frame` from Task 1.
- Produces: `correction_add(session, decision: str, dataset: str, args_json: str) -> str`

The signature changes: `memory_path` disappears (there is no path — the store is warehouse tables) and `session` is prepended.

- [ ] **Step 1: Write the failing test**

```python
def test_correction_add_writes_to_the_memory_store() -> None:
    import json

    import snowflake.connector

    fakesnow = pytest.importorskip("fakesnow")
    from goldenmatch.core.memory.store import MemoryStore
    from goldenmatch.snowflake.udfs import correction_add

    with fakesnow.patch():
        conn = snowflake.connector.connect(database="GM", schema="PUB")

        class SessionWithConn:
            connection = conn

        correction_id = correction_add(
            SessionWithConn(), "approve", "customers",
            json.dumps({"record_a": "crm:1", "record_b": "crm:2",
                        "source": "steward"}),
        )
        assert isinstance(correction_id, str) and correction_id

        store = MemoryStore(
            backend="snowflake", connection=conn, database="GM", schema="PUB",
        )
        assert store.count_corrections(dataset="customers") == 1
        store.close()


def test_correction_add_rejects_an_unknown_decision() -> None:
    import snowflake.connector

    fakesnow = pytest.importorskip("fakesnow")
    from goldenmatch.snowflake.udfs import correction_add

    with fakesnow.patch():
        conn = snowflake.connector.connect(database="GM", schema="PUB")

        class SessionWithConn:
            connection = conn

        with pytest.raises(ValueError, match="decision"):
            correction_add(SessionWithConn(), "shrug", "customers", "{}")
```

- [ ] **Step 2: Run to verify it fails**

Expected: FAIL — `NotImplementedError: correction_add ships in Phase 2 ...`

- [ ] **Step 3: Implement**

```python
def correction_add(
    session: Any, decision: str, dataset: str, args_json: str
) -> str:
    """Stored Procedure: ``goldenmatch.goldenmatch_correction_add(...)``.

    Writes a Correction to the Snowflake-native MemoryStore and returns its
    UUID7. The ``memory_path`` argument the Phase 1 scaffold carried is gone:
    the store is warehouse tables reached through ``session``, not a SQLite
    file on a stage.
    """
    import json  # noqa: PLC0415

    from goldenmatch.core.memory.store import (  # noqa: PLC0415
        Correction, Decision, MemoryStore,
    )

    valid = {d.value for d in Decision}
    if decision not in valid:
        raise ValueError(
            f"decision must be one of {sorted(valid)}; got {decision!r}"
        )
    args = json.loads(args_json) if args_json else {}
    store = MemoryStore(backend="snowflake", connection=session)
    try:
        correction = Correction(decision=decision, dataset=dataset, **args)
        store.add_correction(correction)
        return correction.correction_id
    finally:
        store.close()
```

Confirm `Correction`'s id attribute name at `core/memory/store.py:61` and use the real one.

- [ ] **Step 4: Update the SPCS route**

In `spcs/server.py`, `_correction_add` must build its own Session:

```python
def _correction_add(decision: str, dataset: str, args_json: str) -> str:
    return _gm.correction_add(
        _gm._session_from_env(), decision, dataset, args_json,
    )
```

Its route entry loses the `memory_path` parameter. Update the route table to match.

- [ ] **Step 5: Run tests and commit**

```bash
/d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest \
  packages/python/goldenmatch/tests/snowflake/test_phase2_handlers.py -v
git add -A && git commit -m "feat(snowflake): implement correction_add against the Snowflake MemoryStore"
```

---

### Task 3: `scan_table` and `health_score`

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/snowflake/udfs.py:339-363`
- Modify: `packages/dbt/goldensuite/spcs/server.py:151-160`
- Modify: `packages/python/goldenmatch/tests/snowflake/test_phase2_handlers.py`

**Interfaces:**
- Consumes: `_session_frame` (Task 1); `goldencheck.engine.scanner.scan_dataframe`.
- Produces: `scan_table(session, relation_name: str, domain: str) -> str`, `health_score(session, relation_name: str) -> float`

Neither was ever blocked on a store. `scan_dataframe` (`goldencheck/engine/scanner.py:245`) already accepts a frame; the scaffold docstrings claiming otherwise are stale and are corrected in Task 6.

- [ ] **Step 1: Write the failing test**

```python
def test_scan_table_returns_json_findings(session) -> None:
    import json

    pytest.importorskip("goldencheck")
    from goldenmatch.snowflake.udfs import scan_table

    payload = json.loads(scan_table(session, "CUSTOMERS", ""))
    assert "findings" in payload
    assert "health_score" in payload
    assert isinstance(payload["findings"], list)


def test_health_score_is_a_float_in_range(session) -> None:
    pytest.importorskip("goldencheck")
    from goldenmatch.snowflake.udfs import health_score

    score = health_score(session, "CUSTOMERS")
    assert isinstance(score, float)
    assert 0.0 <= score <= 100.0
```

Check `DatasetProfile.health_score`'s real range before asserting `0..100` — if it is `0..1`, correct the test.

- [ ] **Step 2: Run to verify it fails**

Expected: FAIL — `NotImplementedError: scan_table ships in Phase 2 ...`

- [ ] **Step 3: Implement**

```python
def scan_table(session: Any, relation_name: str, domain: str) -> str:
    """Stored Procedure: ``goldenmatch.goldencheck_scan_table(...)``.

    Reads the relation through the Session and hands the frame straight to
    ``goldencheck.engine.scanner.scan_dataframe`` -- no CSV round-trip.
    """
    import json  # noqa: PLC0415

    from goldencheck.engine.scanner import scan_dataframe  # noqa: PLC0415

    df = _session_frame(session, relation_name)
    findings, profile = scan_dataframe(
        df, file_path=relation_name, domain=domain or None,
    )
    return json.dumps({
        "relation": relation_name,
        "health_score": profile.health_score,
        "findings": [
            {
                "rule": f.rule, "severity": f.severity, "column": f.column,
                "message": f.message,
            }
            for f in findings
        ],
    })


def health_score(session: Any, relation_name: str) -> float:
    """Stored Procedure: ``goldenmatch.goldencheck_health_score(...)``."""
    from goldencheck.engine.scanner import scan_dataframe  # noqa: PLC0415

    df = _session_frame(session, relation_name)
    _findings, profile = scan_dataframe(df, file_path=relation_name)
    return float(profile.health_score)
```

Confirm `Finding`'s real attribute names before writing the dict comprehension; adjust to whatever the dataclass actually exposes.

- [ ] **Step 4: Update the two SPCS handlers to pass `_session_from_env()`, run tests, commit**

```bash
git add -A && git commit -m "feat(snowflake): implement scan_table and health_score via scan_dataframe"
```

---

### Task 4: The three dedupe stored procedures

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/snowflake/udfs.py:366-410`
- Modify: `packages/dbt/goldensuite/spcs/server.py:207-221`
- Modify: `packages/python/goldenmatch/tests/snowflake/test_phase2_handlers.py`

**Interfaces:**
- Consumes: `_session_frame` (Task 1); `goldenmatch.dedupe_df`.
- Produces: `dedupe_full(session, input_table, config_json) -> list[list[Any]]`, `dedupe_clusters(...)`, `dedupe_pairs(...)`

The three scaffolds are currently classes with a `process()` method — the UDTF handler shape. Stored-procedure handlers are plain functions, so they become functions. Keep the classes as thin deprecated wrappers that raise a clear message pointing at the functions, so any DDL still referencing `goldenmatch_udfs.DedupeFull` fails loudly rather than mysteriously.

- [ ] **Step 1: Write the failing test**

```python
def test_dedupe_full_returns_one_row_per_cluster(session) -> None:
    import json

    from goldenmatch.snowflake.udfs import dedupe_full

    rows = dedupe_full(
        session, "CUSTOMERS",
        json.dumps({"exact": ["email"], "fuzzy": {"name": 0.85}}),
    )
    # Two Ada rows collapse; Grace stands alone.
    assert len(rows) == 2


def test_dedupe_clusters_attributes_every_input_row(session) -> None:
    import json

    from goldenmatch.snowflake.udfs import dedupe_clusters

    rows = dedupe_clusters(
        session, "CUSTOMERS", json.dumps({"exact": ["email"]}),
    )
    assert len(rows) == 3, "every input row must be attributed to a cluster"


def test_dedupe_pairs_returns_scored_pairs(session) -> None:
    import json

    from goldenmatch.snowflake.udfs import dedupe_pairs

    rows = dedupe_pairs(
        session, "CUSTOMERS", json.dumps({"exact": ["email"]}),
    )
    assert all(len(r) == 3 for r in rows)
    assert all(isinstance(r[2], float) for r in rows)


def test_the_old_udtf_classes_fail_loudly() -> None:
    from goldenmatch.snowflake.udfs import DedupeFull

    with pytest.raises(NotImplementedError, match="stored procedure"):
        DedupeFull().process("CUSTOMERS", "{}")
```

- [ ] **Step 2: Run to verify it fails**

Expected: FAIL — `ImportError: cannot import name 'dedupe_full'`

- [ ] **Step 3: Implement**

```python
def dedupe_full(
    session: Any, input_table: str, config_json: str
) -> list[list[Any]]:
    """Stored Procedure: ``goldenmatch.goldenmatch_dedupe_full(...)``.

    One row per cluster, golden record as the second column.

    Runs on ONE node: a stored procedure's Python sandbox is a single worker.
    That is a deliberate, documented ceiling, not an oversight -- a vectorized
    UDTF would need ``over (partition by 1)`` to see the whole frame, which
    sends it to one worker anyway. Measure before promising anything (#2699).
    """
    import json  # noqa: PLC0415

    from goldenmatch import dedupe_df  # noqa: PLC0415

    df = _session_frame(session, input_table)
    cfg = json.loads(config_json) if config_json else {}
    result = dedupe_df(df, **cfg)
    return [
        [cluster_id, golden]
        for cluster_id, golden in _iter_golden(result)
    ]
```

Write `_iter_golden`, `_iter_clusters` and `_iter_pairs` as small module-level helpers over whatever `DedupeResult` actually exposes — read `goldenmatch/_api.py:318` and the `DedupeResult` dataclass first. Implement `dedupe_clusters` and `dedupe_pairs` the same way, returning `(cluster_id, member_id, score)` and `(id_a, id_b, score)` rows respectively.

Replace each of the three scaffold classes with:

```python
class DedupeFull:
    """Deprecated UDTF handler. Phase 2 ships these as stored procedures.

    A UDTF's ``process()`` is never handed a Snowpark Session, which is exactly
    why the dedupe handlers were deferred. Use ``dedupe_full(session, ...)``
    and register it with CREATE PROCEDURE, not CREATE FUNCTION.
    """

    def process(self, input_table: str, config_json: str):  # noqa: D401
        raise NotImplementedError(
            "DedupeFull is now a stored procedure: call "
            "goldenmatch.snowflake.udfs.dedupe_full(session, input_table, "
            "config_json) and register it with CREATE PROCEDURE. Re-run "
            "`goldenmatch snowflake init` to refresh the DDL."
        )
```

- [ ] **Step 4: Update `__all__`, the SPCS handlers, run tests, commit**

Add the three function names to `__all__`; keep the class names for the deprecation path.

```bash
git add -A && git commit -m "feat(snowflake): dedupe stored procedures replacing the UDTF scaffolds"
```

---

### Task 5: `goldenmatch snowflake init`

**Files:**
- Create: `packages/python/goldenmatch/goldenmatch/cli/snowflake.py`
- Modify: `packages/python/goldenmatch/goldenmatch/cli/main.py` (register the group)
- Create: `packages/python/goldenmatch/tests/snowflake/test_cli_snowflake.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: a `snowflake` click group with `init` (`--database`, `--schema`, `--stage`, `--wheel`, `--dry-run`, `--execute`) and `render-ddl`.

`--dry-run` prints the DDL and touches nothing; that is the default. `--execute` runs it against a connection. Making print-only the default keeps the command safe to explore with.

- [ ] **Step 1: Write the failing test**

```python
"""goldenmatch snowflake init."""
from __future__ import annotations

import pytest
from click.testing import CliRunner


def test_render_ddl_emits_procedures_not_functions() -> None:
    from goldenmatch.cli.snowflake import snowflake as cli

    result = CliRunner().invoke(cli, ["render-ddl", "--database", "GM"])
    assert result.exit_code == 0
    assert "CREATE OR REPLACE PROCEDURE" in result.output
    assert "goldenmatch_dedupe_full" in result.output
    # The Phase 2 handlers must NOT be declared as UDTFs -- a UDTF's process()
    # is never handed a Session.
    assert "CREATE OR REPLACE FUNCTION goldenmatch.goldenmatch_dedupe_full" \
        not in result.output


def test_render_ddl_keeps_the_scalar_udfs_as_functions() -> None:
    from goldenmatch.cli.snowflake import snowflake as cli

    result = CliRunner().invoke(cli, ["render-ddl", "--database", "GM"])
    assert "CREATE OR REPLACE FUNCTION" in result.output
    assert "normalize_email" in result.output


def test_init_defaults_to_dry_run() -> None:
    from goldenmatch.cli.snowflake import snowflake as cli

    result = CliRunner().invoke(cli, ["init", "--database", "GM"])
    assert result.exit_code == 0
    assert "dry run" in result.output.lower()


def test_init_requires_a_connection_to_execute() -> None:
    from goldenmatch.cli.snowflake import snowflake as cli

    result = CliRunner().invoke(cli, ["init", "--database", "GM", "--execute"])
    assert result.exit_code != 0
    assert "connection" in result.output.lower()
```

- [ ] **Step 2: Run to verify it fails**

Expected: FAIL — `ModuleNotFoundError: No module named 'goldenmatch.cli.snowflake'`

- [ ] **Step 3: Implement the CLI**

The DDL templates come from `packages/dbt/goldensuite/docs/snowflake-setup.md` — the thirteen Phase 1 scalar UDFs stay `CREATE OR REPLACE FUNCTION`; the six Phase 2 handlers become `CREATE OR REPLACE PROCEDURE` with `RETURNS TABLE(...)` or a scalar return, and every one gains an implicit Session (declared in Snowflake by the handler's first parameter, which is not listed in the SQL signature).

The bootstrap module written to the stage must carry the #2699 packaging recipe:

```python
_BOOTSTRAP = '''
# goldenmatch_udfs.py -- the HANDLER shim Snowflake imports.
#
# Snowflake compute is aarch64, and a .whl is a zip that zipimport cannot
# dlopen a .so from. So the native wheels are extracted to a real path at
# MODULE scope and prepended to sys.path.
#
# The temp dir is UNIQUE per worker on purpose. Extracting into a fixed path
# lets a second worker see the directory, put it on sys.path, and dlopen a
# half-written .so -- "Fatal Python error: Bus error", an mmap of a truncated
# file. NativeLibrary.java avoids the same way for the JNI .so.
import sys, os, glob, tempfile, zipfile

_dir = tempfile.mkdtemp(prefix="gm_native_")
for _whl in glob.glob(os.path.join(
    sys._xoptions["snowflake_import_directory"], "*.whl"
)):
    with zipfile.ZipFile(_whl) as _z:
        _z.extractall(_dir)
sys.path.insert(0, _dir)

from goldenmatch.snowflake.udfs import *  # noqa: F401,F403,E402
'''
```

- [ ] **Step 4: Register the group in `cli/main.py`**

Follow the pattern the existing groups use (`identity`, `memory`, `sync`).

- [ ] **Step 5: Run tests and commit**

```bash
/d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest \
  packages/python/goldenmatch/tests/snowflake/test_cli_snowflake.py -v
git add -A && git commit -m "feat(cli): goldenmatch snowflake init registration command"
```

---

### Task 6: Documentation reconciliation

**Files:**
- Modify: `packages/dbt/goldensuite/docs/snowflake-setup.md:93-166`
- Modify: `packages/dbt/goldensuite/docs/snowflake-handlers.md`
- Modify: `packages/python/goldenmatch/goldenmatch/snowflake/udfs.py` (module docstring)
- Modify: `packages/python/goldenmatch/CHANGELOG.md`

- [ ] **Step 1: Rewrite the Phase 2 DDL in `snowflake-setup.md`**

Replace the five `CREATE OR REPLACE FUNCTION` blocks for `goldenmatch_dedupe_full`, `_clusters`, `_pairs`, `goldencheck_scan_table` and `goldencheck_health_score` with `CREATE OR REPLACE PROCEDURE` equivalents, and add `goldenmatch_correction_add`. Add a sentence saying why: a UDTF handler never receives a Session.

- [ ] **Step 2: Correct `snowflake-handlers.md`**

- "PR #553 shipped the outside" — the dbt macros shipped; `cli/snowflake.py` did not, until this plan.
- The Phase 2 deferral table is now a shipped table.
- The `scan_table` row's stated reason ("`scan_file` currently expects a file path") was already false: `scan_dataframe` exists.
- Note the single-node ceiling for the dedupe procedures.

- [ ] **Step 3: Correct the `udfs.py` module docstring**

Phase 2 is no longer scaffolded. The `goldenmatch snowflake init` sentence is now true. Fix the three "Tracking issue: `docs/snowflake-handlers.md`" paths to `packages/dbt/goldensuite/docs/snowflake-handlers.md`.

- [ ] **Step 4: CHANGELOG**

```markdown
- **Snowflake Phase 2 handlers (#2699).** `correction_add`, `scan_table`,
  `health_score` and the three dedupe handlers are implemented and registered as
  stored procedures rather than UDTFs -- a UDTF handler is never given a Snowpark
  Session, which is what deferred them. `goldenmatch snowflake init` renders and
  optionally executes the registration DDL. The dedupe procedures run on a single
  node; no throughput claim is made.
```

- [ ] **Step 5: Run the full affected surface and commit**

```bash
/d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest \
  packages/python/goldenmatch/tests/snowflake/ \
  packages/dbt/goldensuite/tests/ -v
git add -A && git commit -m "docs(snowflake): reconcile Phase 2 handler docs with the shipped procedures"
```

---

## Verification

1. `tests/snowflake/` passes in full.
2. `packages/dbt/goldensuite/tests/` still passes — the SPCS route table changed.
3. No `NotImplementedError` remains in `udfs.py` except the three deliberate UDTF-class deprecations.
4. `render-ddl` output and `snowflake-setup.md` agree on every object name.

## Known limitations to state in the PR body

- The dedupe procedures run on one node. No benchmark was run and none should be
  quoted. #2699's scope note stands.
- Handlers are tested against a fake Session and `fakesnow`, never against live
  Snowflake or real Snowpark. The registration DDL has not been executed against
  a warehouse.
- The wheel-extraction bootstrap is carried from #2699's field report; it has not
  been re-verified in this work.
