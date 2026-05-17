# Distributed Plan v1 — Component 2 v2 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Component 2 v1's "one DuckDB table per block" storage with hash-bucketed Parquet files. Workers receive a bucket file containing many blocks; they recover per-block grouping in-worker via `partition_by`.

**Architecture:** Two new primitives in `goldenmatch/distributed/record_store.py` (`materialize_bucketed_blocks`, `load_bucket`, `iter_buckets`). Drop v1's per-block API entirely. `goldenmatch/backends/ray_backend.py` gets a new `_score_block_remote_by_bucket` task body replacing `_score_block_remote_by_key`. Pipeline wiring updated to compute `block_assignments` once and call the new materialize function.

**Tech Stack:** Python 3.12, Polars + PyArrow (existing hard deps), DuckDB (only for Component 1's prep cache; no DuckDB at all in v2 bucket storage), Ray (optional via `[ray]`), pytest.

**Spec:** [`docs/superpowers/specs/2026-05-17-distributed-plan-component-2-v2-bucketed-storage-design.md`](../specs/2026-05-17-distributed-plan-component-2-v2-bucketed-storage-design.md).

**Kill criterion (binding):** after v2 ships, 5M bench using `bench-dataset-v1` must show ≥ 20% wall AND ≥ 20% peak RSS vs `backend="chunked"`. If not, revert PRs #280-#297 + v2 PRs.

---

## File structure

**Modified files:**

| Path | Phase | Change |
|---|---|---|
| `packages/python/goldenmatch/goldenmatch/distributed/record_store.py` | 1 | Replace v1 block API. Add `BUCKET_HASH_SEED` constant, `materialize_bucketed_blocks`, `load_bucket`, `iter_buckets`. Extend `PreparedRecordStore.close()` to remove sibling `buckets_*` dirs. Remove `_BLOCK_PREFIX`, `_block_table_name`, `materialize_blocks`, `load_block`, `list_blocks`, `iter_blocks` (v1 API). Keep `_sanitize_signature`, `PreparedRecordStore`, `materialize_prepared_records`, `load_prepared_records` (Component 1 surface). |
| `packages/python/goldenmatch/goldenmatch/backends/ray_backend.py` | 2 | Replace in-function `_score_block_remote_by_key` with `_score_block_remote_by_bucket`. Update dispatch loop to iterate buckets via `iter_buckets(bucket_dir)`. |
| `packages/python/goldenmatch/goldenmatch/core/pipeline.py` | 2 | Replace v1 `materialize_blocks` call (PR #287's hook) with `materialize_bucketed_blocks`. Same gating; same `block_assignments` derivation; rename stage label to `partition_blocks_to_buckets`. |
| `packages/python/goldenmatch/goldenmatch/config/schemas.py` | 2 | Add `n_buckets: int \| None = None` field with `1 <= n <= 1024` validation. |

**Created files:**

| Path | Phase | Responsibility |
|---|---|---|
| `packages/python/goldenmatch/tests/test_bucketed_store.py` | 1 | 8 unit tests for the new primitives. Replaces deleted `test_block_partitioned_store.py`. |

**Deleted files:**

| Path | Phase | Reason |
|---|---|---|
| `packages/python/goldenmatch/tests/test_block_partitioned_store.py` | 1 | Tests for the dropped v1 API. |

---

## Pre-flight checklist

Before any phase:

- [ ] On clean branch off `main`: `git fetch origin main && git switch -c distributed-plan-c2-v2-phase-N origin/main`. Each phase branches off main, not prior phase, per the stacked-PR-auto-closure rule in root CLAUDE.md.
- [ ] Verify spec exists locally: `ls docs/superpowers/specs/2026-05-17-distributed-plan-component-2-v2-bucketed-storage-design.md`.
- [ ] Verify Ray is installed: `python -c "import ray; print(ray.__version__)"`. If missing: `uv pip install -e "packages/python/goldenmatch[ray]"`.
- [ ] Verify the existing Component 1+3 test slice is green:
  ```
  cd packages/python/goldenmatch
  python -m pytest tests/test_prepared_record_store.py tests/test_prepared_record_store_pipeline.py tests/test_prepared_record_store_controller.py tests/test_distributed_scoring.py -v --timeout=120
  ```
  Expected: 35+ passed before phase 1 starts.

---

## Phase 1 — New primitives + tests + v1 removal

The structural change. No pipeline wiring yet (Phase 2). One PR.

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/distributed/record_store.py`
- Test: `packages/python/goldenmatch/tests/test_bucketed_store.py` (NEW)
- Delete: `packages/python/goldenmatch/tests/test_block_partitioned_store.py`

### Task 1.1: Write failing tests + delete v1 tests

**Phase 1 must land green on its own — Phase 2's PR can't inherit a broken main.** That means Phase 1 also touches `pipeline.py` and `ray_backend.py` to remove the v1 import sites (replacing with explicit `NotImplementedError` raises that the gating flags' default-off makes unreachable in practice). Phase 2 then wires the v2 path. This adds one more file modification to Phase 1 but keeps the phase boundary clean.

- [ ] **Step 0: Inventory v1 import sites.**

```bash
grep -n "materialize_blocks\|load_block\|list_blocks\|iter_blocks\|_block_table_name\|_BLOCK_PREFIX" packages/python/goldenmatch/goldenmatch/core/pipeline.py packages/python/goldenmatch/goldenmatch/backends/ray_backend.py
```

Expected sites (verify; if grep finds others, add them to Step 2(f)):
- `pipeline.py` ~line 865-880 (the v1 hook from PR #287, inside `_run_dedupe_pipeline`)
- `ray_backend.py` ~line 95-130 (the in-function `_score_block_remote_by_key` task + its dispatch loop from PR #290)
- **No match-path site** (per spec/PR #294 scope, `_run_match_pipeline` was intentionally not wired)

- [ ] **Step 1: Delete `tests/test_block_partitioned_store.py`.**

```bash
git rm packages/python/goldenmatch/tests/test_block_partitioned_store.py
```

- [ ] **Step 2: Create `tests/test_bucketed_store.py`** with all 8 unit tests verbatim:

```python
"""Unit tests for Component 2 v2 bucketed Parquet storage.

Spec: docs/superpowers/specs/2026-05-17-distributed-plan-component-2-v2-bucketed-storage-design.md
Replaces test_block_partitioned_store.py (deleted -- v1 API dropped).
"""
from __future__ import annotations

import shutil
from collections import Counter
from pathlib import Path

import polars as pl
import pytest

from goldenmatch.config.schemas import GoldenMatchConfig
from goldenmatch.distributed.record_store import (
    PreparedRecordStore,
    _sanitize_signature,
    iter_buckets,
    load_bucket,
    materialize_bucketed_blocks,
)


def _df(n_rows: int) -> pl.DataFrame:
    return pl.DataFrame({
        "__row_id__": list(range(n_rows)),
        "name": [f"name_{i}" for i in range(n_rows)],
    })


def _assignments(n_rows: int, n_keys: int) -> dict[int, str]:
    """Round-robin assign rows to `n_keys` distinct block_keys."""
    return {i: f"k{i % n_keys}" for i in range(n_rows)}


def test_materialize_writes_at_most_n_files(tmp_path: Path):
    """Spec §Testing: small df + N=4 -> ≤ 4 files. Empty buckets skipped
    per §Error handling #4."""
    with PreparedRecordStore(base_dir=tmp_path) as store:
        df = _df(20)
        bucket_dir = materialize_bucketed_blocks(
            store, df,
            block_assignments=_assignments(20, n_keys=4),
            n_buckets=4,
            signature="sig-v1",
        )
        n_files = len(list(bucket_dir.glob("bucket=*/data.parquet")))
        assert 1 <= n_files <= 4


def test_load_bucket_roundtrip(tmp_path: Path):
    """Spec §Components #2: load_bucket reads a Parquet back as a Polars df."""
    p = tmp_path / "bucket=0" / "data.parquet"
    p.parent.mkdir(parents=True)
    df = pl.DataFrame({"__row_id__": [0, 1], "__block_key__": ["k0", "k0"]})
    df.write_parquet(p)
    loaded = load_bucket(p)
    assert loaded.shape == df.shape
    assert set(loaded.columns) == set(df.columns)


def test_iter_buckets_yields_sorted(tmp_path: Path):
    """Spec §Components #3: iter_buckets sorts by bucket_id."""
    for k in [3, 0, 2, 1]:
        d = tmp_path / f"bucket={k}"
        d.mkdir()
        pl.DataFrame({"__row_id__": [k]}).write_parquet(d / "data.parquet")
    ids = [bid for bid, _ in iter_buckets(tmp_path)]
    assert ids == [0, 1, 2, 3]


def test_iter_buckets_missing_directory_yields_empty(tmp_path: Path):
    """Spec §Components #3 missing-dir semantics: non-existent dir
    yields zero items, doesn't raise."""
    missing = tmp_path / "does_not_exist"
    assert list(iter_buckets(missing)) == []


def test_hash_is_deterministic_across_calls(tmp_path: Path):
    """Spec §Decisions: BUCKET_HASH_SEED pinned -> same block_key
    lands in same bucket on repeated runs.

    Also sanity-checks Polars hash API (`pl.col(x).hash(seed=u64)`) so
    a future Polars API change (e.g. seed_1..seed_4 split) fails this
    test loudly instead of silently corrupting bucket assignments at
    bench time.
    """
    # API sanity check first -- catches a Polars API regression
    # before we depend on the seed kwarg below.
    sanity = pl.DataFrame({"x": ["a", "b", "c"]})
    hashed = sanity.select(pl.col("x").hash(seed=0))
    assert hashed.dtypes[0] == pl.UInt64, (
        f"Polars hash dtype changed from UInt64 to {hashed.dtypes[0]} -- "
        f"bucket assignment will silently corrupt. Pin polars version "
        f"or update materialize_bucketed_blocks's hash expression."
    )
    df = _df(60)
    assignments = _assignments(60, n_keys=10)

    def bucket_for_key(store_path, key):
        # Find which bucket file contains rows with this block_key.
        for bid, path in iter_buckets(store_path.parent / f"buckets_{_sanitize_signature('sig-v1')}"):
            bucket_df = load_bucket(path)
            if key in bucket_df["__block_key__"].to_list():
                return bid
        raise AssertionError(f"key {key!r} not found in any bucket")

    with PreparedRecordStore(base_dir=tmp_path / "run1") as s1:
        materialize_bucketed_blocks(
            s1, df, block_assignments=assignments,
            n_buckets=4, signature="sig-v1",
        )
        b_first = {f"k{i}": bucket_for_key(s1.path, f"k{i}") for i in range(10)}
        bucket_dir_1 = s1.path.parent / f"buckets_{_sanitize_signature('sig-v1')}"
        # Copy out before close so we can compare.
        snapshot = sorted(bucket_dir_1.glob("**/data.parquet"))

    with PreparedRecordStore(base_dir=tmp_path / "run2") as s2:
        materialize_bucketed_blocks(
            s2, df, block_assignments=assignments,
            n_buckets=4, signature="sig-v1",
        )
        b_second = {f"k{i}": bucket_for_key(s2.path, f"k{i}") for i in range(10)}

    assert b_first == b_second


def test_n_buckets_bounds_validated():
    """Spec §Configuration: n_buckets in [1, 1024]; out-of-range raises
    at config construction."""
    GoldenMatchConfig(n_buckets=1)
    GoldenMatchConfig(n_buckets=1024)
    GoldenMatchConfig(n_buckets=None)  # heuristic default
    with pytest.raises(Exception):  # Pydantic ValidationError
        GoldenMatchConfig(n_buckets=0)
    with pytest.raises(Exception):
        GoldenMatchConfig(n_buckets=2000)


def test_empty_block_assignments_writes_zero_files(tmp_path: Path):
    """Edge case: empty assignments -> no-op materialize, no buckets,
    iter_buckets yields empty."""
    with PreparedRecordStore(base_dir=tmp_path) as store:
        bucket_dir = materialize_bucketed_blocks(
            store, _df(0),
            block_assignments={},
            n_buckets=4,
            signature="sig-empty",
        )
    assert list(iter_buckets(bucket_dir)) == []


def test_hash_distribution_skew_bounded(tmp_path: Path):
    """Spec §Testing: 10K block_keys hashed into N=32; max/min bucket
    size ratio ≤ 3. Guards against accidental seed change producing
    pathological skew."""
    n_keys = 10_000
    n_rows = n_keys  # one row per key for this test
    df = pl.DataFrame({
        "__row_id__": list(range(n_rows)),
        "name": [f"n_{i}" for i in range(n_rows)],
    })
    assignments = {i: f"key_{i}" for i in range(n_keys)}

    with PreparedRecordStore(base_dir=tmp_path) as store:
        bucket_dir = materialize_bucketed_blocks(
            store, df,
            block_assignments=assignments,
            n_buckets=32,
            signature="sig-skew",
        )
        sizes = []
        for _, path in iter_buckets(bucket_dir):
            sizes.append(load_bucket(path).height)
    assert min(sizes) > 0
    assert max(sizes) / min(sizes) <= 3.0
```

- [ ] **Step 3: Add `n_buckets` to `GoldenMatchConfig`.**

In `packages/python/goldenmatch/goldenmatch/config/schemas.py`, add next to `prepared_record_store` and `partitioned_block_scoring`:

```python
    n_buckets: int | None = Field(
        default=None,
        ge=1,
        le=1024,
        description=(
            "Number of hash buckets for Component 2 v2 bucketed Parquet "
            "storage. None means use the heuristic default "
            "max(cpu_count() * 4, 64). Hard-capped at 1024. Spec: "
            "docs/superpowers/specs/2026-05-17-distributed-plan-component-2-v2"
            "-bucketed-storage-design.md §Configuration."
        ),
    )
```

- [ ] **Step 4: Run; expect failures** (`ImportError` on `materialize_bucketed_blocks`, etc.).

```bash
cd packages/python/goldenmatch
python -m pytest tests/test_bucketed_store.py -v
```

Expected: 8 errors, all citing missing symbols in `goldenmatch.distributed.record_store`.

### Task 1.2: Implement the new primitives

- [ ] **Step 1: Read existing record_store.py** to confirm exact location of imports, helpers.

```bash
sed -n '1,50p' packages/python/goldenmatch/goldenmatch/distributed/record_store.py
```

- [ ] **Step 2: Replace the v1 block API with the v2 bucketed API.**

In `packages/python/goldenmatch/goldenmatch/distributed/record_store.py`:

(a) **Imports:** add at the top of the existing import block:

```python
import shutil
from collections.abc import Iterator
```

(b) **Remove v1 block API:** delete `_BLOCK_PREFIX`, `_block_table_name`, `materialize_blocks`, `load_block`, `list_blocks`, `iter_blocks`. Keep everything else.

(c) **Add module-level constant** below `_TABLE_PREFIX`:

```python
BUCKET_HASH_SEED = 0xC2B5C0BBE7ED5E5D
"""Deterministic seed for Polars' xxHash-based bucket assignment.
Changing this value reshuffles every bucket assignment; treat as a
constant. See spec §Decisions log."""
```

(d) **Add the three new helpers** at the bottom of the file:

```python
def materialize_bucketed_blocks(
    store: PreparedRecordStore,
    df: pl.DataFrame,
    *,
    block_assignments: dict[int, str] | pl.DataFrame,
    n_buckets: int,
    signature: str,
) -> Path:
    """Write `df` partitioned into N hash buckets at
    `store.path.parent / buckets_<sig_hash>/bucket=K/data.parquet`.

    `block_assignments` accepts EITHER:
    * `dict[int, str]` mapping `__row_id__` -> `block_key` (convenient
      for tests; converted to a 2-col df internally).
    * `pl.DataFrame` with `__row_id__` (int) and `__block_key__` (str)
      columns. Production callers pass this form -- building a
      5M-entry Python dict is precisely the per-row Python loop v2
      exists to avoid.

    Empty assignments yield a bucket_dir with no Parquet files
    (Polars' partition_by skips empty groups).

    Spec: docs/superpowers/specs/2026-05-17-...-v2-bucketed-storage-design.md
    §Components #1.
    """
    sig_hash = _sanitize_signature(signature)
    bucket_dir = store.path.parent / f"buckets_{sig_hash}"
    bucket_dir.mkdir(parents=True, exist_ok=True)

    # Normalize to a Polars DataFrame.
    if isinstance(block_assignments, dict):
        if not block_assignments:
            return bucket_dir
        rid_to_block = pl.DataFrame({
            "__row_id__": list(block_assignments.keys()),
            "__block_key__": list(block_assignments.values()),
        })
    else:
        rid_to_block = block_assignments
        if rid_to_block.height == 0:
            return bucket_dir
        required = {"__row_id__", "__block_key__"}
        if not required.issubset(set(rid_to_block.columns)):
            raise ValueError(
                f"block_assignments DataFrame must have columns "
                f"{required}; got {set(rid_to_block.columns)}"
            )

    # Inner join attaches __block_key__. Rows in `df` without an
    # assignment drop out (matches v1: unassigned rows weren't scored).
    keyed = df.join(rid_to_block, on="__row_id__", how="inner")

    # Bucket assignment via Polars xxHash with fixed seed.
    with_bucket = keyed.with_columns(
        (pl.col("__block_key__").hash(seed=BUCKET_HASH_SEED) % n_buckets)
        .alias("__bucket__"),
    )

    for bucket_id, bucket_df in with_bucket.partition_by(
        "__bucket__", as_dict=True,
    ).items():
        # bucket_id may arrive as a tuple (Polars >= 1.0 with as_dict=True
        # uses tuple keys for partition cols). Unwrap.
        if isinstance(bucket_id, tuple):
            bucket_id = bucket_id[0]
        bucket_path = bucket_dir / f"bucket={int(bucket_id)}" / "data.parquet"
        bucket_path.parent.mkdir(parents=True, exist_ok=True)
        bucket_df.drop("__bucket__").write_parquet(
            bucket_path, compression="snappy",
        )

    return bucket_dir


def load_bucket(bucket_path: Path) -> pl.DataFrame:
    """Read a bucket Parquet file as a Polars DataFrame.

    Trivial wrapper, lifted to a function so future enhancements
    (streaming, column projection) have one site to change.
    """
    return pl.read_parquet(bucket_path)


def iter_buckets(bucket_dir: Path) -> Iterator[tuple[int, Path]]:
    """Yield (bucket_id, parquet_path) pairs for each bucket=K/data.parquet
    under `bucket_dir`. Sorted by bucket_id for determinism.

    Missing directory yields zero items (does NOT raise) -- spec
    §Components #3 missing-dir semantics. Workers receive these paths;
    the driver never reads bucket contents.
    """
    if not bucket_dir.exists():
        return
    pairs: list[tuple[int, Path]] = []
    for sub in bucket_dir.iterdir():
        if not sub.is_dir() or not sub.name.startswith("bucket="):
            continue
        try:
            bid = int(sub.name.split("=", 1)[1])
        except (IndexError, ValueError):
            continue
        path = sub / "data.parquet"
        if path.is_file():
            pairs.append((bid, path))
    pairs.sort(key=lambda p: p[0])
    for bid, path in pairs:
        yield bid, path
```

(e) **Extend `PreparedRecordStore.close()`** to clean up sibling bucket dirs.

The existing `close()` has its own structure (`_owns_file` flag, the existing unlink call). **DO NOT replace the function body.** Use an Edit-style insertion: find the last line of the existing `close()` body and add a new block before the method ends. Read the existing implementation first via `sed -n '115,140p' packages/python/goldenmatch/goldenmatch/distributed/record_store.py` to see the real shape.

The new block to insert (after the existing unlink, before the method's natural end):

```python
        # v2 bucket directory cleanup (NEW per spec §Error handling #7).
        # shutil.rmtree does NOT expand globs -- iterate explicitly via
        # Path.glob. ignore_errors=True absorbs benign Windows file-locking
        # races (mirrors v1's unlink(missing_ok=True) style).
        if self._cleanup and self._owns_file:
            for sibling in self.path.parent.glob("buckets_*"):
                if sibling.is_dir():
                    shutil.rmtree(sibling, ignore_errors=True)
```

Verify after edit that the existing v1-DuckDB-file unlink semantics are unchanged — the insertion should only ADD lines, not modify any line that was already there.

- [ ] **Step 3: Run tests.**

```bash
python -m pytest tests/test_bucketed_store.py -v --timeout=60
```

Expected: 8 passed.

- [ ] **Step 4 (NEW): Stub out v1 import sites in pipeline.py + ray_backend.py.**

The v1 import sites cannot stay — they reference symbols Step 2(b) just deleted. But this is Phase 1, so we don't wire the v2 path yet either. Replace each site with a clear `raise NotImplementedError("Component 2 v2 Phase 2 wires this")` guarded by the same gating flags, so:
  * Default-off code paths (`partitioned_block_scoring=False`) still work — the existing `if` predicate keeps the body unreachable.
  * Anyone flipping the flag on between Phase 1 and Phase 2 lands gets a loud error instead of an import-error stack trace.

**In `pipeline.py` ~line 865** (locate via Step 0's grep), replace the entire `if config.prepared_record_store and config.partitioned_block_scoring and _prep_store is not None:` body with:

```python
            if (
                config.prepared_record_store
                and config.partitioned_block_scoring
                and _prep_store is not None
            ):
                raise NotImplementedError(
                    "Component 2 v2 Phase 1: v1 materialize_blocks API "
                    "removed; v2 materialize_bucketed_blocks wiring "
                    "lands in Phase 2. Disable partitioned_block_scoring "
                    "for now."
                )
```

**In `ray_backend.py`** (locate `_score_block_remote_by_key` via Step 0's grep):

1. **Delete** the entire in-function `_score_block_remote_by_key` definition (the `@ray.remote(max_retries=0)` task body added by PR #290).
2. **Delete** the `if use_key_mode:` branch that submitted futures via `_score_block_remote_by_key.remote(...)`.
3. **Add** a `NotImplementedError` raise at the top of where that branch was, so flipping the flags between Phase 1 and Phase 2 fails loudly instead of mysteriously:

```python
    use_key_mode = store_path is not None and signature is not None
    if use_key_mode:
        raise NotImplementedError(
            "Component 2 v2 Phase 1: key-mode dispatch removed; "
            "bucket-mode dispatch lands in Phase 2. Pass "
            "store_path=None/signature=None for df-mode."
        )
```

4. **PRESERVE** the existing df-mode `for block in blocks: ...` loop body verbatim — that's the path active when `use_key_mode` is False (the default). Don't paste `...` over it; keep every line that built `collected_block` and called `_score_block_remote.remote(...)` exactly as it lives on `main` today.

The net change is: stub `_score_block_remote_by_key` out, leave `_score_block_remote` and the df-mode dispatch loop untouched.

- [ ] **Step 5: Regression check** — full suite must stay green.

```bash
python -m pytest tests/test_prepared_record_store.py tests/test_prepared_record_store_pipeline.py tests/test_prepared_record_store_controller.py tests/test_distributed_scoring.py tests/test_partitioned_block_scoring_pipeline.py -v --timeout=120
```

Expected: all green. The default-off code paths don't touch the new `NotImplementedError` raises. Any test that explicitly turns the flags on must either be updated to expect the `NotImplementedError` in Phase 1 (then re-updated in Phase 2 to expect success) OR deferred to Phase 2 — flag these to the spec-reviewer subagent so the reviewer can confirm Phase 1's commit message lists them.

Specifically:
- `test_distributed_scoring.py::test_key_mode_*` and bucket-mode tests **should be REMOVED in Phase 1** (Step 6 below) because they reference v1's `_score_block_remote_by_key` and the new tests land in Phase 2.
- `test_partitioned_block_scoring_pipeline.py`'s "all flags on" positive-case test needs to either expect `NotImplementedError` (clunky) or be marked `pytest.mark.skip(reason="Component 2 v2 Phase 2")`. Choose the skip — Phase 2 unskips it.

- [ ] **Step 6: Strip Phase-2 tests from Phase 1's branch.**

First, **verify what's actually in the test file post-PR #294**:

```bash
grep -n "^def test_\|_score_block_remote_by_key\|materialize_blocks\b\|load_block\b" packages/python/goldenmatch/tests/test_distributed_scoring.py
```

For each test function, decide:
- Touches `_score_block_remote_by_key`, `materialize_blocks` (v1), or `load_block` (v1) → **delete in Phase 1**, re-add in Phase 2 as bucket-mode.
- Touches only `_KeyModeBlock` shim, `_PAIR_BYTES_ESTIMATE` constant, or `score_blocks_ray` kwarg signature → **keep**.
- Touches real-Ray fixtures (`_ray_local`, `_build_small_blocks`) → keep the fixtures; mark tests that use them `pytest.mark.skip(reason="Component 2 v2 Phase 2")` if they exercise the v1 dispatch path.

When in doubt, run the suite after edits — anything that fails on import or first-line execution is a deletion candidate.

### Task 1.3: Lint + commit + PR

- [ ] **Step 1: Lint.**

```bash
uv run ruff check goldenmatch/distributed/record_store.py goldenmatch/config/schemas.py tests/test_bucketed_store.py
```

Fix anything reported (use `--fix` if it offers).

- [ ] **Step 2: Commit.**

```bash
git add packages/python/goldenmatch/goldenmatch/distributed/record_store.py packages/python/goldenmatch/goldenmatch/config/schemas.py packages/python/goldenmatch/tests/test_bucketed_store.py packages/python/goldenmatch/tests/test_block_partitioned_store.py
git commit -m "feat(distributed): Component 2 v2 / Phase 1 -- bucketed Parquet storage primitives

Replaces v1's per-block-DuckDB-table API with hash-bucketed Parquet
files. Standard industry pattern (Spark bucketBy, Iceberg hidden
partitioning, Splink distribution model). Local probe established v1
fails at the auto-config workload: 100K tables took 62 min wall +
21 GB DuckDB catalog metadata; 1.67M tables (real 5M auto-config
output) would have hung.

- BUCKET_HASH_SEED module-level constant (pinned u64).
- materialize_bucketed_blocks(): partition df by hash(block_key) %
  n_buckets, write N Parquet files at buckets_<sig>/bucket=K/data.parquet.
- load_bucket(): pl.read_parquet wrapper, lifted for future streaming.
- iter_buckets(): yields (bucket_id, path) sorted; missing dir yields
  empty (spec §Components #3 missing-dir semantics).
- PreparedRecordStore.close() extended to rmtree sibling buckets_*
  dirs under cleanup=True (spec §Error handling #7).
- GoldenMatchConfig.n_buckets: Optional[int] in [1, 1024].
- v1 block API (_BLOCK_PREFIX, _block_table_name, materialize_blocks,
  load_block, list_blocks, iter_blocks) deleted.
- tests/test_block_partitioned_store.py (v1 tests) deleted; replaced by
  tests/test_bucketed_store.py (8 new unit tests).

Phase 2 wires this into pipeline.py + ray_backend.py.

Spec: docs/superpowers/specs/2026-05-17-distributed-plan-component-2-v2-bucketed-storage-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 3: Push + open PR** with auth dance per `feedback_github_auth_switch`.

```bash
gh auth switch --user benzsevern
GH_TOKEN=$(gh auth token --user benzsevern) git push -u origin distributed-plan-c2-v2-phase-1
GH_TOKEN=$(gh auth token --user benzsevern) gh pr create --base main --title "feat(distributed): Component 2 v2 / Phase 1 -- bucketed Parquet storage primitives" --body "<see plan>"
gh auth switch --user benzsevern-mjh
```

---

## Phase 2 — Pipeline + Ray backend rewiring

The wiring change. v1's pipeline.py hook + ray_backend.py task body get replaced. One PR.

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/pipeline.py`
- Modify: `packages/python/goldenmatch/goldenmatch/backends/ray_backend.py`
- Test: `packages/python/goldenmatch/tests/test_distributed_scoring.py` (extend; rewrite Phase-2/4 v1 tests)
- Test: `packages/python/goldenmatch/tests/test_partitioned_block_scoring_pipeline.py` (extend with one new bucketed-materialize test)

### Task 2.1: Update pipeline.py wiring

- [ ] **Step 1: Locate the v1 hook** at the partition_blocks_to_store stage.

```bash
grep -n "partition_blocks_to_store\|materialize_blocks" packages/python/goldenmatch/goldenmatch/core/pipeline.py
```

- [ ] **Step 2: Replace with the v2 hook.**

Replace the entire v1 block (the one inside `if config.prepared_record_store and config.partitioned_block_scoring and _prep_store is not None:`) with:

```python
            if (
                config.prepared_record_store
                and config.partitioned_block_scoring
                and _prep_store is not None
            ):
                from goldenmatch.distributed.record_store import (
                    materialize_bucketed_blocks,
                )
                # Component 2 v2: build the (row_id, block_key)
                # assignment table fully vectorized in Polars. v1's
                # dict-comprehension at 5M / 1.67M blocks was the
                # bottleneck v2 exists to avoid; this path stays in
                # Arrow/Rust the whole way.
                #
                # Multi-pass blocking semantics: a row that appears in
                # blocks A then B then C ends up with block_key = "C".
                # The .unique(subset=["__row_id__"], keep="last")
                # enforces last-write-wins by deduplicating ON the
                # row_id with the trailing block_key. After unique(),
                # the row -> block_key map is single-valued, so the
                # downstream inner join in materialize_bucketed_blocks
                # has no ambiguity.
                assignment_parts = []
                for blk in blocks:
                    lf = blk.df if isinstance(blk.df, pl.LazyFrame) else blk.df.lazy()
                    assignment_parts.append(
                        lf.select("__row_id__").with_columns(
                            pl.lit(blk.block_key).alias("__block_key__"),
                        )
                    )
                assignments_df = (
                    pl.concat(assignment_parts)
                    .unique(subset=["__row_id__"], keep="last")
                    .collect()
                )

                n_buckets = config.n_buckets or max((os.cpu_count() or 1) * 4, 64)
                n_buckets = min(n_buckets, 1024)
                with stage("partition_blocks_to_buckets"):
                    materialize_bucketed_blocks(
                        _prep_store,
                        combined_lf.collect(),
                        block_assignments=assignments_df,
                        n_buckets=n_buckets,
                        signature=_prep_cache_signature(config),
                    )
```

Verify the surrounding context still imports `os`, `pl`, `stage`. Add if missing.

### Task 2.2: Update ray_backend.py task body

- [ ] **Step 1: Locate `_score_block_remote_by_key` inside `score_blocks_ray`.**

```bash
grep -n "_score_block_remote_by_key\|_KeyModeBlock\|use_key_mode" packages/python/goldenmatch/goldenmatch/backends/ray_backend.py
```

- [ ] **Step 2: Replace the in-function task body and dispatch loop.**

The new `_score_block_remote_by_bucket` task (same @ray.remote(max_retries=0) decoration, same in-function placement):

```python
    @ray.remote(max_retries=0)
    def _score_block_remote_by_bucket(
        bucket_path: str,
        mk_config,
        exclude,
        src_lookup,
        across_only: bool,
    ):
        """Component 2 v2 / Component 3: worker loads one bucket
        Parquet, recovers per-block grouping via partition_by, scores
        each block, returns concatenated pairs."""
        from pathlib import Path as _Path

        from goldenmatch.core.scorer import _score_one_block
        from goldenmatch.distributed.record_store import load_bucket

        bucket_df = load_bucket(_Path(bucket_path))
        all_pairs: list[tuple[int, int, float]] = []
        for block_key, block_df in bucket_df.partition_by(
            "__block_key__", as_dict=True,
        ).items():
            # block_key arrives as tuple under Polars >= 1.0 partition_by.
            if isinstance(block_key, tuple):
                block_key = block_key[0]
            shim = _KeyModeBlock(block_key=str(block_key), df=block_df.lazy())
            all_pairs.extend(
                _score_one_block(
                    shim, mk_config, exclude,
                    across_files_only=across_only, source_lookup=src_lookup,
                )
            )
        return all_pairs
```

The dispatch loop (where v1 looped `for block in blocks:`):

```python
    use_bucket_mode = store_path is not None and signature is not None

    futures = []
    if use_bucket_mode:
        from pathlib import Path as _Path

        from goldenmatch.distributed.record_store import (
            _sanitize_signature,
            iter_buckets,
        )
        sig_hash = _sanitize_signature(signature)
        bucket_dir = _Path(store_path).parent / f"buckets_{sig_hash}"
        for _bucket_id, bucket_path in iter_buckets(bucket_dir):
            future = _score_block_remote_by_bucket.remote(
                str(bucket_path),
                mk_ref, exclude_ref, source_ref,
                across_files_only,
            )
            futures.append(future)
    else:
        # df-mode unchanged from Component 3 v1.
        for block in blocks:
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
        "Submitted %d %s to Ray (%s mode, %d CPUs available)",
        len(futures),
        "buckets" if use_bucket_mode else "blocks",
        "bucket" if use_bucket_mode else "df",
        int(ray.cluster_resources().get("CPU", 0)),
    )
```

Delete the old `_score_block_remote_by_key` task body entirely.

The incremental `ray.wait` + driver-OOM guard from Component 3 Phase 3 (PR #291) needs no change — it operates on `futures` regardless of which mode populated them.

### Task 2.3: Rewrite affected integration tests

- [ ] **Step 1: Locate v1-symbol references in test_distributed_scoring.py.**

```bash
grep -n "_score_block_remote_by_key\|test_score_blocks_ray\|_build_small_blocks\|materialize_blocks\b" packages/python/goldenmatch/tests/test_distributed_scoring.py
```

- [ ] **Step 2: Replace `_build_small_blocks` helper** to write buckets instead of per-block tables. New body:

```python
def _build_small_blocks(tmp_path):
    """Materialize a small df to bucketed Parquet (Component 2 v2).
    Returns (store_path, signature, blocks_list) for backward compat
    with existing tests. blocks_list is the in-memory BlockResult list
    df-mode uses; bucket-mode reads buckets via the store_path."""
    from goldenmatch.core.blocker import BlockResult
    from goldenmatch.distributed.record_store import (
        PreparedRecordStore,
        materialize_bucketed_blocks,
        materialize_prepared_records,
    )

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
        materialize_bucketed_blocks(
            store, df,
            block_assignments=block_assignments,
            n_buckets=8,  # < 5 blocks would force the small-block
                          # fallback in score_blocks_ray; we want
                          # bucket-mode to actually engage.
            signature="sig-v1",
        )

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
```

- [ ] **Step 3: Update tests that reference `_score_block_remote_by_key`** (if any) to reference `_score_block_remote_by_bucket`. The error-message test (PR #293's `test_key_mode_block_not_found_raises_runtime_error`) becomes obsolete — bucket-mode doesn't raise on missing block_key because per-block grouping happens in-worker. **Delete that test.** The dispatch-shape test (PR #290's `test_score_blocks_ray_with_store_path_routes_to_key_mode`) should already pass without modification since it asserts kwargs reach the backend, not the in-function task body.

- [ ] **Step 4: Add Phase 5.2 (Spec §Testing) integration tests:**

```python
def test_bucket_mode_equivalence_with_df_mode(_ray_local, tmp_path):
    """Same input -> same canonical pair set whether df-mode or bucket-mode.
    Set comparison, not list -- ordering is non-deterministic across
    buckets (spec §Testing)."""
    from goldenmatch.backends import ray_backend
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField

    store_path, sig, blocks = _build_small_blocks(tmp_path)
    mk = MatchkeyConfig(
        name="name_exact", type="weighted", threshold=0.5,
        fields=[MatchkeyField(field="__mk_name__", scorer="exact", weight=1.0)],
    )

    pairs_df = ray_backend.score_blocks_ray(blocks, mk, set())
    pairs_bucket = ray_backend.score_blocks_ray(
        blocks, mk, set(),
        store_path=store_path, signature=sig,
    )

    def canon(p):
        a, b, s = p
        return (min(a, b), max(a, b), round(s, 6))
    assert {canon(p) for p in pairs_bucket} == {canon(p) for p in pairs_df}


def test_bucket_mode_dispatches_n_tasks(_ray_local, tmp_path, caplog):
    """Driver submits one Ray task per non-empty bucket, not per block.

    Captured via the `logger.info("Submitted %d ... Ray ...")` line in
    score_blocks_ray. The actual count of futures is internal to the
    function (in-function @ray.remote task can't be monkey-patched from
    outside the function scope), so we assert via the structured log
    line that's emitted right after futures are built.
    """
    import logging

    from goldenmatch.backends import ray_backend
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField

    store_path, sig, blocks = _build_small_blocks(tmp_path)
    mk = MatchkeyConfig(
        name="name_exact", type="weighted", threshold=0.5,
        fields=[MatchkeyField(field="__mk_name__", scorer="exact", weight=1.0)],
    )

    with caplog.at_level(logging.INFO, logger="goldenmatch.backends.ray_backend"):
        ray_backend.score_blocks_ray(
            blocks, mk, set(),
            store_path=store_path, signature=sig,
        )

    # Find the "Submitted N buckets ... bucket mode" line.
    submitted_lines = [
        r for r in caplog.records
        if "Submitted" in r.getMessage() and "bucket mode" in r.getMessage()
    ]
    assert len(submitted_lines) == 1, (
        f"expected exactly one 'Submitted ... bucket mode' log line; "
        f"got {len(submitted_lines)}. Records: "
        f"{[r.getMessage() for r in caplog.records]}"
    )
    # The fixture has 5 distinct block_keys (A..E) and n_buckets=8;
    # actual bucket count is <= 5 due to empty-bucket skipping. Just
    # assert it's <= len(blocks) and > 0 -- the load-bearing invariant
    # is "fewer tasks than blocks at scale," not "exactly some number
    # at toy scale."
    submitted_msg = submitted_lines[0].getMessage()
    import re
    n_submitted = int(re.search(r"Submitted (\d+)", submitted_msg).group(1))
    assert 0 < n_submitted <= len(blocks), (
        f"submitted {n_submitted} tasks; expected 0 < n <= len(blocks)={len(blocks)}"
    )


def test_oom_guard_fires_at_bucket_granularity(_ray_local, tmp_path, monkeypatch):
    """Spec §Error handling #6: OOM guard still works when N futures
    are buckets instead of blocks."""
    import psutil
    from goldenmatch.backends import ray_backend
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField

    store_path, sig, blocks = _build_small_blocks(tmp_path)
    mk = MatchkeyConfig(
        name="name_exact", type="weighted", threshold=0.5,
        fields=[MatchkeyField(field="__mk_name__", scorer="exact", weight=1.0)],
    )

    class FakeMem:
        available = 80
    monkeypatch.setattr(psutil, "virtual_memory", lambda: FakeMem)

    with pytest.raises(MemoryError, match="scored pairs"):
        ray_backend.score_blocks_ray(
            blocks, mk, set(),
            store_path=store_path, signature=sig,
        )
```

### Task 2.4: Add pipeline test

- [ ] **Step 1: Append to `tests/test_partitioned_block_scoring_pipeline.py`:**

```python
def test_pipeline_uses_bucketed_materialize_on_flag_on(tmp_path, monkeypatch):
    """Spec §Testing pipeline integration: with all flags on, pipeline
    calls materialize_bucketed_blocks (not v1 materialize_blocks)."""
    import goldenmatch as gm
    import goldenmatch.distributed.record_store as rs
    from goldenmatch.core.autoconfig import auto_configure_df

    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    monkeypatch.setenv("GOLDENMATCH_PREPARED_RECORD_STORE_DIR", str(tmp_path))
    monkeypatch.setenv("GOLDENMATCH_PREPARED_RECORD_STORE_PERSIST", "1")

    captured = {}
    original = rs.materialize_bucketed_blocks
    def fake_materialize(store, df, *, block_assignments, n_buckets, signature):
        captured["called"] = True
        captured["n_buckets"] = n_buckets
        captured["n_rows"] = df.height
        return original(store, df, block_assignments=block_assignments,
                        n_buckets=n_buckets, signature=signature)
    # Patch on the source module ONLY. pipeline.py does
    # `from goldenmatch.distributed.record_store import materialize_bucketed_blocks`
    # inside the `if` block, so the import re-reads the module attribute
    # on every dedupe call -- the rs.* patch is sufficient. Patching
    # `goldenmatch.core.pipeline.materialize_bucketed_blocks` would only
    # work if the import were module-level (it isn't).
    monkeypatch.setattr(rs, "materialize_bucketed_blocks", fake_materialize)

    df = _diverse_df()
    cfg = auto_configure_df(df, confidence_required=False)
    cfg.prepared_record_store = True
    cfg.partitioned_block_scoring = True
    gm.dedupe_df(df, config=cfg, confidence_required=False)

    assert captured.get("called"), "pipeline must call materialize_bucketed_blocks"
    assert 1 <= captured["n_buckets"] <= 1024
```

### Task 2.5: Run all tests + lint + commit + PR

- [ ] **Step 1: Run.**

```bash
cd packages/python/goldenmatch
python -m pytest tests/test_bucketed_store.py tests/test_distributed_scoring.py tests/test_partitioned_block_scoring_pipeline.py tests/test_prepared_record_store_pipeline.py tests/test_prepared_record_store_controller.py -v --timeout=180
```

Expected: all green. Phase 1's tests plus the new bucket-mode integration tests pass.

- [ ] **Step 2: Lint.**

```bash
uv run ruff check goldenmatch/core/pipeline.py goldenmatch/backends/ray_backend.py tests/test_distributed_scoring.py tests/test_partitioned_block_scoring_pipeline.py
```

- [ ] **Step 3: Commit + PR.**

Commit message:

```
feat(distributed): Component 2 v2 / Phase 2 -- pipeline + Ray rewiring

Replaces v1's per-block-table dispatch with per-bucket dispatch.
Pipeline.py calls materialize_bucketed_blocks. Ray backend's
in-function task switches from _score_block_remote_by_key to
_score_block_remote_by_bucket; worker loads one bucket Parquet,
partition_by("__block_key__") recovers per-block grouping, scores
each, returns concatenated pairs.

Driver-OOM guard from Component 3 Phase 3 carries over unchanged --
now operates over N <= 1024 bucket futures instead of len(blocks)
futures (strictly better).

Tests:
- test_distributed_scoring.py: _build_small_blocks updated to write
  buckets; obsolete v1 block-not-found test removed; equivalence /
  dispatch / OOM-guard tests added.
- test_partitioned_block_scoring_pipeline.py: positive-case test
  asserts materialize_bucketed_blocks is called.

Spec: docs/superpowers/specs/2026-05-17-distributed-plan-component-2-v2-bucketed-storage-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

PR title: `feat(distributed): Component 2 v2 / Phase 2 — pipeline + Ray rewiring`.

---

## Phase 3 — 5M bench against pre-gen dataset

Re-trigger the bench against `bench-dataset-v1` (already published per PR #297's infrastructure). One workflow_dispatch invocation. No new code needed — the existing bench script and workflow handle both arms.

### Task 3.1: Generate the dataset (if not already published)

- [ ] **Step 1: Check whether `bench-dataset-v1` Release exists.**

```bash
gh release view bench-dataset-v1 --repo benseverndev-oss/goldenmatch
```

If 404, generate it first:

```bash
gh auth switch --user benzsevern
GH_TOKEN=$(gh auth token --user benzsevern) gh workflow run generate-bench-dataset.yml --ref main -f rows=5000000 -f tag=bench-dataset-v1
gh auth switch --user benzsevern-mjh
gh run watch <run-id> --exit-status
```

Wait for completion; verify the asset uploaded.

### Task 3.2: Trigger the bench

- [ ] **Step 1: Bump workflow timeout to 300 min, NOT more.**

The 180-min default didn't fit two 2.5-hour passes (baseline was 160 min in run 25980529866). The TIGHT correct cap is `baseline_wall + target_20pct_better_treatment + 12_min_buffer ≈ 160 + 128 + 12 = 300 min`. If treatment runs >128 min wall, that's the kill criterion failing on wall_pct alone — the workflow timing out IS the FAIL signal.

```yaml
    timeout-minutes: 300
```

**Do not extend beyond 300.** Per escalation #3, a treatment run that hits 300 min has already failed the kill criterion; extending the timeout buys nothing except the same FAIL verdict expressed via JSON instead of CI red.

Commit + push as a one-line PR or merge into Phase 2's PR before triggering.

- [ ] **Step 2: Trigger.**

```bash
gh auth switch --user benzsevern
GH_TOKEN=$(gh auth token --user benzsevern) gh workflow run bench-distributed-stack.yml --ref main -f rows=5000000 -f dataset_tag=bench-dataset-v1
gh auth switch --user benzsevern-mjh
gh run watch <run-id> --exit-status
```

Expected wall: ~2.5h baseline + ~?h treatment. Either side may surface new bugs; the diagnostics from PR #296 (per-run JSON to stdout) make root cause visible.

### Task 3.3: Interpret the verdict

- [ ] **Step 1: Download artifact.**

```bash
mkdir -p .bench-out && rm -rf .bench-out/*
GH_TOKEN=$(gh auth token --user benzsevern) gh run download <run-id> --dir .bench-out
cat .bench-out/*/results.json
gh auth switch --user benzsevern-mjh
```

- [ ] **Step 2: Apply the kill criterion.**

If `kill_criterion.verdict == "PASS"` (≥ 20% wall AND ≥ 20% peak RSS improvement):
- Memo the numbers to `project_distributed_plan_v1_component_1.md`.
- Update CLAUDE.md's Performance section with the v2-vs-chunked result.
- Component 2 v2 stays; plan Component 4 (streaming pair store) as the next horizon.

If `verdict == "FAIL"`:
- Execute the revert. PRs to revert in order: this v2 stack first, then C3 (#289-#291 + #293-#295), then C2 v1 (#287, #283), then C1 (#282, #281, #280).
- Document the negative result in CLAUDE.md and update the project memory with what we learned (chunked is the right path at this scale; the distributed stack doesn't pay until N×N nodes with multi-machine FS).

---

## Acceptance checklist

- [ ] Phase 1 merged: bucketed primitives + Config field + v1 API removed + 8 unit tests green.
- [ ] Phase 2 merged: pipeline + Ray backend rewired; integration tests green; full prepared-store regression slice green.
- [ ] Phase 3: 5M bench run completes; verdict recorded; either v2 stays + Component 4 planned, OR revert executed + negative result documented.

---

## When to escalate

1. **Phase 1 `test_hash_distribution_skew_bounded` fails.** Polars' xxHash should produce well-distributed buckets on 10K random-shaped keys. If skew > 3×, either the seed value is pathological (unlikely with the chosen `0xC2B5C0BBE7ED5E5D`), the test is mis-counting, or Polars changed hash semantics. Debug by inspecting the actual bucket size distribution — `Counter(bucket_id for each block_key)`. Don't lower the threshold without investigation.

2. **Phase 2 equivalence test fails.** Bucket-mode produces a different pair set than df-mode on identical input. Likely cause: a row ordering issue in DuckDB→Arrow→Polars→Parquet→Polars roundtrip, OR `partition_by("__block_key__")` is dropping rows. Debug: collect the bucket df via `load_bucket`, group by `__block_key__` manually, compare to the in-memory `BlockResult` for the same key. If the row sets differ, the bug is in materialize_bucketed_blocks's hash-then-write logic.

3. **Phase 3 bench times out (> 360 min).** The treatment path is taking longer than baseline. Most likely cause: bucket dispatch is fine but per-bucket scoring is slower than ThreadPool-based chunked (Ray task overhead × N buckets > work-per-bucket). Capture the bucket-side stage timings from the per-run JSON. If `partition_blocks_to_buckets` itself is the long pole at 5M (multi-million-row write), investigate Polars `partition_by` performance. Don't extend the timeout further — that's the same slow-walk the kill criterion is designed to prevent. Execute the revert.

4. **Phase 3 bench shows < 20% improvement.** The kill criterion fires. Revert per Task 3.3 Step 2. Don't relitigate the threshold.
