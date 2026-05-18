# Distributed Plan v1 — Component 3: distributed scoring (design)

**Status:** Approved 2026-05-16. Plan to follow.

**Goal:** Add a key-mode dispatch path to the existing Ray backend so workers receive block-keys instead of materialized `BlockResult` objects. Workers call `load_block` on the Component 2 disk store themselves. The driver only holds block-key strings + small shared refs. Distributes the **memory**, not just the CPU.

**Spec lineage:** Components 1 (PRs #280-#282), 2 Phase 1 (#283), 2 Phase 2 (#287) merged. Bench infra (#284-#285) merged. Kill criterion set per [[project-distributed-plan-v1-kill-criterion]]: after Component 3, a 5M end-to-end bench must show ≥ 20% wall AND ≥ 20% peak RSS improvement vs today's `chunked` backend, or the whole stack (PRs #280-#283 + #287 + Component 3 PRs) reverts.

---

## Architecture

Component 3 lives entirely inside `goldenmatch/backends/ray_backend.py`. No new files. `score_blocks_ray(blocks, mk, matched_pairs, ...)` keeps its current signature; two new kwargs (`store_path`, `signature`) and a new internal branch route to **key-mode** when both gating flags are set, otherwise stays on today's **df-mode** path.

```
                    pipeline.py
                         │
                         ▼
            score_blocks_ray(blocks, mk, ...,
                             store_path=?, signature=?)
                         │
        ┌────────────────┴────────────────┐
        │                                 │
    df-mode                         key-mode
 (today's path)              (Component 3, gated)
        │                                 │
ray.put(BlockResult)        ray.put(store_path + sig)
  per block                  + small (key, metadata)
        │                       per block
        ▼                                 │
worker: _score_one_block(           worker: load_block(...)
  block, ...)                          → _score_one_block(...)
        │                                 │
        └────────────────┬────────────────┘
                         ▼
                  list[(ida, idb, score)]
                  (gathered via ray.get)
```

**Why one function with two branches:** the orchestration (`ray.put` of shared state, `ray.get` fan-in, the `len(blocks) <= 4` thread-pool fallback) is identical. Only the `_score_block_remote` task body differs.

**Gating flags (binding):** key-mode activates iff `config.backend == "ray"` AND `config.prepared_record_store` AND `config.partitioned_block_scoring`. Any of the three off → df-mode. The Component 2 Phase 2 wiring (PR #287) already materializes blocks to the disk store when the latter two are on, so key-mode workers find blocks already on disk — no driver-side write needed at scoring time.

**What stays out of scope:** multi-pass blocking's `pre_scored_pairs` field. v1 drops it on the key-mode path; multi-pass users stay on df-mode until Component 4 (streaming pair store) can hold the cross-pass exclude set.

---

## Components

Three units. All inside `goldenmatch/backends/ray_backend.py`; no new files.

### 1. `score_blocks_ray(blocks, mk, matched_pairs, *, store_path=None, signature=None, ...)` — public entry point

New kwargs:

| Kwarg | Type | Default | Meaning |
|---|---|---|---|
| `store_path` | `str \| None` | `None` | Filesystem path to the `PreparedRecordStore` DuckDB file. |
| `signature` | `str \| None` | `None` | Block signature produced by `_prep_cache_signature(config)`. |

Branching rules (in priority order):
- `len(blocks) <= 4` → fall to `score_blocks_parallel`, identical to today's small-block fast path. Applies to both df-mode and key-mode users — at exactly 4 blocks, key-mode does NOT engage, even if its gating kwargs are set.
- Both `store_path` and `signature` set AND `len(blocks) > 4` → **key-mode** (Component 3 path).
- Otherwise → today's **df-mode** (unchanged).

Existing kwargs (`across_files_only: bool`, `source_lookup: dict[int, str] | None`, `target_ids: set[int] | None`) forward unchanged to both modes — the kwarg list grows by exactly two, none are dropped on either branch.

Pipeline integration: `pipeline.py`'s call site at the fuzzy-scoring stage (currently around line 865) gains a small block: when `config.partitioned_block_scoring` and `config.prepared_record_store` and `_prep_store is not None`, pass `store_path=str(_prep_store.path)` and `signature=_prep_cache_signature(config)`. The pipeline doesn't need to know which mode the backend will pick.

### 2. `_score_block_remote_by_key` — new Ray task body

Replaces `_score_block_remote` for key-mode. The `_KeyModeBlock` shim is a **module-level `@dataclass(frozen=True)`** at the top of `ray_backend.py` — must be module-level so Ray's pickling can resolve it on workers; a nested class breaks serialization. Two fields only: `block_key: str` and `df: pl.DataFrame`.

Signature:

```python
@ray.remote(max_retries=0)
def _score_block_remote_by_key(
    block_key: str,
    store_path: str,
    signature: str,
    mk_config,
    exclude,
    src_lookup,
    across_only: bool,
) -> list[tuple[int, int, float]]: ...
```

Body (structured as `try` / `finally` so the error path can't leak the DuckDB handle):

```python
store = PreparedRecordStore(path=store_path, cleanup=False, read_only=True)
try:
    block_df = load_block(store, signature=signature, block_key=block_key)
    if block_df is None:
        raise RuntimeError(
            f"Component 3: block_key={block_key!r} not found in store at "
            f"{store_path} for signature={signature!r} — likely cause is "
            f"signature drift between driver and worker (config mutated "
            f"mid-run) or off-by-one in block_assignments"
        )
    shim = _KeyModeBlock(block_key=block_key, df=block_df)
    pairs = _score_one_block(
        shim, mk_config, exclude,
        across_files_only=across_only, source_lookup=src_lookup,
    )
finally:
    store.close()
return pairs
```

The `finally` runs on every exit — success, `RuntimeError` from step `load_block is None`, or exceptions from `_score_one_block`. No handle leak on any path.

### 3. `_score_one_block` (existing, in `core/scorer.py`) — no changes

Already takes a `BlockResult`-shaped object. The `_KeyModeBlock` shim in #2 (`dataclass(frozen=True)` with just `block_key` + `df`) satisfies its contract.

**Dependency rule:** Component 3 imports from Components 1+2 (`PreparedRecordStore`, `load_block`) but nothing in Components 1+2 imports from `ray_backend.py`. One-way dependency keeps the optional `[ray]` extra honest.

### Extension to `PreparedRecordStore` (Component 1)

A `read_only: bool = False` kwarg gets added to `PreparedRecordStore.__init__`. When `True`, the underlying `duckdb.connect(str(self.path), read_only=True)` lets multiple processes open the same `.duckdb` file concurrently without write-lock contention. The driver (single writer) keeps `read_only=False`; workers always pass `read_only=True`.

---

## Data flow

End-to-end for one `dedupe_df` call with all three flags on (`backend="ray"`, `prepared_record_store=True`, `partitioned_block_scoring=True`):

**1. Pipeline driver (single process)**
- `_run_dedupe_pipeline` runs prep stages → `_PREP_CACHE` populated, prepped df also written to `_prep_store` (Component 1, PR #281).
- `build_blocks(combined_lf, config.blocking)` → `list[BlockResult]`, in-memory.
- Component 2 Phase 2 hook (existing, PR #287): iterate blocks, build `block_assignments: dict[int, str]`, call `materialize_blocks(_prep_store, prepped_full, block_assignments, signature=_prep_cache_signature(config))`. After this, every `block_key` in the run has a DuckDB table on disk.
- `block_scorer = _get_block_scorer(config)` resolves to `score_blocks_ray`.
- Driver calls `score_blocks_ray(blocks, mk, matched_pairs, store_path=str(_prep_store.path), signature=_prep_cache_signature(config), ...)`.

**2. Ray backend driver-side (single process, in `score_blocks_ray`)**
- `_ensure_ray()` initializes Ray (local or cluster).
- Detect key-mode: `store_path is not None AND signature is not None AND len(blocks) > 4`.
- `mk_ref = ray.put(mk)`, `exclude_ref = ray.put(frozenset(matched_pairs))`, `source_ref = ray.put(source_lookup) if source_lookup else None`.
- For each block, submit `_score_block_remote_by_key.remote(block.block_key, store_path, signature, mk_ref, exclude_ref, source_ref, across_files_only)`. The driver never reads `block.df` in this path.
- `pairs_per_block = ray.get([futures])` — driver gathers all pair lists.
- Before concatenation: guard against driver-OOM (see error handling §5).
- Driver concatenates into a flat `list[tuple[int, int, float]]` and returns.

**3. Ray worker (one task per block, runs on a Ray worker process)**
- Receive `(block_key, store_path, signature, mk_ref, exclude_ref, source_ref, across_files_only)`. Refs deserialize zero-copy from Ray's object store.
- `store = PreparedRecordStore(path=store_path, cleanup=False, read_only=True)`. `cleanup=False` is critical: workers must never delete the file.
- `block_df = load_block(store, signature=signature, block_key=block_key)`. If `None`, close + raise (§ error handling §1).
- Construct `_KeyModeBlock(block_key=block_key, df=block_df)`.
- `pairs = _score_one_block(shim, mk, exclude, across_files_only=..., source_lookup=...)`.
- `store.close()` in `finally`. Return `pairs`.

**Memory contract:** the driver never holds more than `len(blocks)` × a few hundred bytes (the small `BlockResult` shells minus their dfs, which df-mode would have shipped via `ray.put`). Each worker holds at most one block df at a time plus the shared refs. At 50M rows / 5K blocks of 10K rows each, driver peak drops from ~5GB (full prepped + all blocks) to ~50MB (block keys + ref handles). Worker peak is bounded by the largest single block.

---

## Error handling

### 1. Worker can't find block on disk (`load_block` returns `None`)

Two likely causes:
1. **Signature drift** between driver and worker — config mutated mid-run, or the driver/worker computed `_prep_cache_signature(config)` against different config states.
2. **Off-by-one in `block_assignments`** — Component 2 Phase 2's iteration missed this row → `materialize_blocks` didn't write a table for this key.

Worker raises `RuntimeError(f"Component 3: block_key={key!r} not found in store at {store_path} for signature={signature!r} — likely cause is signature drift between driver and worker (config mutated mid-run) or off-by-one in block_assignments")`. Ray surfaces it on `ray.get`, the whole `score_blocks_ray` call fails. Not silently recoverable mid-run.

### 2. DuckDB read failure / corrupted database file

DuckDB raises `duckdb.IOException` or similar. Worker doesn't catch — Ray catches, marks task failed, surfaces. **Component 3 v1 disables Ray's default task retry** (`max_retries=0`) because §2 failures are deterministic (same file, same key → same error) and §1 failures are deterministic (wrong signature → still wrong on retry); retrying burns time before the same crash.

**Acknowledged tradeoff:** the §4 case (transient OOM or segfault on one worker that might succeed on another) loses the free recovery. v1 accepts this — the user can rerun the whole job. If transient-failure recovery becomes valuable, a per-failure-type retry policy is a v2 follow-up.

### 3. Concurrent DuckDB read contention on Windows

Multiple workers opening the same `.duckdb` file simultaneously is the most likely real production failure. DuckDB on Linux uses a shared-read lock and tolerates this; **Windows is the unknown.** Mitigation:
- Worker opens with `read_only=True` (new kwarg on `PreparedRecordStore`). Lets multiple processes read concurrently without write-lock contention.
- The driver (single writer) stays writable.
- If Windows-specific issues surface in CI, fall back to one-worker-per-file by sharding the store at materialize time. Documented as a known follow-up risk, not built in v1.

### 4. Ray worker crash mid-task (OOM, segfault)

Ray's default scheduling reattempts crashed tasks. Combined with `max_retries=0` from §2: a crash returns failure to the driver. User gets a stack trace + partial-progress message (`scored M of N blocks before failure`). No partial output is written.

### 5. Driver-side gather exceeds memory at 50M+

The `ray.get([futures])` materializes all pairs to driver RAM. This is the **explicit Component 4 boundary** — documented in the PR body and CLAUDE.md.

**Check timing (precise):** gather pairs incrementally via `ray.wait(futures, num_returns=1)` in a loop. After each completed task, sum the cumulative pair count and compare to a per-run **budget**.

Budget math:
- One pair = a 3-tuple `(int, int, float)`. CPython object overhead puts this at **~80 bytes** in a flat Python list (tuple header ~56 bytes + 2 cached small-ints + 1 float, plus the list slot's pointer). Use `_PAIR_BYTES_ESTIMATE = 80` as a module-level constant.
- Budget: `budget_bytes = psutil.virtual_memory().available * 0.5`.
- Threshold (number of pairs that fit): `budget_pairs = budget_bytes // _PAIR_BYTES_ESTIMATE`.

When cumulative `n_pairs > budget_pairs`, cancel remaining futures via `ray.cancel(f)` and raise:

```python
raise MemoryError(
    f"Component 3: scored pairs ({n_pairs:,}) would exceed 50% of "
    f"available driver RAM ({budget_bytes // (1024*1024)} MB budget, "
    f"~{_PAIR_BYTES_ESTIMATE} bytes/pair) — switch to backend='chunked' "
    f"or wait for Component 4 (streaming pair store)"
)
```

This trades a small wall overhead (incremental gather vs. one `ray.get`) for the ability to fail fast before allocation. The 80-byte estimate is conservative — true overhead varies with Python version and tuple caching; underestimating it would let the guard fire late (after allocation), which defeats the purpose. If the 50% headroom turns out too aggressive in bench (false-positives), tune to 0.6-0.7 in a follow-up rather than dropping the guard.

**Dependency note:** `psutil>=5.9` is already in `goldenmatch`'s hard deps (per `pyproject.toml`); no new install requirement.

### 6. Worker store path differs from driver

On a Ray cluster, workers may run on remote machines without access to the driver's local `_prep_store.path`. **v1 requires a shared filesystem (NFS, GCS-fuse, etc.) when running on a multi-node cluster.** Local single-machine Ray (the default `ray.init()` path) sees the same filesystem as the driver. Documented as a constraint, not a code change — workers downloading the store via S3/etc. is Component 4+ scope.

### Logging

Every worker logs one line at start (`block_key=...` first 8 hex of hash for privacy), one at end (elapsed + pair count). Driver logs the dispatch summary (block count, mode picked, total elapsed).

---

## Testing strategy

Tests live in `packages/python/goldenmatch/tests/test_distributed_scoring.py` (new). All tests use `pytest.importorskip("ray")`.

### Unit-level (no Ray, ~5 tests)

- `_KeyModeBlock` shim exposes `.block_key` and `.df` — verifies `_score_one_block` accepts it without modification.
- `PreparedRecordStore(read_only=True)` opens a writable-by-driver store and rejects write attempts (regression anchor for the new kwarg).
- `score_blocks_ray` with `store_path=None` falls through to df-mode unchanged.
- `score_blocks_ray` with `store_path` set + `len(blocks) <= 4` falls back to `score_blocks_parallel`.
- `score_blocks_ray` driver-side dispatch: monkeypatch `_score_block_remote_by_key.remote` to a fake that records kwargs; assert driver sends `block_key` strings + shared `store_path`, never `BlockResult.df`.

### Integration (one Ray task, ~4 tests)

- **Equivalence:** end-to-end key-mode round-trip on a small df. Same input → same pairs whether df-mode or key-mode. Without this, key-mode silently producing different pairs is unobservable.
- **Block-not-found:** force by passing a wrong `signature`; worker raises `RuntimeError` with the documented message (assert it mentions BOTH "signature drift" and "block_assignments" so the diagnostic covers either root cause).
- **Concurrent readers via Ray (best-effort):** 2-worker run against the same file; both succeed. Useful as a regression anchor; if Ray's local mode serializes workers it passes trivially.
- **Cross-process concurrent readers (independent of Ray):** spawn 2 `multiprocessing.Process`es that each open `PreparedRecordStore(path=same, cleanup=False, read_only=True)` and call `load_block` simultaneously. Both must succeed. This is the direct test of the §3 Windows concurrent-read concern that Ray's local mode might paper over. Mark `pytest.mark.skipif(sys.platform != "win32")` to focus on the platform-specific risk; the Linux behavior is well-understood.

### Pipeline integration (~3 tests, extend `test_partitioned_block_scoring_pipeline.py`)

- **All flags on, kwargs reach the backend:** monkeypatch `score_blocks_ray` to a fake that records its kwargs, run `dedupe_df` with all three flags on. Assert `store_path` and `signature` were both passed. **Do NOT skip when `ray` isn't installed** — the monkeypatch replaces the real backend, so Ray's presence is irrelevant for this assertion. Skipping would leave the default CI lane (which doesn't have `ray`) blind to pipeline-side regressions that silently drop the kwargs.
- **All flags on, end-to-end equivalence:** `dedupe_df` produces the same clusters as `backend=None`. End-to-end semantic equivalence.
- `backend="ray"` but `prepared_record_store=False` → pipeline does NOT pass `store_path` to `score_blocks_ray` (df-mode unchanged for users who turned on Ray but not the disk store).

### Bench (kill checkpoint, separate from unit tests)

Extend `scripts/bench_prepared_store.py` (or sibling) to compare:
- Baseline: `backend="chunked"` (today's recommended large-N path).
- Treatment: `backend="ray"` + `prepared_record_store=True` + `partitioned_block_scoring=True`.

Same synthetic fixture, both at **5M rows on `large-new-64GB`**. Per the kill criterion: if treatment doesn't beat baseline by **≥ 20% wall AND ≥ 20% peak RSS**, revert PRs #280-#283 + #287 + Component 3 PRs and document the negative result in CLAUDE.md.

Bench runs as a separate `workflow_dispatch` after Component 3 lands; not part of regular CI.

### Not tested in v1

- Multi-node Ray cluster (no CI infra; v2 follow-up).
- Multi-pass blocking's `pre_scored_pairs` (key-mode drops; df-mode keeps).
- Windows concurrent-reader robustness beyond the 2-worker happy path.

---

## Decisions log

- **Process model: extend Ray** (vs. multiprocessing or both). Ray is already wired; doubles less surface area than building a parallel impl.
- **Pair return: gather to driver memory.** Driver-OOM ceiling is explicit Component 4 boundary.
- **One function with two branches** (vs. new function alongside). Orchestration is identical; the dispatch body is the only difference.
- **`read_only=True`** kwarg on `PreparedRecordStore` for worker-side reads (vs. one-store-per-worker). Lets multiple readers coexist without sharding the store.
- **`max_retries=0`** on the Ray task (vs. default retry-on-failure). Block-not-found is deterministic; retrying wastes time.
- **Multi-pass blocking dropped from key-mode v1.** `pre_scored_pairs` needs cross-pass exclude state that Component 4 will handle.

---

## Followups (deferred)

- Multi-node Ray cluster CI lane (needs real cluster infra).
- Sharded store path (one DuckDB file per shard) if Windows concurrent-read issues surface.
- Component 4 (streaming pair store) — removes the driver-OOM ceiling at §5.
- Component 5 (distributed clustering) — only fires after Component 4.
- Component 6 (planner integration) — teaches the v3 planner when to pick the distributed stack.
