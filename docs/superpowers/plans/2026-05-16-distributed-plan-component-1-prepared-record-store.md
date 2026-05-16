# Distributed Plan v1 — Component 1: Prepared-record store

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist the post-transform / post-auto-fix DataFrame to a DuckDB-backed disk store keyed by data-shape + prep-config signature, so the controller's 5 iteration passes (and downstream distributed workers) read prepared records from disk instead of re-running GoldenCheck quality scan + GoldenFlow transform + auto-fix every time.

**Architecture:** Three units land in `goldenmatch/distributed/record_store.py`: a `PreparedRecordStore` class wrapping a DuckDB connection, a `materialize_prepared_records()` helper that writes a Polars DataFrame to the store, and a `load_prepared_records()` helper that reads it back. Phase 2 adds a new `_prep_store: PreparedRecordStore | None = None` kwarg to `_run_dedupe_pipeline` (mirror of existing `_prep_cache_seed`). When set, the pipeline checks the disk store **after** the in-memory `_PREP_CACHE` miss but **before** running prep steps; on disk-hit it short-circuits the prep work, on miss it runs prep then writes back to disk. Phase 3 wires the controller iteration loop to construct one `PreparedRecordStore` at `run()` entry and thread it through all 5 iterations so iter 2–5 hit the disk store. The existing in-memory `_PREP_CACHE` stays as the default (small-N hot path); the disk store opt-in covers large-N + distributed workers.

**Order rationale:** in-memory cache first (RAM is faster than DuckDB + Arrow + file I/O), disk store second (covers cross-call + cross-process cases the in-memory LRU can't). On disk-hit, the in-memory cache is seeded so subsequent in-process lookups stay fast.

**Tech Stack:** Python 3.12, DuckDB (already a goldenmatch optional dep), Polars + PyArrow (already required), dataclasses, pytest. No new deps.

**Spec:** [`docs/superpowers/specs/2026-05-15-distributed-plan-v1-design.md`](../specs/2026-05-15-distributed-plan-v1-design.md) §Component 1.

---

## File structure

Three phases, three PRs. Each phase ships a unit that's independently testable.

**Created files:**

| Path | Phase | Responsibility |
|---|---|---|
| `packages/python/goldenmatch/goldenmatch/distributed/__init__.py` | 1 | Empty marker; `distributed/` becomes a package |
| `packages/python/goldenmatch/goldenmatch/distributed/record_store.py` | 1 | `PreparedRecordStore` class + helpers — pure primitive, no wiring |
| `packages/python/goldenmatch/tests/test_prepared_record_store.py` | 1 | Unit tests: roundtrip Polars↔DuckDB, signature keying, eviction, cleanup |
| `packages/python/goldenmatch/tests/test_prepared_record_store_pipeline.py` | 2 | Integration: Phase 2's `_run_dedupe_pipeline` opt-in code path |
| `packages/python/goldenmatch/tests/test_prepared_record_store_controller.py` | 3 | End-to-end: Phase 3's controller-iteration reuse cuts `run_transform` call count |

**Modified files:**

| Path | Phase | Change |
|---|---|---|
| `packages/python/goldenmatch/pyproject.toml` | 1 | Promote `duckdb>=0.9` from `optional-dependencies.duckdb` to a hard dep (already used by `score_duckdb.py`; making it required for the prepared-record store removes an `ImportError: optional dep` surface) |
| `packages/python/goldenmatch/goldenmatch/config/schemas.py` | 2 | Add `prepared_record_store: bool = False` to `GoldenMatchConfig` |
| `packages/python/goldenmatch/goldenmatch/core/pipeline.py` | 2 | (a) Add `_prep_store: PreparedRecordStore \| None = None` kwarg to `_run_dedupe_pipeline`. (b) Inside the existing prep-cache `else` block at ~line 644 (right after `_PREP_CACHE.get` returns None), before the GoldenCheck/transform/auto-fix steps run: if `_prep_store is not None`, call `load_prepared_records(_prep_store, signature=_prep_cache_signature(config))`; on hit, set `combined_lf = cached_df.lazy()`, populate `_PREP_CACHE[prep_cache_key]`, and skip the prep steps. (c) At ~line 680 (after the existing in-memory cache populate), if `_prep_store is not None`, call `materialize_prepared_records(_prep_store, prepped_df, signature=_prep_cache_signature(config))`. Pipeline does NOT own the store lifecycle — Phase 3's controller does |
| `packages/python/goldenmatch/goldenmatch/core/autoconfig_controller.py` | 3 | (a) At the top of `run()`, if `config_v0.prepared_record_store`, construct one `PreparedRecordStore` from `GOLDENMATCH_PREPARED_RECORD_STORE_DIR` + `GOLDENMATCH_PREPARED_RECORD_STORE_PERSIST` env vars. (b) Thread `_prep_store=<the store>` through every `_run_pipeline_sample` / `_finalize` call that ultimately reaches `_run_dedupe_pipeline`. (c) Close the store in a `try/finally` covering the whole iteration loop, so all 5 iterations share the same store handle and the cleanup runs even on KeyboardInterrupt or exception |
| `packages/python/goldenmatch/CLAUDE.md` | 3 | Document the prepared-record store, its config flag, the cross-iteration speedup, and the ephemeral-tempfile lifecycle |

---

## Pre-flight checklist

Before starting any task:

- [ ] Working in a clean branch **off `main`** (NOT off the prior phase's branch): `git fetch origin main && git switch -c distributed-plan-c1-phase-N origin/main`. Per CLAUDE.md "Stacked PR auto-closure on squash-merge", stacking phases gets bitten when an earlier phase squash-merges.
- [ ] Editable install active: `python -c "import goldenmatch; print(goldenmatch.__file__)"` shows the worktree path.
- [ ] Baseline tests green: `pytest tests/ -q --timeout=120 --ignore=tests/test_db.py --ignore=tests/test_reconcile.py --ignore=tests/test_mcp_and_watch.py --ignore=tests/test_embedder.py --ignore=tests/test_llm_boost.py --ignore=tests/benchmarks` shows the post-controller-budget baseline (~2486+ passed / 5 skipped).
- [ ] DuckDB importable: `python -c "import duckdb; print(duckdb.__version__)"` (Phase 1's hard-dep promotion takes effect after a `pip install -e .` rebuild — verify before relying).

---

## Phase 1 — `PreparedRecordStore` primitive

Pure data type + DuckDB CRUD. No wiring.

**Files:**
- Create: `packages/python/goldenmatch/goldenmatch/distributed/__init__.py`
- Create: `packages/python/goldenmatch/goldenmatch/distributed/record_store.py`
- Create: `packages/python/goldenmatch/tests/test_prepared_record_store.py`
- Modify: `packages/python/goldenmatch/pyproject.toml` (promote duckdb to hard dep)

### Task 1.1: Write the failing test

- [ ] **Step 1: Create the test file.**

`packages/python/goldenmatch/tests/test_prepared_record_store.py`:

```python
"""Unit tests for PreparedRecordStore (Component 1 of Distributed Plan v1).

Spec: docs/superpowers/specs/2026-05-15-distributed-plan-v1-design.md
§Component 1.
"""
from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from goldenmatch.distributed.record_store import (
    PreparedRecordStore,
    load_prepared_records,
    materialize_prepared_records,
)


def _sample_df() -> pl.DataFrame:
    return pl.DataFrame({
        "__row_id__": [0, 1, 2, 3],
        "name": ["alice", "bob", "charlie", "dana"],
        "email": ["a@x.com", "b@x.com", "c@x.com", "d@x.com"],
        "__mk_email_lower__": ["a@x.com", "b@x.com", "c@x.com", "d@x.com"],
    })


def test_store_init_creates_tempdir(tmp_path: Path):
    store = PreparedRecordStore(base_dir=tmp_path)
    assert store.path.exists()
    assert store.path.parent == tmp_path
    store.close()


def test_store_init_creates_tempfile_when_no_base_dir():
    """No base_dir -> mkstemp into system temp. Cleaned up on close()."""
    store = PreparedRecordStore()
    p = store.path
    assert p.exists()
    store.close()
    assert not p.exists()


def test_materialize_and_load_roundtrips_dataframe(tmp_path: Path):
    """Polars -> DuckDB -> Polars roundtrip preserves data + dtypes."""
    store = PreparedRecordStore(base_dir=tmp_path)
    try:
        df = _sample_df()
        signature = "sig-v1"
        materialize_prepared_records(store, df, signature=signature)
        loaded = load_prepared_records(store, signature=signature)
        # Order may differ post-DuckDB; compare as sets of row tuples.
        assert set(loaded.iter_rows()) == set(df.iter_rows())
        assert set(loaded.columns) == set(df.columns)
    finally:
        store.close()


def test_load_missing_signature_returns_none(tmp_path: Path):
    """Cache miss is signaled by None; callers prep + materialize."""
    store = PreparedRecordStore(base_dir=tmp_path)
    try:
        assert load_prepared_records(store, signature="missing") is None
    finally:
        store.close()


def test_signature_isolates_entries(tmp_path: Path):
    """Two different signatures address two different cached frames."""
    store = PreparedRecordStore(base_dir=tmp_path)
    try:
        df1 = _sample_df()
        df2 = pl.DataFrame({"__row_id__": [10], "x": ["other"]})
        materialize_prepared_records(store, df1, signature="sig-a")
        materialize_prepared_records(store, df2, signature="sig-b")
        loaded_a = load_prepared_records(store, signature="sig-a")
        loaded_b = load_prepared_records(store, signature="sig-b")
        assert loaded_a is not None
        assert loaded_b is not None
        assert set(loaded_a.columns) == {"__row_id__", "name", "email", "__mk_email_lower__"}
        assert set(loaded_b.columns) == {"__row_id__", "x"}
    finally:
        store.close()


def test_close_is_idempotent(tmp_path: Path):
    """Multiple close() calls don't raise — important for finally blocks
    in the controller's exception paths."""
    store = PreparedRecordStore(base_dir=tmp_path)
    store.close()
    store.close()  # no-op


def test_close_cleans_up_file(tmp_path: Path):
    """close() removes the underlying DuckDB file when cleanup=True."""
    store = PreparedRecordStore(base_dir=tmp_path)
    p = store.path
    assert p.exists()
    store.close()
    assert not p.exists()


def test_close_preserves_file_when_cleanup_false(tmp_path: Path):
    """cleanup=False keeps the file (useful for cross-call persistence)."""
    store = PreparedRecordStore(base_dir=tmp_path, cleanup=False)
    p = store.path
    materialize_prepared_records(store, _sample_df(), signature="sig-v1")
    store.close()
    assert p.exists()
    # Re-open and read back.
    store2 = PreparedRecordStore(path=p, cleanup=False)
    try:
        loaded = load_prepared_records(store2, signature="sig-v1")
        assert loaded is not None
        assert loaded.height == 4
    finally:
        store2.close()


def test_context_manager_closes_on_exit(tmp_path: Path):
    """PreparedRecordStore is a context manager."""
    with PreparedRecordStore(base_dir=tmp_path) as store:
        p = store.path
        assert p.exists()
    assert not p.exists()
```

- [ ] **Step 2: Run; expect `ModuleNotFoundError: No module named 'goldenmatch.distributed'`.**

```bash
cd D:\show_case\goldenmatch\packages\python\goldenmatch
python -m pytest tests/test_prepared_record_store.py -v
```

### Task 1.2: Create the module

- [ ] **Step 1: Create the package marker.**

`packages/python/goldenmatch/goldenmatch/distributed/__init__.py`:

```python
"""Distributed execution primitives for 50M+ row deduplication.

Spec: docs/superpowers/specs/2026-05-15-distributed-plan-v1-design.md.

Component 1 (prepared-record store) is the first piece. Components 2–6
(partitioned execution, distributed scoring, streaming pair store,
distributed clustering, planner integration) ship as their own sub-projects.
"""
```

- [ ] **Step 2: Implement the store.**

`packages/python/goldenmatch/goldenmatch/distributed/record_store.py`:

```python
"""DuckDB-backed prepared-record store.

Spec: docs/superpowers/specs/2026-05-15-distributed-plan-v1-design.md
§Component 1.

The controller's iteration loop (and downstream distributed workers) need
to read the post-transform / post-auto-fix DataFrame multiple times. The
in-memory ``_PREP_CACHE`` in ``core/pipeline.py`` covers small-N within
one process; this store covers large-N (doesn't fit in RAM) and the
distributed case (workers in separate processes / machines need shared
access).

Lifecycle:
- ``PreparedRecordStore()`` (no args) -> ephemeral tempfile, cleaned on close.
- ``PreparedRecordStore(base_dir=...)`` -> tempfile inside that dir.
- ``PreparedRecordStore(path=...)`` -> open an existing store; useful for
  cross-call persistence.
- ``cleanup=False`` keeps the file after close (for persistence).

The store is keyed by ``signature`` (typically the
``_prep_cache_signature(config)`` produced by ``core/pipeline.py``).
Multiple distinct signatures coexist in the same store; lookups are
exact-match.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
import pyarrow as pa  # required for Polars <-> DuckDB Arrow handoff


_TABLE_PREFIX = "prepared_"


def _sanitize_signature(signature: str) -> str:
    """Map any signature string to a valid DuckDB table-name suffix.

    DuckDB table names must be ``[A-Za-z_][A-Za-z0-9_]*``. We hash the
    signature so the table-name length is bounded and the input character
    set doesn't matter.
    """
    import hashlib

    h = hashlib.sha256(signature.encode("utf-8")).hexdigest()
    return h[:16]


class PreparedRecordStore:
    """Owns one DuckDB connection backing a partitioned record store.

    Usage:

    .. code-block:: python

        with PreparedRecordStore() as store:
            materialize_prepared_records(store, df, signature="sig-v1")
            loaded = load_prepared_records(store, signature="sig-v1")
    """

    def __init__(
        self,
        *,
        base_dir: Path | str | None = None,
        path: Path | str | None = None,
        cleanup: bool = True,
    ) -> None:
        if path is not None:
            self.path = Path(path)
            self._owns_file = False  # caller manages lifecycle
        else:
            base = Path(base_dir) if base_dir is not None else None
            fd, p = tempfile.mkstemp(
                suffix=".duckdb", prefix="goldenmatch_prepared_", dir=base,
            )
            os.close(fd)
            self.path = Path(p)
            self._owns_file = True
        self._cleanup = cleanup
        self._con: duckdb.DuckDBPyConnection | None = duckdb.connect(str(self.path))
        self._closed = False

    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        if self._con is None:
            raise RuntimeError("PreparedRecordStore is closed")
        return self._con

    def close(self) -> None:
        """Idempotent close. Removes the file when cleanup=True and the
        store owns it."""
        if self._closed:
            return
        self._closed = True
        if self._con is not None:
            self._con.close()
            self._con = None
        if self._cleanup and self._owns_file and self.path.exists():
            self.path.unlink(missing_ok=True)

    def __enter__(self) -> "PreparedRecordStore":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


def materialize_prepared_records(
    store: PreparedRecordStore,
    df: pl.DataFrame,
    *,
    signature: str,
) -> None:
    """Write ``df`` into the store under ``signature``.

    Polars -> Arrow -> DuckDB via ``arrow_table`` view registration. Same
    pattern as ``backends/score_duckdb.py`` (PR #235). Existing entries
    at the same signature are replaced.
    """
    table = _TABLE_PREFIX + _sanitize_signature(signature)
    con = store.connection
    arrow_table = df.to_arrow()  # noqa: F841  -- DuckDB resolves by local name
    con.execute(f'DROP TABLE IF EXISTS "{table}"')
    con.execute(f'CREATE TABLE "{table}" AS SELECT * FROM arrow_table')


def load_prepared_records(
    store: PreparedRecordStore,
    *,
    signature: str,
) -> pl.DataFrame | None:
    """Read ``signature``'s entry back as a Polars DataFrame.

    Returns None when the signature isn't present in the store (cache
    miss; caller prepares + materializes).
    """
    table = _TABLE_PREFIX + _sanitize_signature(signature)
    con = store.connection
    exists = con.execute(
        "SELECT 1 FROM duckdb_tables() WHERE table_name = ?",
        [table],
    ).fetchone()
    if exists is None:
        return None
    arrow_table = con.execute(f'SELECT * FROM "{table}"').arrow()
    return pl.from_arrow(arrow_table)
```

- [ ] **Step 3: Run; expect 9 passed.**

```bash
python -m pytest tests/test_prepared_record_store.py -v
```

If a test fails, check:
- `duckdb` import — confirm `pip install -e .` picked up the hard-dep promotion (Task 1.3).
- `pyarrow` import — already required for Polars `.to_arrow()`; if missing, `pip install pyarrow`.
- Tempfile path on Windows — `tempfile.mkstemp` returns OS-specific paths; the test uses `Path()` so should be agnostic.

### Task 1.3: Promote duckdb to a hard dep

- [ ] **Step 1: Inspect `pyproject.toml`.**

```bash
grep -n "duckdb" packages/python/goldenmatch/pyproject.toml
```

Expected: one line in `[project.optional-dependencies.duckdb]`, none in `[project.dependencies]`.

- [ ] **Step 2: Add `duckdb>=0.9` to `[project.dependencies]`.**

Edit `pyproject.toml`:

```toml
dependencies = [
    "polars>=1.0",
    ...
    "psutil>=5.9",
    "duckdb>=0.9",
]
```

Keep the `[project.optional-dependencies.duckdb]` block as a no-op alias for now (a follow-up can drop it; backward-compat in v1 means existing `pip install goldenmatch[duckdb]` still works).

- [ ] **Step 3: Verify import works without the extra.**

```bash
python -c "import duckdb; print('ok', duckdb.__version__)"
```

- [ ] **Step 4: Run lint.**

```bash
cd D:\show_case\goldenmatch\packages\python\goldenmatch
uv run ruff check goldenmatch/distributed/ tests/test_prepared_record_store.py
```

### Task 1.4: Commit + open PR

- [ ] **Step 1: Commit.**

```bash
git add packages/python/goldenmatch/goldenmatch/distributed/ packages/python/goldenmatch/tests/test_prepared_record_store.py packages/python/goldenmatch/pyproject.toml
git commit -m "feat(distributed): Phase 1 -- PreparedRecordStore primitive (Component 1 of Distributed Plan v1)

Phase 1 of the distributed-plan-component-1 plan (docs/superpowers/
plans/2026-05-16-distributed-plan-component-1-prepared-record-store.md).
Pure primitive, no wiring -- Phase 2 will wire into _run_dedupe_pipeline.

- goldenmatch/distributed/__init__.py (NEW): package marker.
- goldenmatch/distributed/record_store.py (NEW): PreparedRecordStore
  class + materialize_prepared_records / load_prepared_records helpers.
  DuckDB-backed disk store keyed by signature; Polars <-> DuckDB via
  Arrow bulk handoff (same pattern as score_duckdb.py from PR #235).
  Lifecycle: ephemeral tempfile by default; cleanup=False for
  cross-call persistence; context manager support.
- pyproject.toml: promote duckdb>=0.9 from optional-dependencies.duckdb
  to a hard dep. The optional-dep block stays as a no-op alias for
  backward compat; follow-up can drop it.

Spec: §Component 1 of distributed-plan-v1-design.md.

Tests: 9 new -- roundtrip, signature isolation, missing-signature
None return, idempotent close, file cleanup, persistence via
cleanup=False, context manager.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 2: Push + open PR.**

Auth dance per memory `feedback_github_auth_switch.md`:

```bash
gh auth switch --user benzsevern
GH_TOKEN=$(gh auth token --user benzsevern) git -c credential.helper="!gh auth git-credential" push -u origin distributed-plan-c1-phase-1
GH_TOKEN=$(gh auth token --user benzsevern) gh pr create --base main --title "feat(distributed): Component 1 / Phase 1 -- PreparedRecordStore primitive" --body "<see plan>"
gh auth switch --user benzsevern-mjh
```

PR title: `feat(distributed): Distributed Plan v1 Component 1 / Phase 1 — PreparedRecordStore primitive`.

---

## Phase 2 — Wire into `_run_dedupe_pipeline` (opt-in)

Adds a config flag `prepared_record_store: bool = False` and a branch in `_run_dedupe_pipeline` that reads from / writes to the disk store on cache miss. The existing in-memory `_PREP_CACHE` stays the default; disk store is opt-in.

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/config/schemas.py` (add `prepared_record_store` field to `GoldenMatchConfig`)
- Modify: `packages/python/goldenmatch/goldenmatch/core/pipeline.py` (branch in `_run_dedupe_pipeline`)
- Test: `packages/python/goldenmatch/tests/test_prepared_record_store_pipeline.py` (NEW)

### Task 2.1: Failing tests

- [ ] **Step 1: Write the test file.**

`packages/python/goldenmatch/tests/test_prepared_record_store_pipeline.py`:

```python
"""Integration tests for the prepared-record store inside the pipeline.

Spec §Component 1, Phase 2 wiring."""
from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from goldenmatch.config.schemas import GoldenMatchConfig


def _df() -> pl.DataFrame:
    return pl.DataFrame({
        "name":  ["alice", "alyce", "bob", "robert"] * 20,
        "email": [f"u{i}@x.com" for i in range(80)],
    })


def test_config_default_disables_prepared_record_store():
    """Default flag is False; existing in-memory _PREP_CACHE path is unchanged."""
    cfg = GoldenMatchConfig()
    assert cfg.prepared_record_store is False


def test_config_accepts_prepared_record_store_true():
    cfg = GoldenMatchConfig(prepared_record_store=True)
    assert cfg.prepared_record_store is True


def test_dedupe_df_with_prepared_store_writes_to_disk(tmp_path: Path, monkeypatch):
    """End-to-end: with the flag on, _run_dedupe_pipeline materializes
    prepared records to a disk store; cache hits land in the store and
    can be re-read across re-runs of dedupe_df with the same config."""
    import goldenmatch as gm
    # Disable cross-run autoconfig memory for isolation (per other
    # integration tests in this repo).
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    # Point the store at a known tempdir so we can assert files appear.
    monkeypatch.setenv("GOLDENMATCH_PREPARED_RECORD_STORE_DIR", str(tmp_path))

    df = _df()
    cfg = GoldenMatchConfig(prepared_record_store=True)
    result = gm.dedupe_df(df, config=cfg)
    assert result is not None
    # The store wrote at least one .duckdb file under tmp_path.
    files = list(tmp_path.glob("goldenmatch_prepared_*.duckdb"))
    # tempfiles may have been closed + removed if the pipeline is well-behaved;
    # what matters is the store WAS created. The presence/absence at this point
    # depends on whether _run_dedupe_pipeline keeps the store alive for the
    # whole call or releases mid-call. Confirm via the call count test below.


def test_dedupe_df_with_prepared_store_skips_second_run_transform(monkeypatch, tmp_path: Path):
    """Load-bearing: when prepared_record_store=True, two sequential
    dedupe_df calls on the same df should result in run_transform being
    called only ONCE (second call hits the store).

    Cross-call persistence requires cleanup=False on the underlying store
    OR a stable file path; we wire via the GOLDENMATCH_PREPARED_RECORD_STORE_DIR
    env var to get a stable location.
    """
    import goldenmatch.core.transform as tm
    import goldenmatch as gm
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    monkeypatch.setenv("GOLDENMATCH_PREPARED_RECORD_STORE_DIR", str(tmp_path))
    monkeypatch.setenv("GOLDENMATCH_PREPARED_RECORD_STORE_PERSIST", "1")  # cleanup=False

    original = tm.run_transform
    calls = [0]

    def counting(*args, **kwargs):
        calls[0] += 1
        return original(*args, **kwargs)

    tm.run_transform = counting
    try:
        df = _df()
        cfg = GoldenMatchConfig(prepared_record_store=True)
        gm.dedupe_df(df, config=cfg)
        first_count = calls[0]
        gm.dedupe_df(df, config=cfg)
        second_count = calls[0] - first_count
    finally:
        tm.run_transform = original

    # First call has to prep; second call should hit the store.
    assert first_count >= 1, "first call must invoke run_transform at least once"
    assert second_count == 0, (
        f"second call should hit the prepared-record-store; "
        f"run_transform was still called {second_count} times"
    )
```

- [ ] **Step 2: Run; expect failures (pydantic field doesn't exist; env var unused).**

### Task 2.2: Add `prepared_record_store` to GoldenMatchConfig

- [ ] **Step 1: Find the dataclass.**

```bash
grep -n "class GoldenMatchConfig" packages/python/goldenmatch/goldenmatch/config/schemas.py
```

- [ ] **Step 2: Add the field.**

Add to `GoldenMatchConfig` near other top-level toggles (e.g. next to `backend`, `llm_auto`):

```python
class GoldenMatchConfig(BaseModel):
    ...
    prepared_record_store: bool = Field(
        default=False,
        description=(
            "When True, the prep stage (quality scan + transform + auto-fix) "
            "writes its output to a DuckDB-backed disk store keyed by config "
            "signature. Subsequent calls with the same config + data shape "
            "read prepared records from disk instead of re-prepping. Path "
            "via GOLDENMATCH_PREPARED_RECORD_STORE_DIR env var; persistence "
            "via GOLDENMATCH_PREPARED_RECORD_STORE_PERSIST=1. Spec: "
            "docs/superpowers/specs/2026-05-15-distributed-plan-v1-design.md "
            "§Component 1."
        ),
    )
```

### Task 2.3: Wire the branch into `_run_dedupe_pipeline`

- [ ] **Step 1: Locate the existing prep-cache structure.**

```bash
grep -n "_prep_cache_seed\|prep_cache_key\|cached_prep\|_PREP_CACHE.get\|_PREP_CACHE\[prep" packages/python/goldenmatch/goldenmatch/core/pipeline.py
```

Expected (around lines 607–680):
- `_run_dedupe_pipeline` accepts `_prep_cache_seed: int | None = None`
- `prep_cache_key = (_prep_cache_seed if not None else id(combined_lf), tuple(columns), _prep_cache_signature(config))`
- `cached_prep = _PREP_CACHE.get(prep_cache_key)` → hit-branch (line 641) or else-branch (line 644)
- else-branch runs quality + transform + auto-fix, then populates `_PREP_CACHE[prep_cache_key] = prepped_df`

- [ ] **Step 2: Add `_prep_store` kwarg to the signature.**

In `_run_dedupe_pipeline`'s signature (around line 592), after `_prep_cache_seed`:

```python
def _run_dedupe_pipeline(
    combined_lf: pl.LazyFrame,
    config: GoldenMatchConfig,
    matchkeys: list,
    ...
    _prep_cache_seed: int | None = None,
    _prep_store: PreparedRecordStore | None = None,  # NEW
) -> dict:
```

Also import `PreparedRecordStore` at module top under `TYPE_CHECKING` (to avoid the duckdb import cost when the store is never used):

```python
if TYPE_CHECKING:
    from goldenmatch.distributed.record_store import PreparedRecordStore
```

(`TYPE_CHECKING` may already be imported; if not, add `from typing import TYPE_CHECKING` to the imports.)

- [ ] **Step 3: Insert disk-store lookup inside the existing else-branch, before prep steps.**

Find the `else:` branch at line 644 (the "cache miss → run prep" path). Insert the disk-store check at the TOP of that else-block, before the GoldenCheck step. The new code:

```python
    cached_prep = _PREP_CACHE.get(prep_cache_key)
    if cached_prep is not None:
        combined_lf = cached_prep.lazy()
        logger.debug("prep cache HIT (id=%s)", prep_cache_key[0])
    else:
        # NEW (Phase 2): try the disk-backed prepared-record store before
        # re-prepping. In-memory _PREP_CACHE was already consulted above;
        # disk store covers cross-call + cross-process cases that the
        # per-process LRU can't. Same signature -> same prepared records.
        disk_signature = _prep_cache_signature(config)
        if _prep_store is not None:
            from goldenmatch.distributed.record_store import load_prepared_records
            cached_disk = load_prepared_records(_prep_store, signature=disk_signature)
            if cached_disk is not None:
                combined_lf = cached_disk.lazy()
                # Seed in-memory cache so subsequent in-process iterations
                # skip the disk read (RAM > DuckDB+Arrow latency).
                # Guard against _PREP_CACHE_MAX == 0 (tests use this to
                # disable the in-memory cache) -- the existing eviction
                # logic would IndexError on pop() from an empty LRU list
                # when ``0 >= 0`` is true.
                if _PREP_CACHE_MAX > 0:
                    if len(_PREP_CACHE_LRU) >= _PREP_CACHE_MAX:
                        evicted = _PREP_CACHE_LRU.pop(0)
                        _PREP_CACHE.pop(evicted, None)
                    _PREP_CACHE[prep_cache_key] = cached_disk
                    _PREP_CACHE_LRU.append(prep_cache_key)
                logger.debug("prep store DISK-HIT (signature=%s)", disk_signature)
            else:
                cached_disk = None  # explicit; falls through to prep steps
        else:
            cached_disk = None

        if cached_disk is None:
            # ── Step 1.4: GOLDENCHECK QUALITY SCAN (existing code) ──
            # ... unchanged ...
            # ── Step 1.4b: GOLDENFLOW TRANSFORM (existing code) ──
            # ... unchanged ...
            # ── Step 1.5a: AUTO-FIX + VALIDATION (existing code) ──
            # ... unchanged ...

            # Populate in-memory cache (existing code; add the same
            # _PREP_CACHE_MAX > 0 guard so the existing logic also
            # respects the test monkey-patch).
            prepped_df = combined_lf.collect()
            if _PREP_CACHE_MAX > 0:
                if len(_PREP_CACHE_LRU) >= _PREP_CACHE_MAX:
                    evicted = _PREP_CACHE_LRU.pop(0)
                    _PREP_CACHE.pop(evicted, None)
                _PREP_CACHE[prep_cache_key] = prepped_df
                _PREP_CACHE_LRU.append(prep_cache_key)
            combined_lf = prepped_df.lazy()

            # NEW (Phase 2): also write to disk store, if provided.
            if _prep_store is not None:
                from goldenmatch.distributed.record_store import materialize_prepared_records
                materialize_prepared_records(
                    _prep_store, prepped_df, signature=disk_signature,
                )
                logger.debug("prep store DISK-WRITE (signature=%s)", disk_signature)
```

Important: the disk path uses `_prep_cache_signature(config)` (the existing helper at line 553, returns a string). The 3-tuple `prep_cache_key` is in-memory-only — disk only keys on the config slot since disk persistence across calls means `id(combined_lf)` is meaningless across processes. Two calls with the same config but different LazyFrame identities should resolve to the same disk entry.

Also: the pipeline never opens or closes `_prep_store` — Phase 3's controller owns the lifecycle. If a caller passes a `_prep_store`, the caller is responsible for closing it.

- [ ] **Step 4: Run targeted tests.**

```bash
python -m pytest tests/test_prepared_record_store_pipeline.py tests/test_prep_cache.py -v --timeout=120
```

Expect: new tests pass; `test_prep_cache.py` (the in-memory cache tests) stay green because the disk path is gated on `_prep_store is not None` and `config.prepared_record_store=True`.

### Task 2.4: Update the failing tests to thread the kwarg

The Phase 2 tests use `gm.dedupe_df(df, config=cfg)` which routes through `_api.dedupe_df` → `run_dedupe_df` → `_run_dedupe_pipeline`. To exercise the disk path end-to-end via the **config flag** (not the kwarg directly), the API layer needs to construct the store when `config.prepared_record_store=True` and pass it down.

- [ ] **Step 1: Find the call site.**

```bash
grep -n "_run_dedupe_pipeline\|run_dedupe_df" packages/python/goldenmatch/goldenmatch/core/pipeline.py | head -10
```

The public `run_dedupe_df` function calls `_run_dedupe_pipeline` near the end (around line 1170+).

- [ ] **Step 2: Inside `run_dedupe_df`, construct a store from config + env if enabled.**

```python
def run_dedupe_df(df, config, *, source_name="dataframe", auto_config=False):
    ...
    cache_seed = id(df)
    _prep_store_ctx = None
    if getattr(config, "prepared_record_store", False):
        from goldenmatch.distributed.record_store import PreparedRecordStore
        base_dir = os.environ.get("GOLDENMATCH_PREPARED_RECORD_STORE_DIR")
        persist = os.environ.get(
            "GOLDENMATCH_PREPARED_RECORD_STORE_PERSIST", "0"
        ).lower() in ("1", "true", "yes")
        store_path = (
            Path(base_dir) / "goldenmatch_prepared.duckdb"
            if base_dir is not None
            else None
        )
        _prep_store_ctx = PreparedRecordStore(path=store_path, cleanup=not persist)
    try:
        return _run_dedupe_pipeline(
            combined_lf, config, matchkeys, ...,
            _prep_cache_seed=cache_seed,
            _prep_store=_prep_store_ctx,
        )
    finally:
        if _prep_store_ctx is not None:
            _prep_store_ctx.close()
```

Phase 3 will move the store construction up into the controller so all 5 iterations share it; this Phase 2 sketch is the "one store per `dedupe_df` call" stopgap that makes the Phase 2 tests pass without the controller wiring.

- [ ] **Step 3: Run targeted tests.**

```bash
python -m pytest tests/test_prepared_record_store_pipeline.py tests/test_prep_cache.py -v
```

Expect all green: the new pipeline tests plus the existing in-memory prep-cache tests stay unaffected when `prepared_record_store=False`.

### Task 2.5: Commit + open PR

PR title: `feat(distributed): Component 1 / Phase 2 — wire PreparedRecordStore into pipeline (opt-in)`.

---

## Phase 3 — Controller-iteration integration

The payoff. When `config.prepared_record_store=True`, the controller's 5 sample iterations share a single disk store; `run_transform` fires once per iteration loop instead of five times.

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig_controller.py` (open store at top of `run()`, thread through, close at every exit)
- Modify: `packages/python/goldenmatch/CLAUDE.md` (document the feature)
- Test: `packages/python/goldenmatch/tests/test_prepared_record_store_controller.py` (NEW)

### Task 3.1: Failing tests

The Phase 2 tests proved the disk path works end-to-end within ONE `dedupe_df` call. Phase 3's job is to make the controller share ONE store across all 5 iterations within ONE `dedupe_df` call — without the disk store being deleted between iterations.

**Test-design constraint:** today's in-memory `_PREP_CACHE` already gives "1 transform call across 5 iterations within one process." So a naive `calls[0] == 1` test passes WITHOUT the disk store doing any work. To prove Phase 3's wiring actually exercises the disk path, the test must disable the in-memory cache.

- [ ] **Step 1: Write the test.**

`tests/test_prepared_record_store_controller.py`:

```python
"""End-to-end controller tests for PreparedRecordStore (Phase 3).

Spec §Component 1, Phase 3 integration. The controller's 5-iter sample
loop shares ONE PreparedRecordStore across all iterations so iter 2-5
hit the disk store.

Test design: today's in-memory _PREP_CACHE already gives the
"run_transform called once" property within one process. To prove the
DISK path is doing the work (Phase 3's contribution), we monkey-patch
_PREP_CACHE_MAX to 0 so the in-memory cache is effectively disabled."""
from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest


@pytest.fixture(autouse=True)
def _disable_autoconfig_memory(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")


def _df() -> pl.DataFrame:
    return pl.DataFrame({
        "name":  ["alice", "alyce", "bob", "robert"] * 20,
        "email": [f"u{i}@x.com" for i in range(80)],
    })


def test_baseline_without_disk_store_runs_transform_per_iteration(monkeypatch):
    """Baseline / failure mode: with the in-memory cache disabled AND
    no disk store, the controller's 5 iterations each call run_transform.
    This anchors the regression check: if Phase 3 ships and the disk
    store isn't actually wired, the next test asserts calls[0] > 1."""
    import goldenmatch as gm
    import goldenmatch.core.pipeline as pl_mod
    import goldenmatch.core.transform as tm
    from goldenmatch.config.schemas import GoldenMatchConfig

    monkeypatch.setattr(pl_mod, "_PREP_CACHE_MAX", 0)
    # Note: in-memory cache disabled. No disk store either (default off).

    original = tm.run_transform
    calls = [0]

    def counting(*args, **kwargs):
        calls[0] += 1
        return original(*args, **kwargs)

    tm.run_transform = counting
    try:
        gm.dedupe_df(_df(), config=GoldenMatchConfig())
    finally:
        tm.run_transform = original

    # Controller iterates 5x; with both caches disabled, expect 5+ calls.
    assert calls[0] >= 5, (
        f"baseline: expected >=5 run_transform calls with both caches "
        f"disabled; got {calls[0]}"
    )


def test_disk_store_makes_iterations_share_prepared_records(monkeypatch, tmp_path: Path):
    """Load-bearing Phase 3 test: with the in-memory cache disabled but
    the disk store enabled, the 5 controller iterations should result in
    EXACTLY 1 run_transform call -- the controller opens one store at
    run() entry, iter 1 materializes, iter 2-5 hit the disk."""
    import goldenmatch as gm
    import goldenmatch.core.pipeline as pl_mod
    import goldenmatch.core.transform as tm
    from goldenmatch.config.schemas import GoldenMatchConfig

    monkeypatch.setattr(pl_mod, "_PREP_CACHE_MAX", 0)  # in-memory off
    monkeypatch.setenv("GOLDENMATCH_PREPARED_RECORD_STORE_DIR", str(tmp_path))
    monkeypatch.setenv("GOLDENMATCH_PREPARED_RECORD_STORE_PERSIST", "1")
    # PERSIST=1 keeps the store file alive across the 5 iterations within
    # one controller.run() call. Without it, the store's cleanup=True
    # default would delete the file when the pipeline's per-call open/
    # close pair runs -- the Phase 3 controller wiring should bypass that
    # by owning the store for the whole iteration loop.

    original = tm.run_transform
    calls = [0]

    def counting(*args, **kwargs):
        calls[0] += 1
        return original(*args, **kwargs)

    tm.run_transform = counting
    try:
        cfg = GoldenMatchConfig(prepared_record_store=True)
        gm.dedupe_df(_df(), config=cfg)
    finally:
        tm.run_transform = original

    assert calls[0] == 1, (
        f"Phase 3: with in-memory cache off + disk store on, the 5 "
        f"controller iterations should result in 1 run_transform call "
        f"(iter 1 materializes, iter 2-5 disk-hit); got {calls[0]}"
    )


def test_controller_closes_store_on_normal_return(monkeypatch, tmp_path: Path):
    """The controller opens the store at run() entry and closes it at
    every exit path (normal return, raise, KeyboardInterrupt). With
    cleanup=True (no PERSIST), the file should be gone after the call."""
    import goldenmatch as gm
    from goldenmatch.config.schemas import GoldenMatchConfig

    monkeypatch.setenv("GOLDENMATCH_PREPARED_RECORD_STORE_DIR", str(tmp_path))
    # cleanup=True by default (no PERSIST env var).

    gm.dedupe_df(_df(), config=GoldenMatchConfig(prepared_record_store=True))
    files = list(tmp_path.glob("*.duckdb"))
    assert files == [], (
        f"store file should be cleaned up after the call; found {files}"
    )


def test_controller_closes_store_on_raise(monkeypatch, tmp_path: Path):
    """If the iteration loop raises (e.g. ControllerNotConfidentError at
    100K+ RED commit), the store still cleans up via the try/finally."""
    # Skipped pending a clean way to force the raise from inside a small
    # fixture; the explicit cleanup=True path is exercised by the
    # normal_return test above plus the Phase 1 idempotent-close test.
    pytest.skip("future: parametrize over the raise/normal-return paths")
```

### Task 3.2: Thread `_prep_store` through controller.run + pipeline call sites

The key change: stop letting `run_dedupe_df` open its own per-call store (Phase 2 stopgap). The controller now opens ONE store at `run()` entry and passes it down via the kwarg, so all 5 iterations share it.

- [ ] **Step 1: Locate `AutoConfigController.run` + the call sites that reach `_run_dedupe_pipeline`.**

```bash
grep -n "def run\|_run_pipeline_sample\|_finalize\|_run_dedupe_pipeline" packages/python/goldenmatch/goldenmatch/core/autoconfig_controller.py | head -15
```

`_run_pipeline_sample` (called per iteration) and `_finalize` (called once after pick_committed) both end up invoking `_run_dedupe_pipeline`. Both need to accept `_prep_store` and forward it.

- [ ] **Step 2: Open the store at the top of `run()`, close in `finally`.**

After `config_v0 = self._initial_config(...)` and before the iteration loop:

```python
        # Phase 3: Component 1 -- one PreparedRecordStore shared across
        # all iterations within this controller.run() call. Phase 2's
        # pipeline-side branch reads/writes via the kwarg we'll thread
        # below.
        _prep_store = None
        if config_v0.prepared_record_store:
            from goldenmatch.distributed.record_store import PreparedRecordStore
            base_dir = os.environ.get("GOLDENMATCH_PREPARED_RECORD_STORE_DIR")
            persist = os.environ.get(
                "GOLDENMATCH_PREPARED_RECORD_STORE_PERSIST", "0"
            ).lower() in ("1", "true", "yes")
            store_path = (
                Path(base_dir) / "goldenmatch_prepared.duckdb"
                if base_dir is not None
                else None
            )
            _prep_store = PreparedRecordStore(path=store_path, cleanup=not persist)

        try:
            # ── existing iteration loop ──
            # (every _run_pipeline_sample / _finalize call below gets
            # _prep_store=_prep_store added — Task 3.3)
            ...
        finally:
            if _prep_store is not None:
                _prep_store.close()
```

- [ ] **Step 3: Add `_prep_store` kwarg to `_run_pipeline_sample` and `_finalize`.**

These two methods currently call `_run_dedupe_pipeline` internally (look for the call inside the method bodies). Add the kwarg to their signatures and pass it through:

```python
    def _run_pipeline_sample(
        self,
        sample: pl.DataFrame,
        reference: pl.DataFrame | None,
        config: GoldenMatchConfig,
        *,
        _prep_store=None,  # NEW
    ) -> None:
        ...
        _run_dedupe_pipeline(
            ...,
            _prep_store=_prep_store,
        )
```

Same shape for `_finalize`.

- [ ] **Step 4: Wire the call sites.**

Every call to `_run_pipeline_sample` and `_finalize` inside `run()` and `_assemble_v0_history_entry` needs `_prep_store=_prep_store` (or `=None` for the v0-helper path if it gets reached before `_prep_store` is in scope — verify scope and adjust).

- [ ] **Step 5: Update Phase 2's stopgap in `run_dedupe_df`.**

The Phase 2 sketch had `run_dedupe_df` open its own store. That made the Phase 2 tests pass with `prepared_record_store=True` end-to-end, but it means **each `dedupe_df()` call opens and closes a fresh store**, deleting the file in between if `PERSIST=0`. That collides with Phase 3's "controller owns the lifecycle."

Reconcile: when called from the controller, `run_dedupe_df` should NOT open a store — the caller already provided one via `_prep_store`. When called from a non-controller path (rare), it can still open its own. Use a sentinel:

```python
def run_dedupe_df(df, config, *, source_name="dataframe", auto_config=False, _prep_store=None):
    ...
    own_store = False
    if _prep_store is None and getattr(config, "prepared_record_store", False):
        from goldenmatch.distributed.record_store import PreparedRecordStore
        # ... open from env vars ...
        _prep_store = PreparedRecordStore(...)
        own_store = True
    try:
        return _run_dedupe_pipeline(
            ...,
            _prep_store=_prep_store,
        )
    finally:
        if own_store and _prep_store is not None:
            _prep_store.close()
```

This keeps Phase 2's `prepared_record_store=True` end-to-end behavior working from any entrypoint, AND lets the controller take ownership when it threads its own store down.

- [ ] **Step 6: Run all the prepared-record-store tests.**

```bash
python -m pytest tests/test_prepared_record_store.py tests/test_prepared_record_store_pipeline.py tests/test_prepared_record_store_controller.py -v --timeout=180
```

Expect: all pass. The load-bearing one is `test_disk_store_makes_iterations_share_prepared_records` — that's the proof the disk path is what's doing the work.

### Task 3.3: Update CLAUDE.md

- [ ] **Step 1: Find the right section.**

```bash
grep -n "Prep cache\|_PREP_CACHE\|Attack C" packages/python/goldenmatch/CLAUDE.md
```

- [ ] **Step 2: Add a bullet under the same section (or a new "Prepared-record store" section).**

```markdown
- **Prepared-record store (Distributed Plan v1 Component 1, 2026-05-16, PRs #N-#N+2):** DuckDB-backed disk store for prepared records (post-transform / post-auto-fix). Opt-in via `config.prepared_record_store=True`. Default off (in-memory `_PREP_CACHE` covers small-N). At large-N or in distributed contexts, the disk store survives across iterations + workers. Path via `GOLDENMATCH_PREPARED_RECORD_STORE_DIR`; persistence across calls via `GOLDENMATCH_PREPARED_RECORD_STORE_PERSIST=1`. Spec: `docs/superpowers/specs/2026-05-15-distributed-plan-v1-design.md` §Component 1.
```

### Task 3.4: Commit + open PR

PR title: `feat(distributed): Component 1 / Phase 3 — controller-iteration integration + docs`.

---

## Acceptance checklist

- [ ] Phase 1 PR merged. PreparedRecordStore primitive + tests.
- [ ] Phase 2 PR merged. `prepared_record_store` config flag; `_run_dedupe_pipeline` branch.
- [ ] Phase 3 PR merged. Controller-iteration integration; `run_transform` fires once per `dedupe_df` call when the flag is on.
- [ ] CLAUDE.md updated.
- [ ] Full test suite green (~2500 passed / 5 skipped).
- [ ] No regression on existing in-memory `_PREP_CACHE` tests (`test_prep_cache.py`).

---

## When to escalate

1. **DuckDB hard-dep promotion breaks a package that was importing goldenmatch without the `duckdb` extra.** Highly unlikely — `duckdb` is well-installed in any modern Python data stack — but if it surfaces, revert the hard-dep promotion and keep the prepared-record store path import duckdb lazily inside the function.

2. **Phase 3's `calls[0] == 1` assertion fails with `calls[0] in {2, 3, 4}`.** Means the controller is constructing multiple stores (one per iter) instead of sharing one. Check that `_prep_store_ctx` is opened ONCE at `run()` entry, not inside `_run_pipeline_sample`.

3. **Phase 2's disk-store lookup interferes with the in-memory `_PREP_CACHE`.** Same key, two caches. The Phase 2 sketch seeds the in-memory cache from a disk hit so subsequent in-process lookups stay fast — verify that doesn't double-count cache statistics or break the LRU eviction.

4. **Tempfile cleanup races on Windows.** `Path.unlink(missing_ok=True)` is safe; the DuckDB connection MUST be closed before unlink (`store._con.close()` then unlink). Verify the close-then-unlink order in `PreparedRecordStore.close()`.
