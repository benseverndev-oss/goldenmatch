# FS frame-residency — bounded bucket streaming + out-of-core (DuckDB) block source

**Epic:** `docs/superpowers/plans/2026-07-18-fs-rust-arrow-only.md` — a new memory
axis alongside PR-A…D. **Branch:** `claude/benchmark-failure-gh-7h5ryr`.

> **⚠ MEASUREMENT CORRECTION (2026-07-20, post-commit).** Follow-up profiling
> prompted by "how does the non-probabilistic path handle this?" invalidated this
> spec's central premise. **The dominant FS peak is NOT `score_buckets` frame
> residency — it is `build_blocks` for EM training on the full frame.** Evidence
> (person 100K, `scripts/bench_fs_vmrss_probe.py` + `bench_fs_peak_probe.py`):
> - VmRSS jumps 187 MB → 2084 MB **between `auto_configure` and EM training**, i.e.
>   entirely UPSTREAM of `score_buckets`. At `score_buckets` entry the process is
>   already at 2084 MB (weighted path: 398 MB).
> - Forcing `GOLDENMATCH_FS_EM_SAMPLE_ROWS=10000` drops the 100K peak **2096 MB →
>   715 MB** — so ~1400 MB was EM's full-frame `build_blocks`.
> - The `score_buckets` slim-projection + `partition_by` machinery (this spec's
>   target) is SHARED with the weighted path, which peaks at 467 MB — so it is the
>   *secondary* term (a few hundred MB), not the dominant one.
>
> The de-risking prototype under-measured because it replays a clean parquet-read
> frame through `score_buckets` only, skipping the EM `build_blocks` that actually
> holds the peak. **The existing `GOLDENMATCH_FS_EM_SAMPLE_ROWS` cap (default 100K)
> is already the mitigation for the dominant term at ≥1M** (at ≤100K the cap does
> not bite: sample == full frame, which is why the local probe saw the full spike).
>
> **Status: DO NOT IMPLEMENT as written.** The frame-residency + DuckDB-source idea
> may still matter at ≥1M (where the EM cap is active and `score_buckets` residency
> scales with N), but that requires a 1M-regime per-stage RSS split to confirm the
> term breakdown before committing. The byte-parity result (FS-over-DuckDB ==
> resident, 23,413 pairs) and the harness stand; the *targeting* was wrong. Revise
> against the 1M split before promoting any PR. See the correction thread in the
> session for the full measurement.

**Trigger:** with the Arrow pair-stream fix already landed (`398006b` / #1896:
"cut the FS memory peak — Arrow pair stream, EM-block-sample") the FS peak RSS is
no longer the pair stream. Stage-attributed measurement on HEAD (`0ad8f02`, which
includes #1896) shows the **remaining** peak is *frame residency*: the
`bucket_slim_projection` `.select()` that consolidates the `__xform_*` chunks into
one contiguous slim frame, **plus the 20 eager `partition_by` frames**, all held
live through `bucket_score`. This is a *parallelism* cost, not a scoring cost.

## Measured evidence (de-risking prototype)

Harness: `scripts/bench_fs_streaming_prototype.py`. It captures the EXACT blocks
the real pipeline hands the native FS kernel (`score_probabilistic_bucket_native`
→ `score_block_pairs_fs_arrow`) — the per-bucket row order + `size_list` — plus
the prepared frame and trained `em_result`/`mk`, then replays the *identical*
blocks through the *identical* kernel two ways, each in its own process for a
clean peak-RSS read.

Person fixture, FS lane env (`GOLDENMATCH_FS_NATIVE=1`, posterior, SN bound):

| path | pairs | peak RSS 100K | peak RSS 200K |
|---|---|---|---|
| **Real FS pipeline** (clean stage probe) | — | **2096 MB** | **2841 MB** |
| block-streamed, **resident** source | 23413 | **261 MB** | **490 MB** |
| block-streamed, **DuckDB** source | 23413 | **366 MB** | — |

Two conclusions, both load-bearing:

1. **Parity is exact.** FS scoring over DuckDB-sourced blocks is byte-identical to
   the resident path — 23,413 pairs, `resident == duckdb`, zero diff (100K). The
   out-of-core FS bet is correctness-proven before any pipeline change.
2. **The peak is frame materialization, not scoring.** Both streamed replays run
   ~6–8× lighter than the real pipeline. Scoring block-at-a-time is intrinsically
   bounded by *bucket size*, not *N*. The real path's 2 GB is `.select()`
   consolidation + 20 resident partitions, materialized to feed the 20-way
   `ThreadPoolExecutor`.

Trade-off surfaced: sequential streaming loses that parallelism (100K replay 92 s
vs real `bucket_score` 29 s on 4 cores) — so the design must preserve parallelism
with a *bounded* resident working set, not drop it.

## Why this is a distinct axis (not covered by PR-B/C/D)

- PR-B retired the `list[tuple]` pair stream → Arrow (16 GB → 1.3 GB at 66M pairs).
  **Landed.** Different structure (pairs, not frames).
- PR-C moves EM training to Rust/Arrow. Different stage (train, not score).
- PR-D moves *candidate generation* off polars. Adjacent, but its concern is
  block *enumeration*, not the *residency* of the scored frame + partitions.

The frame-residency peak survives all three. At 1M it is the dominant remaining
term (CI: ~5.3 GB with auto-config; ~3.4 GB/M is the slim-frame + partitions).

## Target design

Replace "materialize slim frame → `partition_by` into `n_buckets` eager frames →
`ThreadPoolExecutor` over the frames" with **bounded bucket streaming**:

1. **Bucket iterator, not partition list.** Assign each row its bucket id + the
   `__block_key__` exactly as today (`bucket_hash_modulo` + block-key expr — no
   change to *which* rows land in which bucket, so parity holds). But instead of
   `partition_by` materializing all `n_buckets` frames up front, yield buckets
   **one at a time** from a lazy source.
2. **Block-source abstraction** (`FsBlockSource`): `iter_buckets() -> Iterator[
   pa.Table]`, each already block-key-sorted, with its `size_list`. Two impls:
   - `FrameBlockSource` (default, below RAM): holds the slim frame, slices one
     bucket's rows on demand (`filter` on bucket id) and drops the slice after
     scoring. Working set ≈ one bucket, not the frame + 20 partitions. This alone
     kills the peak below RAM.
   - `DuckDBBlockSource` (opt-in, above RAM): prepared records live in a DuckDB
     table (Arrow-loaded, `__bucket__`/`__block_key__` indexed); each bucket is a
     `SELECT … WHERE __bucket__ = k ORDER BY __block_key__` returning an **Arrow**
     batch handed straight to `score_block_pairs_fs_arrow` (no polars round-trip —
     respects the Arrow-native directive). The prepared frame is never resident in
     the driver heap.
3. **Bounded parallel pool.** Keep the ThreadPoolExecutor, but bound *in-flight
   buckets* to `max_workers` (the kernel releases the GIL, so this is real
   parallelism) instead of pre-materializing all `n_buckets`. Resident set =
   `max_workers` buckets, not `n_buckets` frames + consolidated slim frame.
   Prefetch depth = pool size keeps workers fed without unbounding memory.

Peak RSS goes from `O(N)` (slim frame + all partitions) to `O(max_workers ×
mean_bucket_rows)` — flat in N for a fixed pool + bucket count.

## Block-source selection

`resolve_fs_block_source(n_rows, config)` mirroring `resolve_base_store_kind`:
env `GOLDENMATCH_FS_BLOCK_SOURCE ∈ {auto, frame, duckdb}`, default `auto` →
`duckdb` only above a measured floor (prepared-frame bytes projected to exceed a
fraction of `RuntimeProfile` RAM) AND duckdb importable; else `frame`. Below the
floor, `frame` wins (the prototype shows DuckDB adds ~100 MB with no benefit until
the frame doesn't fit). No new Pydantic field — env-only v1, matching the
CandidateStore precedent.

## Scope boundaries

- **FS bucket route only** (`score_buckets` / the `_fs_use_bucket_route` path).
  The weighted/fuzzy lane is untouched (its own `backend="duckdb"` stays as is).
- **Parity is the hard gate.** Same buckets + same kernel ⇒ byte-identical pair
  stream. The prototype is promoted to a CI parity check (frame vs duckdb source).
- DuckDB source is the out-of-core lever for the ≥ RAM tier; it does **not**
  replace the in-RAM path, it extends past it.

## Risks & mitigations

- **OOM history.** `chunked` hung at 62.99 GB; the old weighted `duckdb-backend`
  "still OOMs". Both moved the wrong thing (pair storage / whole-frame). This moves
  the *scored frame residency*, which the prototype shows is the actual peak.
  Mitigation: land behind a default-off flag; the scale-envelope bench is the
  binding gate before any default flip.
- **Wall regression from serialization.** The bounded pool must keep the kernel
  saturated; a too-small prefetch starves workers. Mitigation: prefetch = pool
  size; bench `bucket_score` wall must stay within noise of the partitioned path
  on the ≤ RAM tier (where both fit) before shipping.
- **glibc arena fragmentation** (the #688-adjacent RSS-climb class) is *reduced*
  by not allocating `n_buckets` eager frames, but per-bucket slice churn must be
  watched; `GOLDENMATCH_BUCKET_DEBUG` timing split extends to per-bucket residency.

## Validation / gates

- **Parity (blocking):** promote `scripts/bench_fs_streaming_prototype.py` into a
  CI check — frame-source and duckdb-source pair streams must be byte-identical to
  the current partitioned `score_buckets` output on person + biblio fixtures.
- **`bench-probabilistic` panel** F1 parity (unchanged — parity implies it).
- **`bench-er-headtohead`** person + biblio, 1M native + a 5M FS tier: peak RSS
  must drop materially and stay bounded as N grows (the whole point); wall within
  noise on 1M.
- Not in `ci-required` (FS scale gates are out-of-band); confirm before auto-merge.

## Rollout

Default-off env flag (`GOLDENMATCH_FS_BLOCK_SOURCE=frame` initially keeps today's
partitioned path via a compatibility shim, `auto`/`duckdb` opt in). Prove peak-RSS
drop + wall-neutral on the bench, then flip `auto` on. DuckDB source ships behind
the same flag; `frame` streaming can flip first (smaller blast radius) with the
`duckdb` source following once the ≥ RAM tier is bench-validated.

## Sequenced PRs

- **PR-1** — `FsBlockSource` + `FrameBlockSource` + bounded-pool streaming in
  `score_buckets`; default-off. Gate: parity check + `bucket_score` wall-neutral +
  peak-RSS drop on 1M person.
- **PR-2** — `DuckDBBlockSource` (Arrow-in/Arrow-out) + `resolve_fs_block_source`
  auto floor. Gate: byte-parity frame-vs-duckdb + 5M FS tier bounded RSS.
- **PR-3** — flip `auto` on after two green scale-envelope runs; document in
  `docs/scale-envelope.md`.
