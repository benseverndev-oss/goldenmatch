# Distributed Plan v1 — Component 3 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a key-mode dispatch path to the existing Ray backend so workers receive block-keys instead of materialized `BlockResult` dfs. Workers call `load_block` on the Component 2 disk store, return pairs to driver via `ray.get`. Distributes memory, not just CPU.

**Architecture:** All code in `goldenmatch/backends/ray_backend.py` plus a `read_only` kwarg added to Component 1's `PreparedRecordStore`. `score_blocks_ray` keeps its current signature; new `store_path` / `signature` kwargs route to **key-mode** when set + `len(blocks) > 4`. Otherwise: today's df-mode path. Pipeline.py gains one small block to pass the kwargs when all three gating flags (`backend=="ray"` + `prepared_record_store=True` + `partitioned_block_scoring=True`) are on.

**Tech Stack:** Python 3.12, Ray (optional dep already wired via `goldenmatch[ray]`), DuckDB (hard dep), Polars + PyArrow, `psutil>=5.9` (hard dep), pytest.

**Spec:** [`docs/superpowers/specs/2026-05-16-distributed-plan-component-3-distributed-scoring-design.md`](../specs/2026-05-16-distributed-plan-component-3-distributed-scoring-design.md).

---

## File structure

**Modified files:**

| Path | Phase | Change |
|---|---|---|
| `packages/python/goldenmatch/goldenmatch/distributed/record_store.py` | 1 | Add `read_only: bool = False` kwarg to `PreparedRecordStore.__init__`; forward to `duckdb.connect(..., read_only=read_only)` |
| `packages/python/goldenmatch/goldenmatch/backends/ray_backend.py` | 2-3 | Add `_KeyModeBlock` dataclass; add `_score_block_remote_by_key` Ray task; add key-mode branch to `score_blocks_ray`; add `store_path` + `signature` kwargs; driver-OOM guard with incremental `ray.wait` gather |
| `packages/python/goldenmatch/goldenmatch/core/pipeline.py` | 5 | At the existing `block_scorer = _get_block_scorer(config)` call site, pass `store_path` + `signature` kwargs when all three gating flags are on |

**Created files:**

| Path | Phase | Responsibility |
|---|---|---|
| `packages/python/goldenmatch/tests/test_distributed_scoring.py` | 2-4 | All Component 3 tests: unit + Ray integration + cross-process concurrency |
| `packages/python/goldenmatch/scripts/bench_distributed_stack.py` | 6 | 5M baseline-vs-treatment bench script (kill checkpoint) |
| `.github/workflows/bench-distributed-stack.yml` | 6 | `workflow_dispatch`, `large-new-64GB`, 180-min timeout |

---

## Pre-flight checklist

Before starting any phase:

- [ ] On clean branch off `main`: `git fetch origin main && git switch -c distributed-plan-c3-phase-N origin/main`. Each phase branches off main (not off prior phase) per [[stacked-PR auto-closure rule]] in root CLAUDE.md.
- [ ] Editable install: `python -c "import goldenmatch; print(goldenmatch.__file__)"` resolves to the worktree, not site-packages.
- [ ] Baseline green: `cd packages/python/goldenmatch && python -m pytest tests/test_prepared_record_store.py tests/test_block_partitioned_store.py tests/test_partitioned_block_scoring_pipeline.py -v` → 20 passed (Component 1+2 surface).
- [ ] Ray available locally: `python -c "import ray; print(ray.__version__)"`. If `ImportError`: `uv pip install -e "packages/python/goldenmatch[ray]"` (workspace-correct form; plain `uv pip install ray` works too but installs a non-workspace ray). CI handles the install via the `[ray]` extra in the bench workflow.

---

## Phase 1 — `read_only` kwarg on `PreparedRecordStore`

Lowest-risk prerequisite. Component 1's `PreparedRecordStore.__init__` currently always opens read/write. Workers in Phase 2+ need `read_only=True` so multiple processes can open the same `.duckdb` file without write-lock contention.

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/distributed/record_store.py`
- Test: `packages/python/goldenmatch/tests/test_prepared_record_store.py` (extend existing)

### Task 1.1: Write failing tests

- [ ] **Step 1: Append three tests to `tests/test_prepared_record_store.py`** at the end of the file:

```python
def test_read_only_kwarg_default_is_false(tmp_path: Path):
    """Default open is read/write -- existing callers (Component 1+2) unaffected."""
    store = PreparedRecordStore(base_dir=tmp_path)
    try:
        # Writing should succeed.
        materialize_prepared_records(store, _sample_df(), signature="sig-v1")
        assert load_prepared_records(store, signature="sig-v1") is not None
    finally:
        store.close()


def test_read_only_true_allows_read(tmp_path: Path):
    """Driver materializes, then workers open read-only and load_block succeeds."""
    # Driver pass: write.
    with PreparedRecordStore(base_dir=tmp_path, cleanup=False) as driver:
        materialize_prepared_records(driver, _sample_df(), signature="sig-v1")
        driver_path = driver.path
    # Worker pass: read-only.
    worker = PreparedRecordStore(path=driver_path, cleanup=False, read_only=True)
    try:
        loaded = load_prepared_records(worker, signature="sig-v1")
        assert loaded is not None
        assert loaded.height == 4
    finally:
        worker.close()


def test_read_only_true_rejects_writes(tmp_path: Path):
    """A read-only store must raise when materialize_prepared_records tries to
    write. The exact error class is DuckDB-version dependent; assert the
    write call raises *some* exception (regression anchor)."""
    # First, seed the file from a writable driver.
    with PreparedRecordStore(base_dir=tmp_path, cleanup=False) as driver:
        materialize_prepared_records(driver, _sample_df(), signature="seed")
        path = driver.path
    # Now open read-only and try to write -- must raise.
    ro = PreparedRecordStore(path=path, cleanup=False, read_only=True)
    try:
        with pytest.raises(Exception):  # noqa: B017 -- DuckDB-version-dependent class
            materialize_prepared_records(ro, _sample_df(), signature="should-fail")
    finally:
        ro.close()
```

- [ ] **Step 2: Run to confirm failure.**

```bash
cd packages/python/goldenmatch
python -m pytest tests/test_prepared_record_store.py -v -k "read_only"
```

Expected: 3 failures with `TypeError: __init__() got an unexpected keyword argument 'read_only'`.

### Task 1.2: Add the kwarg

- [ ] **Step 1: Modify `PreparedRecordStore.__init__`.**

In `packages/python/goldenmatch/goldenmatch/distributed/record_store.py`, locate the existing `__init__` signature. Today's signature is:

```python
def __init__(
    self,
    *,
    base_dir: Path | str | None = None,
    path: Path | str | None = None,
    cleanup: bool = True,
) -> None:
```

Change to:

```python
def __init__(
    self,
    *,
    base_dir: Path | str | None = None,
    path: Path | str | None = None,
    cleanup: bool = True,
    read_only: bool = False,
) -> None:
```

Find the `duckdb.connect(str(self.path))` call inside `__init__` and change it to:

```python
self._con: duckdb.DuckDBPyConnection | None = duckdb.connect(
    str(self.path), read_only=read_only,
)
```

- [ ] **Step 2: Run tests.**

```bash
python -m pytest tests/test_prepared_record_store.py -v
```

Expected: 12 passed (9 existing + 3 new).

### Task 1.3: Commit + open PR

- [ ] **Step 1: Commit.**

```bash
git add packages/python/goldenmatch/goldenmatch/distributed/record_store.py packages/python/goldenmatch/tests/test_prepared_record_store.py
git commit -m "feat(distributed): PreparedRecordStore read_only kwarg (Component 3 prereq)

Adds read_only: bool = False kwarg to PreparedRecordStore.__init__.
Forwards to duckdb.connect(read_only=...). When True, multiple
processes can open the same .duckdb file concurrently without
write-lock contention -- prereq for Component 3 worker-side reads.

Driver (single writer) stays read_only=False (the default).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 2: Push + open PR.**

```bash
gh auth switch --user benzsevern
GH_TOKEN=$(gh auth token --user benzsevern) git push -u origin distributed-plan-c3-phase-1
GH_TOKEN=$(gh auth token --user benzsevern) gh pr create --base main --title "feat(distributed): Component 3 Phase 1 -- PreparedRecordStore read_only kwarg" --body "<see plan>"
gh auth switch --user benzsevern-mjh
```

PR title: `feat(distributed): Component 3 / Phase 1 — PreparedRecordStore read_only kwarg (prereq)`.

---

## Phase 2 — `_KeyModeBlock` + key-mode branch in `score_blocks_ray`

The heart of Component 3. Adds the new Ray task body and the routing.

**Existing-code constraint discovered during review:** today's `_score_block_remote` is defined **inside** `score_blocks_ray` (at line ~92, decorated with `@ray.remote`) — it is NOT a module-level symbol. We can't monkeypatch it at the module level. Two consequences:

1. The new `_score_block_remote_by_key` follows the same in-function pattern (defined inside `score_blocks_ray` alongside the existing one). No module-level singletons. No `_make_key_mode_remote` factory.
2. Phase 2 unit tests cover ONLY the `_KeyModeBlock` shim. All dispatch-routing verification is deferred to Phase 4 (real Ray local mode), which exercises the actual code path end-to-end. Trying to monkeypatch a `@ray.remote`-decorated function defined inside another function is more lying than testing.

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/backends/ray_backend.py`
- Test: `packages/python/goldenmatch/tests/test_distributed_scoring.py` (NEW)

### Task 2.1: Write failing unit tests

- [ ] **Step 1: Create the test file.**

`packages/python/goldenmatch/tests/test_distributed_scoring.py`:

```python
"""Unit + integration tests for Component 3 (distributed scoring).

All tests gated on `ray` being importable; the file's collection
falls through (no errors) when the [ray] extra isn't installed.
"""
from __future__ import annotations

import multiprocessing as mp
import sys
from pathlib import Path

import polars as pl
import pytest

ray = pytest.importorskip("ray")


# ── Shared fixtures + helpers (used by Phase 2, 3, and 4 tests) ─────


@pytest.fixture(scope="module")
def _ray_local():
    """Module-scoped Ray init so we pay startup once across all integration
    tests. ignore_reinit_error in case earlier tests already touched ray."""
    ray.init(
        local_mode=False, ignore_reinit_error=True,
        num_cpus=2, logging_level="WARNING",
    )
    yield
    ray.shutdown()


def _build_small_blocks(tmp_path: Path):
    """Materialize a small df to a PreparedRecordStore split across 5 blocks
    of 1-2 rows each. Returns (store_path, signature, blocks list).

    Total blocks > 4 so the small-block fast path doesn't engage.
    Used by Phase 3 (OOM guard) and Phase 4 (integration) tests.
    """
    from goldenmatch.core.blocker import BlockResult
    from goldenmatch.distributed.record_store import (
        PreparedRecordStore,
        materialize_blocks,
        materialize_prepared_records,
    )
    # Every block has 2 rows that share __mk_name__, so every block
    # emits at least 1 pair. Required for the Phase 3 OOM test to be
    # deterministic: if any block returned 0 pairs, the cumulative
    # pair counter could stay at 0 long enough for the loop to drain
    # without tripping the guard. 5 multi-row blocks > 4 -> key-mode
    # engages (no small-block fallback).
    df = pl.DataFrame({
        "__row_id__":  [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
        "name":        ["alice", "alice2", "bob", "bob2", "carol", "carol2", "dan", "dan2", "eve", "eve2"],
        "__mk_name__": ["alice", "alice",  "bob", "bob",  "carol", "carol",  "dan", "dan",  "eve", "eve"],
    })
    block_assignments = {
        0: "A", 1: "A",
        2: "B", 3: "B",
        4: "C", 5: "C",
        6: "D", 7: "D",
        8: "E", 9: "E",
    }

    store_path = tmp_path / "store.duckdb"
    with PreparedRecordStore(path=store_path, cleanup=False) as store:
        materialize_prepared_records(store, df, signature="sig-v1")
        materialize_blocks(
            store, df, block_assignments=block_assignments, signature="sig-v1",
        )

    # Build BlockResult shells. df-mode reads .df; key-mode ignores it.
    # BlockResult requires `strategy=` per its dataclass; pass "static".
    blocks = [
        BlockResult(
            block_key=k,
            df=df.filter(
                pl.col("__row_id__").is_in(
                    [r for r, v in block_assignments.items() if v == k]
                )
            ).lazy(),
            strategy="static",
        )
        for k in sorted(set(block_assignments.values()))
    ]
    return str(store_path), "sig-v1", blocks


# ── Unit tests (no real Ray runtime) ────────────────────────────────

def test_key_mode_block_shim_exposes_required_attributes():
    """_KeyModeBlock must satisfy the _score_one_block contract: .block_key
    (str) and .df (DataFrame). Module-level dataclass so Ray pickling
    resolves it; a nested class breaks serialization."""
    from goldenmatch.backends.ray_backend import _KeyModeBlock
    df = pl.DataFrame({"__row_id__": [0], "name": ["a"]})
    block = _KeyModeBlock(block_key="key-1", df=df)
    assert block.block_key == "key-1"
    assert block.df is df
    # Frozen dataclass: assignment must raise.
    with pytest.raises(Exception):  # noqa: B017 -- frozen dataclass error class
        block.block_key = "mutated"


def test_pair_bytes_estimate_constant_is_module_level():
    """_PAIR_BYTES_ESTIMATE must be importable + finite + > 0 (Phase 3
    OOM guard depends on it). Anchors the constant against accidental
    deletion."""
    from goldenmatch.backends.ray_backend import _PAIR_BYTES_ESTIMATE
    assert isinstance(_PAIR_BYTES_ESTIMATE, int)
    assert _PAIR_BYTES_ESTIMATE > 0


def test_score_blocks_ray_signature_accepts_new_kwargs():
    """score_blocks_ray must accept store_path + signature kwargs without
    raising TypeError, even when ray isn't actually initialized. Achieved
    by short-circuiting on empty blocks list (returns [] early)."""
    from goldenmatch.backends import ray_backend
    result = ray_backend.score_blocks_ray(
        [], mk=None, matched_pairs=set(),
        store_path="/tmp/store.duckdb",
        signature="sig-v1",
    )
    assert result == []
```

- [ ] **Step 2: Run to confirm failure.**

```bash
python -m pytest tests/test_distributed_scoring.py -v
```

Expected: 3 failures — `_KeyModeBlock` not defined, `_PAIR_BYTES_ESTIMATE` not defined, `score_blocks_ray` doesn't accept `store_path` kwarg.

### Task 2.2: Implement the module-level pieces (shim + constant)

- [ ] **Step 1: Add module-level constants + dataclass at the top of `ray_backend.py`.**

After the existing imports and the `_ensure_ray()` function but BEFORE `score_blocks_ray`:

```python
from dataclasses import dataclass
import polars as pl


_PAIR_BYTES_ESTIMATE = 80
"""Approx bytes per scored pair (3-tuple of int, int, float) in a flat
list. CPython tuple header ~56 bytes + ints + float. Conservative;
underestimating would let the driver-OOM guard fire late.

Used by Phase 3's incremental ray.wait gather to project cumulative
pair memory against psutil.virtual_memory().available * 0.5.
"""


@dataclass(frozen=True)
class _KeyModeBlock:
    """Minimal block shim used by the key-mode Ray task (defined inside
    score_blocks_ray).

    _score_one_block (core/scorer.py) only reads .block_key + .df; this
    matches that contract without dragging in BlockResult's multi-pass
    fields (strategy, depth, parent_key, pre_scored_pairs) which
    key-mode v1 doesn't support. Module-level so Ray pickling resolves
    it on workers — a nested class breaks serialization.
    """
    block_key: str
    df: pl.DataFrame
```

### Task 2.3: Add the empty-blocks short-circuit + new kwargs

- [ ] **Step 1: Modify `score_blocks_ray` signature.**

Current (in `ray_backend.py` around line 42):

```python
def score_blocks_ray(
    blocks: list,
    mk: MatchkeyConfig,
    matched_pairs: set[tuple[int, int]],
    across_files_only: bool = False,
    source_lookup: dict[int, str] | None = None,
    target_ids: set[int] | None = None,
) -> list[tuple[int, int, float]]:
```

Change to:

```python
def score_blocks_ray(
    blocks: list,
    mk: MatchkeyConfig,
    matched_pairs: set[tuple[int, int]],
    across_files_only: bool = False,
    source_lookup: dict[int, str] | None = None,
    target_ids: set[int] | None = None,
    *,
    store_path: str | None = None,
    signature: str | None = None,
) -> list[tuple[int, int, float]]:
```

The Phase 2 unit test for the new kwargs relies on a `if not blocks: return []` early-return BEFORE `_ensure_ray()` is called. The existing code (line 69-70 of `ray_backend.py`) already has it in the right place — verify with `grep -n "if not blocks" packages/python/goldenmatch/goldenmatch/backends/ray_backend.py` and confirm the match is above the `_ensure_ray()` call. If for any reason it isn't, move it. This is a hard precondition; the unit tests fail otherwise.

### Task 2.4: Add the in-function `_score_block_remote_by_key` task + key-mode branch

The task is defined inside `score_blocks_ray` alongside the existing `_score_block_remote` (same pattern, same `@ray.remote` decorator scope). The dispatch decision (`use_key_mode`) lives at the call site.

- [ ] **Step 1: Inside `score_blocks_ray`, alongside the existing `@ray.remote def _score_block_remote(...)`, add the new task.**

After the existing task definition (around line 99) and BEFORE the existing fan-out loop:

```python
    @ray.remote(max_retries=0)
    def _score_block_remote_by_key(
        block_key: str,
        store_path_inner: str,
        signature_inner: str,
        mk_config,
        exclude,
        src_lookup,
        across_only: bool,
    ):
        """Component 3 key-mode Ray task: worker opens the store and
        loads its block by key. Driver only ships strings."""
        from goldenmatch.core.scorer import _score_one_block
        from goldenmatch.distributed.record_store import (
            PreparedRecordStore,
            load_block,
        )

        store = PreparedRecordStore(
            path=store_path_inner, cleanup=False, read_only=True,
        )
        try:
            block_df = load_block(
                store, signature=signature_inner, block_key=block_key,
            )
            if block_df is None:
                raise RuntimeError(
                    f"Component 3: block_key={block_key!r} not found in "
                    f"store at {store_path_inner} for signature="
                    f"{signature_inner!r} -- likely cause is signature "
                    f"drift between driver and worker (config mutated "
                    f"mid-run) or off-by-one in block_assignments"
                )
            shim = _KeyModeBlock(block_key=block_key, df=block_df)
            return _score_one_block(
                shim, mk_config, exclude,
                across_files_only=across_only, source_lookup=src_lookup,
            )
        finally:
            store.close()
```

- [ ] **Step 2: Replace the existing fan-out loop with the branched dispatch.**

Find the existing loop (around line 102-121) that builds `futures` via `_score_block_remote.remote(...)`. Replace it with:

```python
    use_key_mode = store_path is not None and signature is not None

    futures = []
    if use_key_mode:
        for block in blocks:
            future = _score_block_remote_by_key.remote(
                block.block_key, store_path, signature,
                mk_ref, exclude_ref, source_ref,
                across_files_only,
            )
            futures.append(future)
    else:
        for block in blocks:
            # Collect the lazy DataFrame before sending to Ray (existing
            # df-mode behavior, preserved verbatim).
            if hasattr(block, "df") and hasattr(block.df, "collect"):
                collected_block = type(block)(
                    block_key=block.block_key,
                    df=block.df.collect().lazy(),
                    strategy=block.strategy,
                    depth=getattr(block, "depth", 0),
                    parent_key=getattr(block, "parent_key", None),
                    pre_scored_pairs=getattr(block, "pre_scored_pairs", None),
                )
            else:
                collected_block = block

            future = _score_block_remote.remote(
                collected_block, mk_ref, exclude_ref,
                across_files_only, source_ref,
            )
            futures.append(future)

    logger.info(
        "Submitted %d blocks to Ray (%s mode, %d CPUs available)",
        len(futures),
        "key" if use_key_mode else "df",
        int(ray.cluster_resources().get("CPU", 0)),
    )
```

Keep the existing `ray.get(futures)` gather + the `target_ids` filter + `matched_pairs.add(...)` loop as-is. Phase 3 replaces the gather with the incremental guard.

- [ ] **Step 3: Run unit tests.**

```bash
cd packages/python/goldenmatch
python -m pytest tests/test_distributed_scoring.py -v
```

Expected: 3 passed.

### Task 2.5: Commit + open PR

- [ ] **Step 1: Commit.**

```bash
git add packages/python/goldenmatch/goldenmatch/backends/ray_backend.py packages/python/goldenmatch/tests/test_distributed_scoring.py
git commit -m "feat(distributed): Component 3 / Phase 2 -- key-mode Ray dispatch

Adds _KeyModeBlock module-level dataclass + _PAIR_BYTES_ESTIMATE
constant + in-function _score_block_remote_by_key Ray task + key-mode
branch in score_blocks_ray. New store_path + signature kwargs; when
both set, workers receive block_keys instead of materialized
BlockResult.df objects.

The key-mode task is defined inside score_blocks_ray alongside the
existing _score_block_remote (same @ray.remote scope pattern). Unit
tests cover only the module-level _KeyModeBlock shim + constant +
new kwargs accepted; full dispatch verification happens in Phase 4
with real Ray local mode.

Phase 3 replaces the existing ray.get() gather with an incremental
ray.wait() loop + driver-OOM guard.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 2: Push + PR.** Same auth dance as Phase 1.

PR title: `feat(distributed): Component 3 / Phase 2 — key-mode Ray dispatch`.

---

## Phase 3 — Driver-OOM guard

Replace the existing `ray.get([futures])` with an incremental `ray.wait` loop. Cancel remaining futures + raise `MemoryError` when cumulative pair count would exceed `psutil.virtual_memory().available * 0.5 / _PAIR_BYTES_ESTIMATE`.

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/backends/ray_backend.py`
- Test: `packages/python/goldenmatch/tests/test_distributed_scoring.py` (extend)

### Task 3.1: Failing test

The Phase 2 plan-review caught that monkeypatching the in-function `_score_block_remote_by_key` doesn't work. For Phase 3, we test the OOM guard via the **integration path** — a tiny Ray local-mode run with `psutil.virtual_memory` patched to claim near-zero memory.

- [ ] **Step 1: Append to `tests/test_distributed_scoring.py`** (the `_ray_local` fixture and `_build_small_blocks` helper were added in Phase 2):

```python
def test_driver_oom_guard_raises_when_budget_exceeded(_ray_local, tmp_path: Path, monkeypatch):
    """End-to-end: with psutil claiming near-zero available memory, the
    incremental gather must trip the OOM guard and raise MemoryError
    citing 'scored pairs'.

    Uses real Ray + real PreparedRecordStore so the guard's interaction
    with ray.wait + ray.cancel + ray.get is exercised, not stubbed."""
    import psutil
    from goldenmatch.backends import ray_backend
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField

    store_path, sig, blocks = _build_small_blocks(tmp_path)
    mk = MatchkeyConfig(
        name="name_exact", type="weighted", threshold=0.5,
        fields=[MatchkeyField(field="name", scorer="exact", weight=1.0)],
    )

    # Pretend the system has 80 bytes of available memory total ->
    # budget_pairs = 80 * 0.5 / 80 = 0. Any non-empty pair list trips.
    class FakeMem:
        available = 80
    monkeypatch.setattr(psutil, "virtual_memory", lambda: FakeMem)

    with pytest.raises(MemoryError, match="scored pairs"):
        ray_backend.score_blocks_ray(
            blocks, mk, set(),
            store_path=store_path, signature=sig,
        )


def test_driver_oom_guard_passes_under_normal_memory(_ray_local, tmp_path: Path):
    """Sanity: with real psutil reporting actual system memory, the
    guard does NOT fire on a tiny test fixture. Anchors that the guard
    isn't pathologically tight."""
    from goldenmatch.backends import ray_backend
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField

    store_path, sig, blocks = _build_small_blocks(tmp_path)
    mk = MatchkeyConfig(
        name="name_exact", type="weighted", threshold=0.5,
        fields=[MatchkeyField(field="name", scorer="exact", weight=1.0)],
    )
    # Should return normally (pairs depend on fixture content; just
    # assert no exception).
    pairs = ray_backend.score_blocks_ray(
        blocks, mk, set(),
        store_path=store_path, signature=sig,
    )
    assert isinstance(pairs, list)
```

- [ ] **Step 2: Run; expect fail** (MemoryError not raised because there's no guard yet).

```bash
cd packages/python/goldenmatch
python -m pytest tests/test_distributed_scoring.py::test_driver_oom_guard_raises_when_budget_exceeded -v
```

### Task 3.2: Implement the incremental guard

- [ ] **Step 1: Replace the gather in `score_blocks_ray`.**

In `ray_backend.py`, replace the existing `pairs_per_block = ray.get(futures)` line with:

```python
    import psutil
    budget_bytes = psutil.virtual_memory().available * 0.5
    budget_pairs = int(budget_bytes // _PAIR_BYTES_ESTIMATE)

    all_pairs: list[tuple[int, int, float]] = []
    remaining = list(futures)
    n_pairs = 0
    while remaining:
        ready, remaining = ray.wait(remaining, num_returns=1)
        block_pairs = ray.get(ready[0])
        all_pairs.extend(block_pairs)
        n_pairs += len(block_pairs)
        if n_pairs > budget_pairs:
            for f in remaining:
                try:
                    ray.cancel(f)
                except Exception:  # noqa: BLE001 -- best-effort cleanup
                    pass
            raise MemoryError(
                f"Component 3: scored pairs ({n_pairs:,}) would exceed "
                f"50% of available driver RAM "
                f"({int(budget_bytes // (1024 * 1024))} MB budget, "
                f"~{_PAIR_BYTES_ESTIMATE} bytes/pair) — switch to "
                f"backend='chunked' or wait for Component 4 "
                f"(streaming pair store)"
            )
    return all_pairs
```

Remove any prior return statement that used `ray.get([futures])`.

- [ ] **Step 2: Run the guard test + the Phase 2 tests together.**

```bash
python -m pytest tests/test_distributed_scoring.py -v
```

Expected: 5 passed (4 from Phase 2 + 1 new).

### Task 3.3: Commit + open PR

- [ ] **Step 1: Commit.**

```bash
git add packages/python/goldenmatch/goldenmatch/backends/ray_backend.py packages/python/goldenmatch/tests/test_distributed_scoring.py
git commit -m "feat(distributed): Component 3 / Phase 3 -- driver-OOM guard

Replaces ray.get([futures]) with an incremental ray.wait loop. After
each completed task, cumulative scored-pair count is compared to a
budget: psutil.virtual_memory().available * 0.5 / 80 bytes/pair.
When the cumulative count exceeds budget, remaining futures are
cancelled (best-effort) and the driver raises MemoryError with a
message pointing to backend='chunked' or Component 4.

The 80-byte/pair estimate accounts for CPython tuple+int+float
overhead in a flat list. Conservative; underestimating would let
the guard fire too late.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 2: Push + PR.**

PR title: `feat(distributed): Component 3 / Phase 3 — driver-OOM guard`.

---

## Phase 4 — Ray integration + cross-process concurrent-read tests

Real Ray runtime (not monkeypatched). Tests live in the same file but use `ray.init(local_mode=True)` so they actually exercise the task pickling and worker code paths.

**Files:**
- Test: `packages/python/goldenmatch/tests/test_distributed_scoring.py` (extend with 3 integration tests + 1 cross-process test)

### Task 4.1: Add real-Ray equivalence + block-not-found tests

**Note:** `_ray_local` fixture and `_build_small_blocks` helper were added to the top of `test_distributed_scoring.py` in Phase 2. This phase reuses them.

- [ ] **Step 1: Append to `tests/test_distributed_scoring.py`:**

```python
def test_key_mode_equivalence_with_df_mode(_ray_local, tmp_path: Path):
    """Same input → same pairs whether key-mode or df-mode. Locks in the
    semantic invariant; without this, key-mode silently producing
    different pairs would be unobservable."""
    from goldenmatch.backends import ray_backend
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField

    store_path, sig, blocks = _build_small_blocks(tmp_path)
    mk = MatchkeyConfig(
        name="name_exact",
        type="weighted",
        threshold=0.5,
        fields=[MatchkeyField(field="name", scorer="exact", weight=1.0)],
    )

    pairs_df_mode = ray_backend.score_blocks_ray(blocks, mk, set())
    pairs_key_mode = ray_backend.score_blocks_ray(
        blocks, mk, set(),
        store_path=store_path, signature=sig,
    )

    # Pairs may arrive in different orders; compare as sets after
    # canonicalizing (id_a, id_b) order.
    def canon(p):
        a, b, s = p
        return (min(a, b), max(a, b), round(s, 6))
    assert {canon(p) for p in pairs_key_mode} == {canon(p) for p in pairs_df_mode}


def test_key_mode_block_not_found_raises_runtime_error(_ray_local, tmp_path: Path):
    """Pass a wrong signature; worker raises RuntimeError citing both
    likely root causes (signature drift, block_assignments off-by-one)."""
    from goldenmatch.backends import ray_backend
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField

    store_path, _good_sig, blocks = _build_small_blocks(tmp_path)
    mk = MatchkeyConfig(
        name="name_exact",
        type="weighted",
        threshold=0.5,
        fields=[MatchkeyField(field="name", scorer="exact", weight=1.0)],
    )

    with pytest.raises(Exception) as exc_info:
        ray_backend.score_blocks_ray(
            blocks, mk, set(),
            store_path=store_path, signature="WRONG-SIG",
        )
    msg = str(exc_info.value)
    assert "signature drift" in msg
    assert "block_assignments" in msg
```

### Task 4.2: Cross-process concurrent-read test (Windows-focused)

- [ ] **Step 1: Append to the same file:**

```python
def _worker_open_and_read(store_path: str, signature: str, block_key: str, queue):
    """Top-level (picklable) target for multiprocessing.Process.

    Opens a read-only PreparedRecordStore, calls load_block, puts the
    row count (or exception) on the queue.
    """
    try:
        from goldenmatch.distributed.record_store import (
            PreparedRecordStore,
            load_block,
        )
        store = PreparedRecordStore(path=store_path, cleanup=False, read_only=True)
        try:
            df = load_block(store, signature=signature, block_key=block_key)
            queue.put(("ok", df.height if df is not None else None))
        finally:
            store.close()
    except Exception as e:  # noqa: BLE001 -- preserve in the queue
        queue.put(("err", repr(e)))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific concurrent-read regression anchor")
def test_two_processes_can_read_same_store_concurrently(tmp_path: Path):
    """Spawn 2 multiprocessing.Process workers that each open the same
    DuckDB file read-only and call load_block simultaneously. Both must
    succeed. This is the direct test of the spec §Error handling §3
    Windows concurrent-read concern; Ray's local mode might serialize
    workers and paper over the issue."""
    store_path, sig, _ = _build_small_blocks(tmp_path)

    ctx = mp.get_context("spawn")
    q1 = ctx.Queue()
    q2 = ctx.Queue()
    p1 = ctx.Process(target=_worker_open_and_read, args=(store_path, sig, "A", q1))
    p2 = ctx.Process(target=_worker_open_and_read, args=(store_path, sig, "B", q2))
    p1.start(); p2.start()
    p1.join(timeout=30); p2.join(timeout=30)

    r1 = q1.get(timeout=5)
    r2 = q2.get(timeout=5)
    assert r1[0] == "ok", f"worker 1 failed: {r1}"
    assert r2[0] == "ok", f"worker 2 failed: {r2}"
    assert r1[1] is not None and r1[1] > 0
    assert r2[1] is not None and r2[1] > 0
```

- [ ] **Step 2: Run integration tests.**

```bash
python -m pytest tests/test_distributed_scoring.py -v --timeout=120
```

Expected: all pass on Windows; on Linux the cross-process test is skipped, others run.

### Task 4.3: Commit + open PR

- [ ] **Step 1: Commit.**

```bash
git add packages/python/goldenmatch/tests/test_distributed_scoring.py
git commit -m "test(distributed): Component 3 / Phase 4 -- Ray integration + concurrent reads

Adds 3 real-Ray integration tests + 1 cross-process concurrent-read test
(Windows-only). The equivalence test locks in the semantic invariant
that key-mode produces the same pairs as df-mode on identical input;
the block-not-found test asserts the error message cites both root
causes (signature drift, block_assignments off-by-one); the cross-
process test exercises the spec §Error handling §3 Windows concurrent-
read concern directly (independent of Ray's local-mode worker
scheduling).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 2: Push + PR.**

PR title: `test(distributed): Component 3 / Phase 4 — Ray integration + concurrent reads`.

---

## Phase 5 — Pipeline.py wiring

The driver-side call site that passes `store_path` + `signature` to `score_blocks_ray` when all three gating flags are on.

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/pipeline.py`
- Test: `packages/python/goldenmatch/tests/test_partitioned_block_scoring_pipeline.py` (extend, NOT a new file)

### Task 5.1: Failing tests

- [ ] **Step 1: Append to `tests/test_partitioned_block_scoring_pipeline.py`:**

```python
def test_pipeline_passes_store_path_when_all_flags_on(tmp_path: Path, monkeypatch):
    """When backend=ray + prepared_record_store + partitioned_block_scoring
    are all on, the pipeline must pass store_path + signature kwargs to
    score_blocks_ray. Monkeypatch score_blocks_ray to record kwargs —
    we don't need ray actually installed to assert pipeline-side wiring."""
    import goldenmatch as gm
    from goldenmatch.core.autoconfig import auto_configure_df
    import goldenmatch.core.pipeline as pl_mod

    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    monkeypatch.setenv("GOLDENMATCH_PREPARED_RECORD_STORE_DIR", str(tmp_path))
    monkeypatch.setenv("GOLDENMATCH_PREPARED_RECORD_STORE_PERSIST", "1")

    df = _diverse_df()
    cfg = auto_configure_df(df, confidence_required=False)
    cfg.prepared_record_store = True
    cfg.partitioned_block_scoring = True
    cfg.backend = "ray"

    captured: dict = {}
    def fake_score_blocks_ray(blocks, mk, matched_pairs, **kwargs):
        captured.update(kwargs)
        return []

    # The pipeline imports score_blocks_ray via _get_block_scorer, which
    # may resolve to a different name at runtime. Patch at the module
    # import point.
    monkeypatch.setattr(
        "goldenmatch.backends.ray_backend.score_blocks_ray",
        fake_score_blocks_ray,
        raising=False,
    )
    monkeypatch.setattr(
        "goldenmatch.core.pipeline._get_block_scorer",
        lambda config: fake_score_blocks_ray,
    )

    gm.dedupe_df(df, config=cfg, confidence_required=False)

    assert "store_path" in captured, (
        f"pipeline must pass store_path kwarg to score_blocks_ray when "
        f"all three flags are on; got kwargs={captured!r}"
    )
    assert "signature" in captured
    assert captured["store_path"] is not None
    assert captured["signature"] is not None


def test_pipeline_does_not_pass_store_path_when_disk_store_off(tmp_path: Path, monkeypatch):
    """backend=ray but prepared_record_store=False → no store_path kwarg.
    Ensures df-mode is unaffected for users who picked Ray but not the
    disk store."""
    import goldenmatch as gm
    from goldenmatch.core.autoconfig import auto_configure_df

    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")

    df = _diverse_df()
    cfg = auto_configure_df(df, confidence_required=False)
    cfg.prepared_record_store = False
    cfg.partitioned_block_scoring = False
    cfg.backend = "ray"

    captured: dict = {}
    def fake_score_blocks_ray(blocks, mk, matched_pairs, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(
        "goldenmatch.core.pipeline._get_block_scorer",
        lambda config: fake_score_blocks_ray,
    )

    gm.dedupe_df(df, config=cfg, confidence_required=False)

    assert captured.get("store_path") is None
    assert captured.get("signature") is None
```

- [ ] **Step 2: Run; expect fail.**

```bash
python -m pytest tests/test_partitioned_block_scoring_pipeline.py -v --timeout=120
```

### Task 5.2: Wire the pipeline

**Scope clarification (verified during plan review):** `pipeline.py` has TWO `block_scorer` call sites:

- Line ~865 inside `_run_dedupe_pipeline` (line 596). **THIS is the one Phase 5 wires** — it has `_prep_store` in scope (added in Component 1 Phase 2 / PR #281).
- Line ~1468 inside `_run_match_pipeline` (line 1344). `_prep_store` is NOT in scope here. Component 3 does NOT fire on the match path; that's a v2 follow-up if match-mode users want the disk-store benefits.

- [ ] **Step 1: Locate the existing scoring call site.**

```bash
grep -n "block_scorer = _get_block_scorer\|block_scorer(" packages/python/goldenmatch/goldenmatch/core/pipeline.py | head -10
```

Confirm the line numbers (865 inside dedupe, 1468 inside match). Only modify the dedupe one.

Today's call (around line 865) is:

```python
block_scorer = _get_block_scorer(config)
with stage("fuzzy_score_blocks"):
    pairs = block_scorer(
        blocks, mk, matched_pairs,
        across_files_only=across_files_only,
        source_lookup=source_lookup if across_files_only else None,
    )
```

- [ ] **Step 2: Add the kwargs.**

Replace the call with:

```python
block_scorer = _get_block_scorer(config)

# Component 3: when all three gating flags are on AND we have a live
# _prep_store, hand the backend the store_path + signature so the Ray
# key-mode dispatch path can fire. Backend ignores these kwargs in
# df-mode and the non-Ray scorers.
key_mode_kwargs: dict[str, str] = {}
if (
    config.backend == "ray"
    and config.prepared_record_store
    and config.partitioned_block_scoring
    and _prep_store is not None
):
    key_mode_kwargs["store_path"] = str(_prep_store.path)
    key_mode_kwargs["signature"] = _prep_cache_signature(config)

with stage("fuzzy_score_blocks"):
    pairs = block_scorer(
        blocks, mk, matched_pairs,
        across_files_only=across_files_only,
        source_lookup=source_lookup if across_files_only else None,
        **key_mode_kwargs,
    )
```

The `key_mode_kwargs` insertion is **unconditionally gated** by the `if config.backend == "ray"` check above — non-ray scorers never see the kwargs. This is the safe form: if `score_blocks_parallel` or `score_blocks_duckdb` ever tightens its signature to reject unknown kwargs, the gating prevents a regression. (The `**{}` form would also work today since both currently accept `**kwargs`, but defensive coding here is cheap.)

- [ ] **Step 3: Run pipeline tests + full prepared-store regression slice.**

```bash
python -m pytest tests/test_partitioned_block_scoring_pipeline.py tests/test_prepared_record_store.py tests/test_prepared_record_store_pipeline.py tests/test_prepared_record_store_controller.py tests/test_block_partitioned_store.py -v --timeout=120
```

Expected: all pass; 2 new pipeline tests + existing 30 passed (27 + 1 skip from prior phases).

### Task 5.3: Commit + open PR

- [ ] **Step 1: Commit.**

```bash
git add packages/python/goldenmatch/goldenmatch/core/pipeline.py packages/python/goldenmatch/tests/test_partitioned_block_scoring_pipeline.py
git commit -m "feat(distributed): Component 3 / Phase 5 -- pipeline.py wiring

When config.backend=='ray' AND config.prepared_record_store AND
config.partitioned_block_scoring AND _prep_store is alive, pipeline
passes store_path + signature kwargs to score_blocks_ray so the
Ray key-mode dispatch path can fire. Other scorers see no kwargs
(key_mode_kwargs stays empty).

Positive-case test asserts the kwargs reach the backend via a
monkeypatched fake; does NOT require ray installed.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 2: Push + PR.**

PR title: `feat(distributed): Component 3 / Phase 5 — pipeline.py wiring`.

---

## Phase 6 — 5M end-to-end bench (kill checkpoint)

The final phase. Compares today's `chunked` backend against the full Component 1+2+3 stack at 5M rows. Per the binding kill criterion: ≥ 20% wall AND ≥ 20% peak RSS or the whole stack reverts.

**Files:**
- Create: `packages/python/goldenmatch/scripts/bench_distributed_stack.py`
- Create: `.github/workflows/bench-distributed-stack.yml`

### Task 6.1: Bench script

- [ ] **Step 1: Create `packages/python/goldenmatch/scripts/bench_distributed_stack.py`:**

```python
"""5M end-to-end bench comparing today's chunked backend against the full
Component 1+2+3 stack (prepared_record_store + partitioned_block_scoring
+ backend=ray).

Kill criterion: stack must show >= 20% wall AND >= 20% peak RSS
improvement vs chunked or PRs #280-#283 + #287 + Component 3 PRs revert
per project_distributed_plan_v1_kill_criterion.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tracemalloc
from pathlib import Path
from time import perf_counter

import polars as pl


def build_df(n: int) -> pl.DataFrame:
    """Diverse-surname person-shape df. Per feedback_synthetic_surname_fixtures
    each surname spans its own soundex bucket so blocking doesn't degenerate
    into one O(N^2) block."""
    surnames = [
        "Smith", "Johnson", "Williams", "Brown", "Jones",
        "Garcia", "Miller", "Davis", "Rodriguez", "Martinez",
        "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson",
        "Thomas", "Taylor", "Moore", "Jackson", "Martin",
    ]
    first_names = [
        "Alice", "Bob", "Charlie", "Dana", "Eve", "Frank",
        "Grace", "Henry", "Iris", "Jack",
    ]
    rows = []
    for i in range(n):
        rows.append({
            "first_name": first_names[i % len(first_names)],
            "last_name":  surnames[i % len(surnames)],
            "email":      f"u{i // 3}@example.com",
            "zip":        f"{10000 + (i % 100):05d}",
        })
    return pl.DataFrame(rows)


def run_one(label: str, df: pl.DataFrame, *, backend: str, prepared_record_store: bool, partitioned_block_scoring: bool) -> dict:
    import goldenmatch as gm
    from goldenmatch.core.autoconfig import auto_configure_df
    from goldenmatch.core.bench import bench_capture

    tracemalloc.start()
    t0 = perf_counter()
    with bench_capture() as rec:
        cfg = auto_configure_df(df, confidence_required=False)
        cfg.backend = backend
        cfg.prepared_record_store = prepared_record_store
        cfg.partitioned_block_scoring = partitioned_block_scoring
        result = gm.dedupe_df(df, config=cfg, confidence_required=False)
    wall = perf_counter() - t0
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    return {
        "label": label,
        "backend": backend,
        "prepared_record_store": prepared_record_store,
        "partitioned_block_scoring": partitioned_block_scoring,
        "rows": df.height,
        "wall_seconds": round(wall, 3),
        "peak_rss_mb": round(peak / (1024 * 1024), 2),
        "clusters": len(result.clusters),
        "stage_timings_seconds": rec.to_dict()["stage_timings_seconds"],
        "metrics": rec.to_dict()["metrics"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=5_000_000)
    parser.add_argument("--out", type=Path, default=Path("bench_distributed_stack.json"))
    parser.add_argument("--store-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    if args.store_dir is not None:
        args.store_dir.mkdir(parents=True, exist_ok=True)
        os.environ["GOLDENMATCH_PREPARED_RECORD_STORE_DIR"] = str(args.store_dir)
        os.environ["GOLDENMATCH_PREPARED_RECORD_STORE_PERSIST"] = "1"

    print(f"Building synthetic df ({args.rows:,} rows)...", flush=True)
    df = build_df(args.rows)

    print("Run 1/2: baseline (backend=chunked)...", flush=True)
    baseline = run_one("baseline", df, backend="chunked", prepared_record_store=False, partitioned_block_scoring=False)
    print(f"  wall = {baseline['wall_seconds']}s; peak = {baseline['peak_rss_mb']} MB", flush=True)

    print("Run 2/2: treatment (full stack: ray + store + partitioned)...", flush=True)
    treatment = run_one("treatment", df, backend="ray", prepared_record_store=True, partitioned_block_scoring=True)
    print(f"  wall = {treatment['wall_seconds']}s; peak = {treatment['peak_rss_mb']} MB", flush=True)

    wall_delta = baseline["wall_seconds"] - treatment["wall_seconds"]
    rss_delta = baseline["peak_rss_mb"] - treatment["peak_rss_mb"]
    wall_pct = (-wall_delta / baseline["wall_seconds"]) * 100 if baseline["wall_seconds"] else 0.0
    rss_pct = (-rss_delta / baseline["peak_rss_mb"]) * 100 if baseline["peak_rss_mb"] else 0.0

    # Kill criterion check.
    KILL_THRESHOLD_PCT = -20.0  # negative because pct_change is the "treatment relative to baseline" signed delta
    kill_verdict = "PASS" if (wall_pct <= KILL_THRESHOLD_PCT and rss_pct <= KILL_THRESHOLD_PCT) else "FAIL"

    out = {
        "rows": args.rows,
        "baseline": baseline,
        "treatment": treatment,
        "diff": {
            "wall_saved_seconds": round(wall_delta, 3),
            "wall_pct_change": round(wall_pct, 2),
            "peak_rss_saved_mb": round(rss_delta, 2),
            "peak_rss_pct_change": round(rss_pct, 2),
        },
        "kill_criterion": {
            "threshold_pct": KILL_THRESHOLD_PCT,
            "verdict": kill_verdict,
            "note": "PASS = both wall and peak RSS improved by >= 20% (Component 1+2+3 stack stays). FAIL = revert PRs #280-#283 + #287 + Component 3 PRs.",
        },
    }
    args.out.write_text(json.dumps(out, indent=2))
    print(f"\nWrote {args.out}.", flush=True)
    print(json.dumps(out["diff"], indent=2), flush=True)
    print(f"\nKill criterion: {kill_verdict}", flush=True)
    # Non-zero exit on FAIL so the workflow run status surfaces the
    # verdict (visible in the GitHub Actions UI without opening the
    # artifact). PASS exits 0.
    return 0 if kill_verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
```

### Task 6.2: Workflow

- [ ] **Step 1: Create `.github/workflows/bench-distributed-stack.yml`:**

```yaml
name: bench-distributed-stack

on:
  workflow_dispatch:
    inputs:
      rows:
        description: "Synthetic df row count"
        required: false
        default: "5000000"
      ref:
        description: "Branch / tag / SHA to bench (default: workflow ref)"
        required: false

jobs:
  bench:
    name: "bench distributed stack (chunked vs Component 1+2+3)"
    runs-on: large-new-64GB
    timeout-minutes: 180

    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ inputs.ref || github.ref }}

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install goldenmatch with ray extra
        working-directory: packages/python/goldenmatch
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[ray]"

      - name: Run bench
        working-directory: packages/python/goldenmatch
        env:
          GOLDENMATCH_AUTOCONFIG_MEMORY: "0"
        run: |
          mkdir -p bench-out
          python scripts/bench_distributed_stack.py \
            --rows "${{ inputs.rows }}" \
            --store-dir bench-out/store \
            --out bench-out/results.json
          echo '## bench-distributed-stack results' >> "$GITHUB_STEP_SUMMARY"
          echo '```json' >> "$GITHUB_STEP_SUMMARY"
          cat bench-out/results.json >> "$GITHUB_STEP_SUMMARY"
          echo '```' >> "$GITHUB_STEP_SUMMARY"

      - name: Upload results
        uses: actions/upload-artifact@v4
        with:
          name: bench-distributed-stack-${{ inputs.rows }}-rows
          path: packages/python/goldenmatch/bench-out/results.json
          retention-days: 30
```

### Task 6.3: Smoke locally + commit + PR + trigger

- [ ] **Step 1: Smoke at 1K rows locally** (just to confirm the script doesn't crash):

```bash
cd packages/python/goldenmatch
pip install -e ".[ray]"  # if not already
python scripts/bench_distributed_stack.py --rows 1000 --out /tmp/smoke.json
cat /tmp/smoke.json | python -m json.tool | head -30
```

Expected: both runs complete; numbers will be noisy at 1K — that's fine, we only need the wiring to work.

- [ ] **Step 2: Commit + push + open PR.**

```bash
git add packages/python/goldenmatch/scripts/bench_distributed_stack.py .github/workflows/bench-distributed-stack.yml
git commit -m "bench(distributed): Component 3 / Phase 6 -- 5M kill checkpoint

Adds scripts/bench_distributed_stack.py + .github/workflows/
bench-distributed-stack.yml. Compares today's chunked backend
against the full Component 1+2+3 stack at 5M rows.

Per project_distributed_plan_v1_kill_criterion: if the treatment
doesn't beat the baseline by >= 20% wall AND >= 20% peak RSS,
PRs #280-#283 + #287 + Component 3 PRs all revert.

Script smoke-tested at 1K rows locally. The 5M run goes via
workflow_dispatch on large-new-64GB (180-min timeout).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 3: Push + PR + trigger.**

PR title: `bench(distributed): Component 3 / Phase 6 — 5M kill checkpoint`.

Once merged:

```bash
GH_TOKEN=$(gh auth token --user benzsevern) gh workflow run bench-distributed-stack.yml --ref main -f rows=5000000
```

Watch the run. When complete, download the artifact and inspect `kill_criterion.verdict`:
- `PASS` → memo the result, plan Component 4 (streaming pair store) for the next horizon.
- `FAIL` → execute the revert plan. PRs to revert in order: Component 3's #280-prereq + 4 Phase PRs (this phase included), then #287, then #283, then #282, #281, #280. Document the negative result in `packages/python/goldenmatch/CLAUDE.md` and update `project_distributed_plan_v1_component_1.md` memory.

---

## Acceptance checklist

- [ ] Phase 1 merged: `PreparedRecordStore(read_only=True)` works.
- [ ] Phase 2 merged: `_KeyModeBlock` + `_score_block_remote_by_key` + key-mode branch.
- [ ] Phase 3 merged: driver-OOM guard via incremental `ray.wait`.
- [ ] Phase 4 merged: real-Ray equivalence + block-not-found + cross-process tests.
- [ ] Phase 5 merged: pipeline.py wiring + positive-case kwarg test.
- [ ] Phase 6 merged + 5M bench run + verdict recorded.

---

## When to escalate

1. **Equivalence test (Phase 4.1) fails.** Key-mode produces different pairs than df-mode on the same input. Don't merge until resolved — debug with print-statements in `_score_block_remote_by_key` and compare `block_df` content between the two paths. Most likely cause: row ordering difference between `load_block` (DuckDB → Arrow → Polars) and the in-memory `BlockResult.df.collect()`. If ordering matters semantically, normalize via sort before scoring; if not, the test's `canon` set-comparison should already absorb it.

2. **Cross-process test fails on Windows** (Phase 4.2). Document the failure in the PR, mark the test `pytest.mark.xfail(reason=..., strict=True)`, and add a Windows-specific sharding follow-up to the spec's "Followups" section. Don't block Phase 4 merge on it — it's a regression anchor, not a release blocker.

3. **Phase 6 5M bench triggers driver OOM at gather time.** Means the driver-OOM guard (Phase 3) fired. Document the n_pairs value at failure in the PR body. Switch to smaller `--rows` for a "stack works at smaller scale" measurement, AND flag Component 4 (streaming pair store) as the immediate next priority.

4. **Phase 6 bench shows < 20% improvement.** Execute the documented revert. Don't argue with the kill criterion mid-flight — that's exactly the failure mode the criterion exists to prevent.
