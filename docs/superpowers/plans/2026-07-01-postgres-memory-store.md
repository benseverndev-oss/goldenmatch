# Postgres MemoryStore Backend — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `backend='postgres'` path to `MemoryStore` so Learning Memory corrections + adjustments persist in a shared multi-tenant Postgres, isolated by `dataset`, with `learn()` scoped per-tenant.

**Architecture:** Keep one `MemoryStore` class. Introduce a thin execution seam (`_execute`/`_commit` + a placeholder helper that dispatch on `self._backend`) so existing method bodies keep their `?`-style SQL and only the genuinely dialect-specific bits (DDL, the two upserts, `IS NULL`→`COALESCE`) branch. Thread `dataset` through the learner + pipeline and `table_prefix` through config + pipeline.

**Tech Stack:** Python, `psycopg` v3 (existing optional `postgres` extra), SQLite (stdlib), Pydantic config, pytest (DB-gated via `tests/_pg_helpers.py`).

**Spec:** `docs/superpowers/specs/2026-07-01-postgres-memory-store-design.md`

## How to run tests (this monorepo)

From the repo root `D:\ER\gm-pg-memory`:
- Sync once: `uv sync --extra dev --extra postgres` (workspace `uv.lock` at root).
- Non-DB tests: `uv run pytest packages/python/goldenmatch/tests/<path> -q`
- DB-gated tests need `GOLDENMATCH_TEST_DATABASE_URL` set to an admin Postgres URL; without it, `HAS_POSTGRES` is False and those tests **skip** cleanly. (Set it during execution; the fixture provisions a throwaway `gm_test_<uuid>` DB per test.)

Locate the existing memory-store tests first (`grep -rl "MemoryStore" packages/python/goldenmatch/tests`) and co-locate new tests beside them.

---

## File Structure

- `packages/python/goldenmatch/goldenmatch/core/memory/store.py` — `LearnedAdjustment.dataset`; `dataset` params; execution seam; postgres branch; `table_prefix`.
- `packages/python/goldenmatch/goldenmatch/core/memory/learner.py` — `MemoryLearner(dataset=…)` threading.
- `packages/python/goldenmatch/goldenmatch/config/schemas.py` — `MemoryConfig.table_prefix`.
- `packages/python/goldenmatch/goldenmatch/core/pipeline.py` — `_open_memory_store` (table_prefix), `_apply_memory_pre` (dataset).
- `packages/python/goldenmatch/tests/…/test_memory_store_postgres.py` — new DB-gated tests.
- Existing SQLite memory-store tests — extended for the new params (back-compat).

---

## Task 1: `LearnedAdjustment.dataset` field

**Files:** Modify `core/memory/store.py` (dataclass ~114; `_row_to_adjustment` ~479). Test: existing memory-store test file.

- [ ] **Step 1: Failing test** — a `LearnedAdjustment` accepts `dataset` and defaults to None.

```python
def test_learned_adjustment_has_dataset_field():
    from goldenmatch.core.memory.store import LearnedAdjustment
    adj = LearnedAdjustment(matchkey_name="mk", threshold=0.9)
    assert adj.dataset is None
    adj2 = LearnedAdjustment(matchkey_name="mk", threshold=0.9, dataset="org_1")
    assert adj2.dataset == "org_1"
```

- [ ] **Step 2: Run → fail** (`TypeError: unexpected keyword argument 'dataset'`).
- [ ] **Step 3: Implement** — add to the dataclass (after `learned_at`):

```python
    dataset: str | None = None
```

And make `_row_to_adjustment` populate it defensively (SQLite rows won't have the column):

```python
    @staticmethod
    def _row_to_adjustment(row: Any) -> LearnedAdjustment:
        weights = json.loads(row["field_weights"]) if row["field_weights"] else None
        keys = row.keys() if hasattr(row, "keys") else ()
        return LearnedAdjustment(
            matchkey_name=row["matchkey_name"],
            threshold=row["threshold"],
            field_weights=weights,
            sample_size=row["sample_size"],
            learned_at=(row["learned_at"] if isinstance(row["learned_at"], datetime)
                        else datetime.fromisoformat(row["learned_at"])),
            dataset=row["dataset"] if "dataset" in keys else None,
        )
```

- [ ] **Step 3b: Apply the same timestamp guard to `_row_to_correction`** (load-bearing for Postgres reads in Task 6: psycopg returns a `datetime`, sqlite an ISO string). Change its `created_at=datetime.fromisoformat(row["created_at"])` to:

```python
            created_at=(row["created_at"] if isinstance(row["created_at"], datetime)
                        else datetime.fromisoformat(row["created_at"])),
```

Add a focused test that a `Correction` round-trips through the SQLite store with a parseable `created_at` (guards the refactor).

- [ ] **Step 4: Run → pass.**
- [ ] **Step 5: Commit** — `feat(memory): LearnedAdjustment.dataset + dialect-safe timestamp parsing`

---

## Task 2: `dataset` params on store read/write methods (SQLite, back-compat)

**Files:** Modify `core/memory/store.py` (`corrections_since` ~415, `save_adjustment` ~424, `get_adjustment` ~436, `get_all_adjustments` ~445). Test: existing memory-store test file.

`get_corrections`/`count_corrections` already accept `dataset` — leave them.

- [ ] **Step 1: Failing tests** (SQLite):

```python
def test_corrections_since_dataset_filter(tmp_path):
    from datetime import datetime, timedelta
    from goldenmatch.core.memory.store import MemoryStore, Correction
    s = MemoryStore(path=str(tmp_path / "m.db"))
    old = datetime.now() - timedelta(hours=1)
    def mk(a, b, ds):
        return Correction(id=f"{a}-{b}-{ds}", id_a=a, id_b=b, decision="reject",
                          source="steward", trust=1.0, field_hash="", record_hash="",
                          original_score=0.5, dataset=ds)
    s.add_correction(mk(1, 2, "A")); s.add_correction(mk(3, 4, "B"))
    assert len(s.corrections_since(old, dataset="A")) == 1
    assert len(s.corrections_since(old)) == 2          # back-compat: unfiltered

def test_adjustment_roundtrip_ignores_dataset_on_sqlite(tmp_path):
    from goldenmatch.core.memory.store import MemoryStore, LearnedAdjustment
    from datetime import datetime
    s = MemoryStore(path=str(tmp_path / "m.db"))
    s.save_adjustment(LearnedAdjustment("mk", threshold=0.9, sample_size=12,
                                        learned_at=datetime.now()), dataset="A")
    got = s.get_adjustment("mk", dataset="A")
    assert got is not None and got.threshold == 0.9
```

- [ ] **Step 2: Run → fail** (`corrections_since()`/`save_adjustment()` reject `dataset`).
- [ ] **Step 3: Implement (SQLite bodies).** Add `dataset: str | None = None` to each signature.
  - `corrections_since(since, dataset=None)`: when `dataset` is set, add `AND dataset = ?` to the WHERE.
  - `save_adjustment(adj, dataset=None)`: SQLite keeps `INSERT OR REPLACE` keyed by `matchkey_name` (its schema has no dataset column). The `dataset` param is accepted + ignored on SQLite (documented) — it is honored on Postgres (Task 6). If `adj.dataset` is None and `dataset` is set, set `adj.dataset = dataset` before saving so the returned object is tagged.
  - `get_adjustment(matchkey_name, dataset=None)`: SQLite ignores `dataset` (matchkey-only lookup); tag the returned adjustment's `.dataset` with the passed value for caller consistency.
  - `get_all_adjustments(dataset=None)`: SQLite ignores the filter (returns all).

  (These keep SQLite behavior identical while making the multi-tenant signature available; Postgres implements the real filtering in Task 6.)

- [ ] **Step 4: Run → pass** (new + all existing SQLite memory tests).
- [ ] **Step 5: Commit** — `feat(memory): dataset params on store methods (sqlite back-compat)`

---

## Task 3: Thread `dataset` into `MemoryLearner`

**Files:** Modify `core/memory/learner.py`. Test: existing learner test file.

- [ ] **Step 1: Failing test** — a learner with a dataset only learns over that dataset and tags the adjustment.

```python
def test_learner_scopes_to_dataset(tmp_path):
    from goldenmatch.core.memory.store import MemoryStore, Correction
    from goldenmatch.core.memory.learner import MemoryLearner
    s = MemoryStore(path=str(tmp_path / "m.db"))
    def mk(a, b, ds, dec, score):
        return Correction(id=f"{a}-{b}-{ds}", id_a=a, id_b=b, decision=dec,
                          source="steward", trust=1.0, field_hash="", record_hash="",
                          original_score=score, matchkey_name="mk", dataset=ds)
    # 10 approves high, 10 rejects low in dataset A; noise in B
    for i in range(10): s.add_correction(mk(i, 100+i, "A", "approve", 0.9))
    for i in range(10): s.add_correction(mk(200+i, 300+i, "A", "reject", 0.2))
    s.add_correction(mk(999, 1000, "B", "approve", 0.1))
    learner = MemoryLearner(s, dataset="A")
    adjustments = learner.learn()
    assert adjustments and adjustments[0].dataset == "A"
    assert 0.2 <= adjustments[0].threshold <= 0.9
```

- [ ] **Step 2: Run → fail** (`MemoryLearner()` rejects `dataset`).
- [ ] **Step 3: Implement.** Add `dataset: str | None = None` to `__init__`, store `self._dataset = dataset`. In `has_new_corrections`, pass it: `self._store.count_corrections(dataset=self._dataset)` and `self._store.corrections_since(last, dataset=self._dataset)`. In `learn`, read `self._store.get_corrections(dataset=self._dataset)` and set `adj = LearnedAdjustment(..., dataset=self._dataset)` and `self._store.save_adjustment(adj, dataset=self._dataset)`.

- [ ] **Step 4: Run → pass.**
- [ ] **Step 5: Commit** — `feat(memory): MemoryLearner dataset scoping`

---

## Task 4: `MemoryConfig.table_prefix` + pipeline threading

**Files:** Modify `config/schemas.py` (`MemoryConfig` ~894), `core/pipeline.py` (`_open_memory_store` ~292, `_apply_memory_pre` ~509). Test: a config test + a pipeline unit test (or grounding assertion).

- [ ] **Step 1: Failing test** — config accepts `table_prefix`; invalid prefix rejected.

```python
def test_memory_config_table_prefix():
    from goldenmatch.config.schemas import MemoryConfig
    assert MemoryConfig().table_prefix == ""
    assert MemoryConfig(table_prefix="goldenmatch_").table_prefix == "goldenmatch_"
    import pytest
    with pytest.raises(Exception):
        MemoryConfig(table_prefix="bad-prefix; DROP")
```

- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement.** Add to `MemoryConfig`:

```python
    table_prefix: str = ""

    @field_validator("table_prefix")
    @classmethod
    def _validate_table_prefix(cls, v: str) -> str:
        import re
        if v and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", v):
            raise ValueError("table_prefix must match ^[A-Za-z_][A-Za-z0-9_]*$")
        return v
```

Thread through the pipeline:
- `_open_memory_store`: add `table_prefix=config.memory.table_prefix,` to the `MemoryStore(...)` call.
- `_apply_memory_pre`: build the learner with the dataset:

```python
        learner = MemoryLearner(
            memory_store,
            threshold_min=config.memory.learning.threshold_min_corrections,
            weights_min=config.memory.learning.weights_min_corrections,
            dataset=config.memory.dataset,
        )
```

- [ ] **Step 4: Run → pass** (config test; and existing pipeline tests still green — `MemoryStore` must accept `table_prefix` now, so do Task 5's signature add first if needed, or add the `table_prefix=""` param in this task's store change and wire the real behavior in Task 6). Add `table_prefix: str = ""` to `MemoryStore.__init__` here (accepted, regex-guarded, unused by SQLite) so the pipeline call type-checks.
- [ ] **Step 5: Commit** — `feat(memory): table_prefix config + pipeline dataset/prefix threading`

---

## Task 5: Execution seam (route SQLite through a dialect-aware executor)

**Files:** Modify `core/memory/store.py`. Test: existing SQLite memory tests are the guard (pure refactor — no behavior change).

- [ ] **Step 1: Confirm the SQLite suite is green** (baseline): `uv run pytest packages/python/goldenmatch/tests/<memory tests> -q` → PASS.
- [ ] **Step 2: Introduce the seam.** Add helpers on `MemoryStore` and route every `self._conn.execute(...)` / `with self._conn:` through them. (`executescript` stays inside the SQLite-only `__init__` branch — it needs no seam.)

```python
    def _ph(self, sql: str) -> str:
        """Translate '?' placeholders to the backend's style."""
        return sql if self._backend == "sqlite" else sql.replace("?", "%s")

    def _execute(self, sql: str, params: tuple = ()):  # returns a cursor/iterable of rows
        return self._conn.execute(self._ph(sql), params)

    def _commit(self):
        self._conn.commit()
```

Mechanically replace `self._conn.execute(<sql>, <params>)` → `self._execute(<sql>, <params>)` in every method, and replace the `with self._conn:` transaction blocks with explicit `self._execute(...); self._commit()` (psycopg has no `with connection:` auto-commit semantics identical to sqlite). Keep the SQL text (with `?`) unchanged — `_ph` handles the dialect.

- [ ] **Step 3: Run → the full SQLite memory suite still passes** (proves the refactor is behavior-preserving on sqlite).
- [ ] **Step 4: Commit** — `refactor(memory): route store SQL through a dialect-aware executor`

---

## Task 6: Postgres backend branch + DDL + upserts + DB-gated tests

**Files:** Modify `core/memory/store.py` (`__init__` ~164, `_SCHEMA`, `add_correction` upsert, `save_adjustment`, the `IS NULL` reads). Create `tests/…/test_memory_store_postgres.py`.

- [ ] **Step 1: Failing DB-gated test** (skips without `GOLDENMATCH_TEST_DATABASE_URL`). Co-locate with existing memory tests; mirror `tests/identity/test_postgres_bulk.py` for the fixture usage.

```python
import pytest
from tests._pg_helpers import HAS_POSTGRES, pg_url_fixture

pytestmark = pytest.mark.skipif(not HAS_POSTGRES, reason="no test postgres")

@pytest.fixture
def pg():
    # pg_url_fixture is a generator (NOT a context manager) — mirror the existing
    # pattern in tests/test_db.py: `yield from`. The yielded holder exposes .url().
    yield from pg_url_fixture()

def _mk(a, b, ds, dec="reject", score=0.5, trust=1.0, src="steward"):
    from goldenmatch.core.memory.store import Correction
    return Correction(id=f"{a}-{b}-{ds}", id_a=a, id_b=b, decision=dec, source=src,
                      trust=trust, field_hash="", record_hash="", original_score=score,
                      matchkey_name="mk", dataset=ds)

def test_pg_correction_roundtrip_and_trust_wins(pg):
    from goldenmatch.core.memory.store import MemoryStore
    s = MemoryStore(backend="postgres", connection=pg.url(), table_prefix="goldenmatch_")
    s.add_correction(_mk(1, 2, "A"))
    assert s.count_corrections(dataset="A") == 1
    # lower-trust does not overwrite higher-trust
    s.add_correction(_mk(1, 2, "A", dec="approve", trust=0.5, src="agent"))
    got = s.get_pair_correction(1, 2, dataset="A")
    assert got.decision == "reject"           # steward (1.0) kept
    s.close()

def test_pg_null_dataset_upsert(pg):
    from goldenmatch.core.memory.store import MemoryStore
    s = MemoryStore(backend="postgres", connection=pg.url(), table_prefix="goldenmatch_")
    s.add_correction(_mk(1, 2, None)); s.add_correction(_mk(1, 2, None, dec="approve"))
    assert s.count_corrections() == 1          # upsert, not duplicate
    s.close()

def test_pg_adjustments_tenant_isolation(pg):
    from goldenmatch.core.memory.store import MemoryStore, LearnedAdjustment
    from datetime import datetime
    s = MemoryStore(backend="postgres", connection=pg.url(), table_prefix="goldenmatch_")
    s.save_adjustment(LearnedAdjustment("mk", threshold=0.8, learned_at=datetime.now()), dataset="A")
    s.save_adjustment(LearnedAdjustment("mk", threshold=0.6, learned_at=datetime.now()), dataset="B")
    assert s.get_adjustment("mk", dataset="A").threshold == 0.8
    assert s.get_adjustment("mk", dataset="B").threshold == 0.6
    got = s.get_all_adjustments(dataset="A")
    assert len(got) == 1 and got[0].dataset == "A"
    s.close()

def test_pg_missing_extra_message(monkeypatch):
    # Force `import psycopg` to fail → the actionable ImportError, without a DB.
    import sys
    monkeypatch.setitem(sys.modules, "psycopg", None)
    from goldenmatch.core.memory.store import MemoryStore
    with pytest.raises(ImportError, match=r"goldenmatch\[postgres\]"):
        MemoryStore(backend="postgres", connection="postgresql://unused")

def test_pg_learn_per_dataset_isolation(pg):
    from goldenmatch.core.memory.store import MemoryStore
    from goldenmatch.core.memory.learner import MemoryLearner
    s = MemoryStore(backend="postgres", connection=pg.url(), table_prefix="goldenmatch_")
    for i in range(10): s.add_correction(_mk(i, 100+i, "A", "approve", 0.9))
    for i in range(10): s.add_correction(_mk(200+i, 300+i, "A", "reject", 0.2))
    for i in range(10): s.add_correction(_mk(i, 100+i, "B", "approve", 0.3))
    for i in range(10): s.add_correction(_mk(200+i, 300+i, "B", "reject", 0.25))
    MemoryLearner(s, dataset="A").learn()
    MemoryLearner(s, dataset="B").learn()
    a, b = s.get_adjustment("mk", dataset="A"), s.get_adjustment("mk", dataset="B")
    assert a and b and a.threshold != b.threshold   # not pooled
    s.close()
```

- [ ] **Step 2: Run → fail** (`NotImplementedError: Backend 'postgres'`).
- [ ] **Step 3: Implement the postgres branch.** In `__init__`, add the regex-guarded `table_prefix` (store as `self._p = f"{table_prefix}" or ""`, applied to table names) and:

```python
        elif backend == "postgres":
            try:
                import psycopg  # noqa: PLC0415 — lazy, optional extra
            except ImportError as e:
                raise ImportError(
                    "backend='postgres' requires: pip install goldenmatch[postgres]"
                ) from e
            if not connection:
                raise ValueError("backend='postgres' requires a connection DSN")
            self._conn = psycopg.connect(connection, row_factory=psycopg.rows.dict_row)
            for stmt in self._pg_schema():   # split DDL; execute individually
                self._conn.execute(stmt)
            self._conn.commit()
```

  - `_pg_schema()` returns the two `CREATE TABLE IF NOT EXISTS` statements (+ the corrections unique index) with Postgres types and `self._p` prefixing the table names:
    - corrections: `id TEXT PRIMARY KEY, id_a BIGINT, id_b BIGINT, decision TEXT, source TEXT, trust DOUBLE PRECISION, field_hash TEXT, record_hash TEXT, original_score DOUBLE PRECISION, matchkey_name TEXT, reason TEXT, dataset TEXT, created_at TIMESTAMPTZ DEFAULT now(), field_name TEXT, original_value TEXT, corrected_value TEXT, cluster_score DOUBLE PRECISION, cluster_outcome TEXT`
    - `CREATE UNIQUE INDEX IF NOT EXISTS <prefix>corrections_pair ON <prefix>corrections (id_a, id_b, COALESCE(dataset, ''))`
    - adjustments: `dataset TEXT NOT NULL DEFAULT '', matchkey_name TEXT, threshold DOUBLE PRECISION, field_weights TEXT, sample_size INTEGER, learned_at TIMESTAMPTZ, PRIMARY KEY (dataset, matchkey_name)`
  - **Table names (pick ONE approach — this one).** In `__init__`, after validating `table_prefix` against `^[A-Za-z_][A-Za-z0-9_]*$` (reuse the same guard as the config validator; empty string allowed), set two attributes: `self._corrections = f"{table_prefix}corrections"` and `self._adjustments = f"{table_prefix}adjustments"` (sqlite → `table_prefix=""` → bare `corrections`/`adjustments`, unchanged). Then f-string-interpolate these attributes into the SQL of **every method that hard-codes a table name** — sweep all nine: `add_correction`, `get_pair_correction`, `get_corrections`, `count_corrections`, `corrections_since`, `save_adjustment`, `get_adjustment`, `get_all_adjustments`, `last_learn_time` (plus the DDL). The names come from validated config, never raw user input, so interpolation is safe. Do NOT introduce a `self._t()` helper — the two attributes are enough.
  - **`add_correction` upsert (postgres):** branch on `self._backend`. Postgres uses:
    `INSERT INTO <corrections> (...) VALUES (...) ON CONFLICT (id_a, id_b, COALESCE(dataset, '')) DO UPDATE SET decision=EXCLUDED.decision, ... WHERE EXCLUDED.trust >= <corrections>.trust`. This replaces the trust-check-then-DELETE+INSERT for postgres (keep the existing sqlite path). Timestamps: pass `correction.created_at` (a `datetime`) directly for postgres (not `.isoformat()`).
  - **`save_adjustment` (postgres):** `INSERT INTO <adjustments> (dataset, matchkey_name, threshold, field_weights, sample_size, learned_at) VALUES (%s,...) ON CONFLICT (dataset, matchkey_name) DO UPDATE SET ...`. Use `dataset or ''`.
  - **`get_adjustment`/`get_all_adjustments` (postgres):** filter by `dataset` (`WHERE dataset = %s`, using `dataset or ''`); `get_all_adjustments(None)` → all rows.
  - **`IS NULL` reads:** `get_pair_correction`/`get_corrections`/`count_corrections`/`corrections_since` with `dataset=None` on postgres must match rows where dataset is NULL via `COALESCE(dataset,'') = ''` (the sqlite `dataset IS NULL` stays for sqlite). Add a `_dataset_pred(alias)` helper returning the right predicate per backend, or branch inline.
  - **Row conversion:** `psycopg` dict_row returns a `dict` (has `.keys()`, `row["x"]`), so `_row_to_correction`/`_row_to_adjustment` already work — the timestamp guard from Task 1 handles `datetime` vs str.
  - **`_commit`:** `self._conn.commit()` for both (psycopg needs explicit commit; sqlite connection.commit() is fine).

- [ ] **Step 4: Run → pass** with `GOLDENMATCH_TEST_DATABASE_URL` set; and confirm the tests **skip** cleanly when it's unset.
- [ ] **Step 5: Full memory suite green on SQLite** (no regression): `uv run pytest packages/python/goldenmatch/tests/<memory> -q`.
- [ ] **Step 6: Commit** — `feat(memory): postgres MemoryStore backend (dataset-isolated corrections + adjustments)`

---

## Final verification

- [ ] SQLite memory suite green; ruff/lint clean on changed files (`uv run ruff check packages/python/goldenmatch/goldenmatch/core/memory packages/python/goldenmatch/goldenmatch/core/pipeline.py packages/python/goldenmatch/goldenmatch/config/schemas.py`).
- [ ] Postgres tests green with a test DSN; skip cleanly without.
- [ ] Open PR against `main` from `feat/postgres-memory-store`.

## Out of scope (per spec)

- golden-truth integration (Spec 2). Connection pooling / injected-connection constructor. Alembic migrations. Adding a `dataset` column to the SQLite `adjustments` schema.
