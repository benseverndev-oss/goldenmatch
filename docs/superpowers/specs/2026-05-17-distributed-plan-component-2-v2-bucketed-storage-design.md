# Distributed Plan v1 — Component 2 v2: bucketed Parquet storage (design)

**Status:** Drafted 2026-05-17, autonomous execution authorized. To be reviewed.

**Goal:** Replace Component 2 v1's "one DuckDB table per block" storage with hash-bucketed Parquet files. Workers receive a bucket file containing many blocks; they recover per-block grouping in-worker via `partition_by`. Standard industry pattern (Spark `bucketBy`, Iceberg hidden partitioning, Splink's distribution model).

**Spec lineage:**
- Component 1 (PRs #280-#282): prepared-record store. Survives.
- **Component 2 v1 (PRs #283 + #287): drops.** `materialize_blocks` / `load_block` / `iter_blocks` / `list_blocks` are removed. Local probe established the design fails: 10K blocks took 36s + 5.4 ms/block; 100K blocks took 62 min + 21 GB of DuckDB catalog metadata (210 KB/table overhead); 1.67M blocks (the real auto-config workload) would have hung or OOM'd.
- Component 3 (PRs #289-#291 + #293-#295): worker dispatch path stays; the storage primitive it reads from changes.
- Bench (PR #296 + #297): re-runnable against v2 with no script changes once the wiring switches.

**Kill criterion (kept binding):** after v2 ships, the 5M bench using the pre-generated dataset (PR #297) must show ≥ 20% wall AND ≥ 20% peak RSS improvement vs `backend="chunked"`. If it doesn't, revert PRs #280-#297 + the v2 PRs and document the negative result. The override that bought us this redesign was contingent on producing a falsifiable retry, not on relitigating the criterion again.

---

## Architecture

Storage shape: **N hash-bucketed Parquet files** under `store_dir/buckets/bucket=K/data.parquet`. One file per hash bucket. Each file contains all rows whose `hash(block_key) % N == K`, with a `__block_key__` string column preserved so workers can recover per-block grouping.

```
                    pipeline.py
                         │
                         ▼
           build_blocks  →  block_assignments
                         │  {__row_id__: block_key}
                         ▼
    materialize_bucketed_blocks(store_dir, df,
                                block_assignments, n_buckets=N)
                         │
                         ▼
           store_dir/buckets/bucket=0/data.parquet
           store_dir/buckets/bucket=1/data.parquet
                              ...
           store_dir/buckets/bucket={N-1}/data.parquet
                         │
                         ▼
           score_blocks_ray(..., bucket_dir=..., n_buckets=N)
                         │  (1 Ray task per bucket)
                         ▼
     worker: load_bucket(bucket_path)
              → df.partition_by("__block_key__")
              → for each block_df: _score_one_block(...)
              → return concatenated pairs
                         │
                         ▼
                  ray.get(...) → driver
```

**Why this works where v1 didn't:**

- **N is bounded.** Default `max(cpu_count() * 4, 64)`, hard-capped at 1024. At 5M rows / 200 buckets ≈ 25K rows per file (~1 MB on-disk Parquet with snappy compression; ~25 MB decompressed in worker memory — typical ~25× ratio for short-string columnar data). Total ~200 files vs. v1's 1.67M tables.
- **Parquet has no catalog.** Each file is independent; no metadata data structure that scales with N. Compare to DuckDB's ~210 KB per table.
- **Worker wall amortizes.** 200 Ray tasks × ~ms task overhead instead of 1.67M × ~ms. Per-task work is ~25K rows of scoring; substantial enough to dwarf Ray's per-task cost.
- **Standard pattern.** Spark, Iceberg, Splink all use hash bucketing for fine-grained keys. We're getting on the well-trodden path.

**Gating:** unchanged from Component 3 — key-mode (now bucket-mode) activates when `config.backend == "ray"` AND `config.prepared_record_store == True` AND `config.partitioned_block_scoring == True`. The third flag's meaning is preserved; it now toggles bucketed storage instead of per-block storage. Default off; df-mode path unchanged.

**Out of scope for v2:**

- Multi-pass blocking's `pre_scored_pairs` (still dropped in bucket-mode v2; same rationale as Component 3 v1).
- Bucket-count auto-tuning beyond the default heuristic. If 200 isn't right, the user passes `n_buckets`.
- Multi-node Ray cluster filesystem semantics. Still requires shared FS, same as v1.

---

## Components

All inside `goldenmatch/distributed/record_store.py` (replacing v1's block-API code) and `goldenmatch/backends/ray_backend.py` (updating worker body).

### 1. `materialize_bucketed_blocks(store, df, *, block_assignments, n_buckets, signature)` — new primary write API

Signature:

```python
def materialize_bucketed_blocks(
    store: PreparedRecordStore,
    df: pl.DataFrame,
    *,
    block_assignments: dict[int, str],
    n_buckets: int,
    signature: str,
) -> Path: ...
```

Behavior:
1. Attach `__block_key__` to `df` via `replace_strict` against `block_assignments` (vectorized; same pattern as the golden-record build from PR #295 era).
2. Compute `__bucket__ = pl.col("__block_key__").hash(seed=BUCKET_HASH_SEED) % n_buckets`. Polars' built-in `.hash()` is deterministic given a fixed seed (xxHash-based); same value across runs and processes. We pin `BUCKET_HASH_SEED = 0xC2B5C0BBE7ED5E5D` as a module-level constant so a seed change is an explicit, reviewable event.
3. Write via `df.partition_by("__bucket__", as_dict=True)` → iterate `(bucket_id, bucket_df)` pairs → write each at `bucket_dir / f"bucket={bucket_id}" / "data.parquet"` (Hive-style layout matching §Architecture). For each bucket: `bucket_path.parent.mkdir(parents=True, exist_ok=True)` then `bucket_df.drop("__bucket__").write_parquet(bucket_path, compression="snappy")`. Polars' partition-by-then-write is the standard Spark-equivalent pattern.
4. Return the `bucket_dir` path: `store.path.parent / f"buckets_{_sanitize_signature(signature)}"`. `_sanitize_signature` is the existing helper in `record_store.py` from Component 1: `hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]`. Reusing it keeps v1's signature → file-system-safe-suffix mapping intact; **every** sibling directory and the `sig_hash` strings in §Components #5 and §Pipeline wiring resolve through this single helper.

The hash and `n_buckets` are stored implicitly in the directory name + file layout — workers don't need them at runtime.

### 2. `load_bucket(bucket_path) -> pl.DataFrame` — worker-side read

```python
def load_bucket(bucket_path: Path) -> pl.DataFrame:
    return pl.read_parquet(bucket_path)
```

Trivial. Lifted to a function (vs. inlined) so a future enhancement (e.g. streaming, column projection) has one site to change. No DuckDB; the prepared-record-store DuckDB file from Component 1 is **only** used for the `_PREP_CACHE` analog. Bucket data lives in Parquet, separate.

### 3. `iter_buckets(bucket_dir) -> Iterator[tuple[int, Path]]` — driver-side enumeration

```python
def iter_buckets(bucket_dir: Path) -> Iterator[tuple[int, Path]]: ...
```

Yields `(bucket_id, path_to_parquet)` for each `bucket=K/data.parquet` under `bucket_dir`. Sorted by `bucket_id` for determinism. Workers receive these paths; the driver never reads bucket contents.

**Missing-directory semantics:** when `bucket_dir` does not exist (flag-on but `materialize_bucketed_blocks` never called — e.g. `build_blocks` returned empty), `iter_buckets` yields zero items rather than raising. The downstream effect is the Ray dispatch produces zero futures and `score_blocks_ray` short-circuits to an empty pair list — same shape the empty-blocks early-return at the top of the function produces. Caller-visible result is "no pairs scored" rather than a crash; matches v1's behavior on the same edge case.

### 4. `_score_block_remote_by_key` → `_score_block_remote_by_bucket` (in `ray_backend.py`)

The Ray task body changes shape:

```python
@ray.remote(max_retries=0)
def _score_block_remote_by_bucket(
    bucket_path: str,
    mk_config,
    exclude,
    src_lookup,
    across_only: bool,
):
    from goldenmatch.core.scorer import _score_one_block
    from goldenmatch.distributed.record_store import load_bucket

    bucket_df = load_bucket(Path(bucket_path))
    all_pairs: list[tuple[int, int, float]] = []
    # Per-block grouping recovered in-worker. partition_by yields
    # (key, df) tuples; we ignore the key since _score_one_block uses
    # block.block_key only for logging.
    for block_key, block_df in bucket_df.partition_by(
        "__block_key__", as_dict=True
    ).items():
        shim = _KeyModeBlock(block_key=block_key, df=block_df.lazy())
        all_pairs.extend(
            _score_one_block(
                shim, mk_config, exclude,
                across_files_only=across_only, source_lookup=src_lookup,
            )
        )
    return all_pairs
```

Replaces the per-block task in PR #290's Phase 2. Same `_KeyModeBlock` shim (still module-level). Worker memory is bounded by bucket size (`5M / n_buckets`), not per-block size.

### 5. `score_blocks_ray` dispatch updates (in `ray_backend.py`)

Replace the per-block dispatch loop with per-bucket dispatch when bucket-mode is on:

```python
if use_bucket_mode:  # store_path + signature both non-None
    from goldenmatch.distributed.record_store import (
        _sanitize_signature,
        iter_buckets,
    )
    sig_hash = _sanitize_signature(signature)
    bucket_dir = Path(store_path).parent / f"buckets_{sig_hash}"
    futures = []
    for _bucket_id, bucket_path in iter_buckets(bucket_dir):
        futures.append(
            _score_block_remote_by_bucket.remote(
                str(bucket_path),
                mk_ref, exclude_ref, source_ref,
                across_files_only,
            )
        )
```

The existing incremental `ray.wait` + driver-OOM guard (PR #291) carries through unchanged — bucket-mode just produces N futures instead of len(blocks) futures.

### 6. Pipeline wiring update (in `pipeline.py`)

The Phase 2 hook in PR #287 (Component 2 v1) called `materialize_blocks(...)`. v2 replaces that single call with `materialize_bucketed_blocks(...)`, computing `n_buckets` from the config (default `max(cpu_count() * 4, 64)`, capped at 1024).

```python
if (
    config.prepared_record_store
    and config.partitioned_block_scoring
    and _prep_store is not None
):
    from goldenmatch.distributed.record_store import (
        materialize_bucketed_blocks,
    )
    # Same construction as Component 2 v1 (PR #287). Iterate the in-memory
    # block list once, collect each LazyFrame to read its __row_id__
    # column, write the row_id -> block_key map. Last-write-wins on
    # multi-pass blocking (a row in two blocks gets the second block_key);
    # documented as a known limitation -- pre_scored_pairs is dropped in
    # bucket-mode v2 same as v1.
    block_assignments: dict[int, str] = {}
    for blk in blocks:
        df_blk = blk.df.collect() if isinstance(blk.df, pl.LazyFrame) else blk.df
        for rid in df_blk["__row_id__"].to_list():
            block_assignments[int(rid)] = blk.block_key

    n_buckets = config.n_buckets or max((os.cpu_count() or 1) * 4, 64)
    n_buckets = min(n_buckets, 1024)
    with stage("partition_blocks_to_buckets"):
        materialize_bucketed_blocks(
            _prep_store,
            combined_lf.collect(),
            block_assignments=block_assignments,
            n_buckets=n_buckets,
            signature=_prep_cache_signature(config),
        )
```

The Phase 5 wiring (PR #294) is unchanged: kwargs passed to `score_blocks_ray` stay `store_path` + `signature`. The backend computes `bucket_dir` from those.

### Configuration additions

`GoldenMatchConfig`:

- `n_buckets: int | None = None` — caller override; `None` uses the default heuristic. Validated to fit `1 <= n_buckets <= 1024`.

That's the only new field. Existing flags (`prepared_record_store`, `partitioned_block_scoring`) are unchanged.

---

## Data flow

End-to-end for `dedupe_df` with all three flags on at 5M rows:

**Driver (single process)**
1. Prep stages → prepared df materialized + cached.
2. `build_blocks(...)` → `list[BlockResult]` (1.67M blocks; in-memory list of LazyFrames, lightweight).
3. Compute `block_assignments: dict[__row_id__, block_key]` from the block list.
4. `n_buckets = 200` (default on 16-core runner).
5. `materialize_bucketed_blocks(store, prepped_df, block_assignments, n_buckets=200, signature=...)` writes 200 Parquet files at `store_dir/buckets_<sig>/bucket={0..199}/data.parquet`. Each file holds ~25K rows.
6. `score_blocks_ray(blocks, mk, ..., store_path=str(store.path), signature=sig)` dispatches.

**Ray driver-side**
- `_ensure_ray()`, `ray.put(mk_ref, exclude_ref, source_ref)`.
- For each of the 200 buckets: `_score_block_remote_by_bucket.remote(bucket_path, refs..., across_files_only)`.
- Incremental `ray.wait` + OOM guard from PR #291 carries over unchanged.

**Ray worker**
- Receive `bucket_path`, load the ~25K-row Parquet.
- `partition_by("__block_key__")` → `dict[str, pl.DataFrame]` of ~8K blocks per worker.
- For each block: construct `_KeyModeBlock(block_key, df.lazy())`, call `_score_one_block`, accumulate pairs.
- Return ~25K pairs (approximately — depends on real duplicate density).

**Memory contract at 5M (per-phase, not steady-state):**

| Phase | Driver peak | Worker peak |
|---|---|---|
| Pre-materialize | full prepped df (~1 GB at 5M / 4 col) | n/a |
| During `materialize_bucketed_blocks` | prepped df + partition_by overhead (~1.5 GB) | n/a |
| Post-materialize, pre-dispatch | bucket-path strings + Ray refs (~MB) | n/a |
| During Ray dispatch + gather | pair list (bounded by `_PAIR_BYTES_ESTIMATE` × cumulative pair count, OOM-guarded per §Error handling #6) | one bucket df (~25 MB) + per-block grouping overhead |
| Post-gather | concatenated pair list (driver-side OOM-guarded) | n/a |

The "MB-only driver" claim only applies post-materialize. Materialize itself is bounded by the full prepped df, same as v1. The win is operational: post-materialize, driver memory drops to nothing while workers do the heavy lifting in parallel.

- Total disk during a run: ~200 MB Parquet, replacing v1's projected 21 GB DuckDB file at 100K tables.

---

## Error handling

### 1. `n_buckets` validation

Pydantic field validator: `1 <= n_buckets <= 1024`. Out-of-range values raise at config construction, not at materialize time.

### 2. Bucket file missing at worker read

`load_bucket(path)` calls `pl.read_parquet(path)`. If the file doesn't exist, Polars raises `ComputeError`. Worker doesn't catch; Ray surfaces to driver. `max_retries=0` (inherited from Component 3 v1 decisions).

### 3. Hash collision tolerance

Two different `block_key` values landing in the same bucket is by design and handled by the in-worker `partition_by("__block_key__")`. Not a bug.

### 4. Empty buckets

When N > distinct block keys (e.g. test fixtures), some buckets receive zero rows. `partition_by` on an empty df returns an empty dict. `df.partition_by("__bucket__", as_dict=True)` only emits buckets that actually contain rows — empty buckets get no Parquet file written. Therefore `iter_buckets` yields only non-empty buckets. Worker dispatch count = number of non-empty buckets, not N. Documented in `iter_buckets`'s docstring.

### 5. Concurrent reads of the same Parquet file

Multiple workers reading the same Parquet file is trivially safe (Parquet readers are stateless against the file; no locks). This was the spec §3 worry under the v1 DuckDB design that no longer applies.

### 6. Driver-side OOM guard

Inherited from PR #291 unchanged. The `_PAIR_BYTES_ESTIMATE = 80` constant + incremental `ray.wait` gather + `MemoryError` carry over. Now operates over N ≤ 1024 futures instead of `len(blocks)` futures — strictly better (fewer futures to gather, fewer `ray.wait` round trips, same cumulative-pair budget enforcement). Test coverage: `test_oom_guard_fires_at_bucket_granularity` in the integration suite directly exercises this.

### 7. Bucket-directory cleanup on store close

`PreparedRecordStore.close()` currently removes the DuckDB file under `cleanup=True`. v2 also writes a sibling `buckets_<sig>/` directory tree which is NOT cleaned by the existing logic. Without an explicit cleanup hook, repeated runs with different signatures accumulate `buckets_<sig_a>/`, `buckets_<sig_b>/`, ... in `store.path.parent` indefinitely.

Fix: extend `PreparedRecordStore.close()` to walk siblings via `Path.glob`:

```python
if self._cleanup and self._owns_file:
    for sibling in self.path.parent.glob("buckets_*"):
        if sibling.is_dir():
            shutil.rmtree(sibling, ignore_errors=True)
```

`shutil.rmtree` does NOT expand globs itself — the iteration above is load-bearing. `ignore_errors=True` swallows benign Windows file-locking races (matches v1's `unlink(missing_ok=True)` style). Gated on `cleanup=True` and `_owns_file=True`; when `cleanup=False` (the persistence path used by the bench's pre-generated dataset workflow), bucket dirs persist same as the DuckDB file — explicit operator control. New test: `test_close_removes_bucket_dirs_when_cleanup_true`.

---

## Testing strategy

Tests in **`tests/test_distributed_scoring.py`** (extending the existing file) and **`tests/test_bucketed_store.py`** (new file replacing `tests/test_block_partitioned_store.py`).

### Unit (no Ray needed, ~8 tests in `test_bucketed_store.py`)

- `test_materialize_writes_at_most_n_files`: small df + N=4 → ≤ 4 files (empty buckets skipped per §Error handling #4). Don't assert exactly N; with small fixtures and hashing skew the count is bounded above by N, not equal to N.
- `test_load_bucket_roundtrip`: write a single bucket via Polars manually, `load_bucket` returns the same rows.
- `test_iter_buckets_yields_sorted`: 4 buckets in the dir → iter yields `(0, ...), (1, ...), (2, ...), (3, ...)`.
- `test_iter_buckets_missing_directory_yields_empty`: `iter_buckets(/non/existent)` yields zero items, doesn't raise. Anchors §Components #3 missing-dir semantics.
- `test_hash_is_deterministic_across_calls`: materialize twice with the same `BUCKET_HASH_SEED`; same block_key lands in same bucket every time.
- `test_n_buckets_bounds_validated`: `GoldenMatchConfig(n_buckets=0)` raises Pydantic ValidationError; `n_buckets=2000` raises (cap = 1024); `n_buckets=None` accepted (heuristic default).
- `test_empty_block_assignments_writes_zero_files`: edge case — no-op materialize, no buckets, `iter_buckets(bucket_dir)` yields empty.
- `test_hash_distribution_skew_bounded`: 10K block_keys hashed into N=32; max bucket size / min non-empty bucket size ≤ 3. Loose bound (Polars xxHash is well-distributed but not perfect on small inputs); guards against accidental seed change producing pathological skew that would break the memory-bound claim in §Data flow.

### Integration (real Ray, ~4 tests in `test_distributed_scoring.py`)

- `test_bucket_mode_equivalence_with_df_mode`: same df scored two ways (df-mode + bucket-mode). Compare as **sets** of canonicalized tuples `(min(a, b), max(a, b), round(score, 6))` — ordering is non-deterministic across buckets, only the pair set is semantically meaningful. The load-bearing semantic invariant.
- `test_bucket_mode_dispatches_n_tasks`: assert `len(futures) == n_non_empty_buckets`, NOT `len(blocks)`.
- `test_worker_recovers_per_block_grouping`: monkey-patch `_score_one_block` to record `block_key` per call. Assert the set of block_keys seen across all workers equals the set in the original `block_assignments`.
- `test_oom_guard_fires_at_bucket_granularity`: similar to Component 3 Phase 3's `test_driver_oom_guard_raises_when_budget_exceeded`. Monkey-patch `psutil.virtual_memory` to claim 80 bytes available; run bucket-mode against the standard fixture; assert `MemoryError("scored pairs")` is raised. Locks in §Error handling #6's "strictly better" claim — guard works with N futures the same way it worked with len(blocks).

### Pipeline integration (extend `test_partitioned_block_scoring_pipeline.py`, ~1 test)

- `test_pipeline_uses_bucketed_materialize_on_flag_on`: monkey-patch `materialize_bucketed_blocks`; assert it's called with the expected `n_buckets` value when all three flags are on.

### v1 test removal

`tests/test_block_partitioned_store.py` (Component 2 v1, 7 tests) deletes entirely. `_block_table_name`, `materialize_blocks`, `load_block`, `iter_blocks`, `list_blocks` and their tests all go.

`tests/test_distributed_scoring.py`'s Phase 2/4 dispatch tests get rewritten for bucket-mode where they referenced per-block dispatch.

### Bench (kill checkpoint, unchanged)

`scripts/bench_distributed_stack.py` doesn't need code changes — it calls `score_blocks_ray` with the same kwargs. The only difference: the workflow downloads the pre-generated parquet via the new `bench-dataset-v1` Release asset (PR #297 infrastructure).

Re-bench command after v2 ships:

```bash
gh workflow run bench-distributed-stack.yml \
  --ref main \
  -f rows=5000000 \
  -f dataset_tag=bench-dataset-v1
```

This one workflow run executes **both** arms of the comparison in sequence: `scripts/bench_distributed_stack.py` does `run_one(backend="chunked", ...)` then `run_one(backend="ray", prepared_record_store=True, partitioned_block_scoring=True)` against the same loaded parquet, then computes the diff + kill verdict. PR #297's harness fix added `if: always()` on the upload step so the JSON artifact lands on FAIL too. Kill criterion stays binding.

---

## Decisions log

- **Hash-bucketed Parquet** (vs. per-block tables). Industry SOP. Probe established v1's per-table approach fails at the workload auto-config picks.
- **N default `max(cpu_count() * 4, 64)`, cap 1024.** Matches Spark's `bucketBy` default heuristic; high enough to give parallelism, low enough to keep per-task work meaningful.
- **`partition_by` in-worker** (vs. pre-grouping at materialize time). Worker has the data anyway; per-block grouping is O(N) for the worker's slice and cheap (~ms at 25K rows). Keeps the on-disk shape one-file-per-bucket, simplest.
- **Polars' `.hash(seed=BUCKET_HASH_SEED)`** (vs. Python `hash()` or hashlib). Polars hash is xxHash-based, deterministic given a seed, vectorized. Python `hash()` is non-deterministic across processes (PYTHONHASHSEED). hashlib is slow per-row.
- **Polars hash API:** the implementation uses `pl.col("__block_key__").hash(seed=BUCKET_HASH_SEED)`. Polars ≥ 1.0 accepts a single u64 `seed` kwarg (older 0.x versions split into `seed_1..seed_4`). `pyproject.toml` already pins `polars>=1.0` (verified via Component 1's existing code); no version bump needed. Implementer should sanity-check `pl.col("x").hash(seed=0).dtype == pl.UInt64` once and proceed; if Polars surfaces an API change, fail loudly at materialize time with a clear message.
- **Drop v1 API entirely** (vs. keep both). v1's flags were gated default-off and never landed in any production path. No callers outside the test surface we're rewriting.
- **Kill criterion stays binding.** The override that authorized this redesign was contingent on a falsifiable retry, not on relitigating the threshold. If v2's 5M bench still misses ≥20% wall + RSS, the C1+C2+C3 stack reverts.

---

## Followups (deferred)

- **Worker-side streaming.** `pl.scan_parquet(bucket_path).collect(streaming=True)` would let very-large buckets stream rather than materializing. Out of scope for v2; pertinent only at row counts much larger than 5M / N.
- **Multi-pass blocking.** `pre_scored_pairs` still dropped. Needs Component 4 (streaming pair store).
- **Bucket auto-tuning.** N could adapt to observed block-size distribution. Default heuristic is fine for v2; revisit if the bench surfaces a gap.
- **Multi-node FS.** Still needs shared FS for distributed Ray. Out of scope; documented same as v1. v2 writes a sibling directory tree (`buckets_<sig>/`) next to the DuckDB file, so the shared-FS requirement extends to that subtree as well.
