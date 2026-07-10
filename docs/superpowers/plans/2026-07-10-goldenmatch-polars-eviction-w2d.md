# GoldenMatch Polars eviction — W2d plan (blocker static path + ingest Frame boundary)

Parent: `2026-07-10-goldenmatch-polars-eviction-w2.md`. Predecessors: W0 #1616,
W1 #1622, W2a #1632, W2b #1633, W2c #1642 (in flight).

## Recon verdict that reshapes W2d (2026-07-10)

Every pipeline stage between ingest and the engine's collect is
Polars-expression-bound and W2e-scoped (`_add_row_ids`, `_cast_user_cols_to_str`,
`apply_standardization`, domain extraction, `compute_matchkeys`,
`precompute_matchkey_transforms` — pipeline.py:715-1751). Therefore:

- **Arrow cannot flow past ingest until W2e**, no matter what `load_file`
  returns. W2d's arrow lane keeps converting to Polars right after load —
  but the conversion moves from being BURIED in load_file:116 to an EXPLICIT
  shim at the pipeline ingest boundary.
- **The parent plan's W2d both-backends 1M gate moves to W2e** (annotated in
  the parent in this PR): an arrow 1M e2e is impossible before the expression
  stages port. W2d's gates: default-backend parity (all blocker tests
  unedited) + the bench workflow gains the `GOLDENMATCH_FRAME` input so W2e
  CAN run the both-backends gate.
- **`BlockResult.df` keeps its `pl.LazyFrame` typing** (ClusterFrames
  precedent): the arrow lane can't reach the blocker until W2e, so retyping
  the dataclass now would churn every scorer consumer (`block.df.collect()`)
  for zero e2e gain. Blocker internals wrap-at-use; the dataclass port rides
  with W2e when arrow actually arrives there.

## Scope (reviewer findings folded)

1. **New seam ops** (fixtures-first, delegation parity):
   - `Frame.with_column(name, col: Column)` — attach a derived Column
     (blocker.py:359-361, :710-712). Polars: `with_columns(series.alias(name))`;
     Arrow: `append_column`/`set_column`.
   - `Frame.with_literal_column(name, value)` — `with_columns(pl.lit(v).alias(n))`
     (ingest.py:191 `__source__` tag).
   - `Frame.group_partitions(key)` — HASH-grouped iteration, first-appearance
     order, NO pre-sort requirement (reviewer BLOCKER-class finding: W2b's
     `partition_by_key` slices adjacent runs and would silently SPLIT a block
     whose key recurs non-adjacently on unsorted input — a lost-pairs bug the
     polars-side tests can never catch). Polars impl:
     `partition_by(key, maintain_order=True)` (a deterministic refinement of
     the raw nondeterministic `group_by` iteration — legal because blocks are
     an unordered set downstream: thread-pool scored, pairs canonicalized).
     Arrow impl: first-appearance key->indices dict + `take` (O(n), no sort).
     Null keys form a group (callers that skip nulls do so explicitly, as
     `_auto_split_block`'s in-loop `None` skip already does).
     `partition_by_key` keeps its pre-sorted contract for the golden call site.
   - EXECUTION AMENDMENT: `group_len_sizes` is DEFERRED to W2e along with
     `_fast_static_block_sizes` (it is a lazy-Polars measure-only perf lever;
     the seam is eager and forcing a collect would regress the exact wall the
     fast path exists to save). Likewise `_build_static_blocks`' lazy
     `with_columns+filter+collect` prefix stays RAW POLARS: the fused lazy
     pipeline is RSS-load-bearing (#375 -- the eager filter-after-collect
     form peaked ~2x frame size), and build_blocks is also called on
     possibly-still-lazy frames (pipeline.py:1058/1103). W2d ports the
     POST-COLLECT surface only: group iteration + auto-split internals.
2. **Blocker static path port** (`_build_static_blocks` post-collect +
   `_auto_split_block`): group-iterate via `group_partitions` (NOT
   partition_by_key); auto-split's cast-key attach via `derive_block_key`(no
   transforms)/`with_column` + `group_partitions`, n_unique/drop via seam. Blocker.py:377's in-loop `key_str is None: continue` is
   dead after the sentinel filter (reviewer-verified) — keep it as a guard,
   don't rely on it. The lazy->eager collect at :369 stays caller-side;
   BlockResult construction (`group_df.lazy()`) unchanged.
3. **Ingest Frame boundary** (reviewer fix — no PolarsFrame(lazy), ever):
   `load_file` gains `return_frame: bool = False`. Default False = today's
   `pl.LazyFrame`, zero churn for every existing call site (incl. the
   pipeline-internal ones at pipeline.py:58/3256/3279, which stay deferred
   and documented). With `return_frame=True`, ONLY the arrow-eligible route
   changes: it returns `ArrowFrame(tbl)` instead of burying
   `pl.from_arrow(tbl).lazy()`; every other route still returns the
   `pl.LazyFrame` unchanged (the seam's PolarsFrame is eager-only by
   contract — a lazy wrap is forbidden). The pipeline ingest loop
   (pipeline.py:803-812) opts in and shims `ArrowFrame ->
   pl.from_arrow(native).lazy()` IMMEDIATELY after load, before
   apply_column_map/validate_columns (which therefore keep receiving
   LazyFrames untouched), with a comment naming W2e as the shim's removal
   point. `load_files` gets the same pass-through kwarg (default False; its
   LazyFrame-asserting consumers/tests unchanged); its `__source__` tag uses
   `with_literal_column` only on the Frame branch.
4. **bench-zero-config.yml**: `frame` workflow_dispatch input -> exports
   `GOLDENMATCH_FRAME` via the existing backend-override step pattern
   (lines 100-102 template). HONESTY NOTE (reviewer): the bench script
   ingests via `pl.read_csv` + `dedupe_df`, so this input alone does NOT
   exercise the ingest boundary — W2e pairs it with a bench-script
   `load_file(return_frame=True)` path; until then the input only flips the
   in-pipeline `resolve_frame_backend()` readers. Stated in the PR.
5. Parent-plan annotations: gate move to W2e; BlockResult.df retyping
   deferred to W2e; "load_file returns a Frame" delivered as the opt-in
   kwarg (third deviation, reviewer-required).

## Tests / gates

- All blocker tests unedited + green: test_blocker.py,
  test_measure_blocking_fast_path.py (the fast-path byte-parity gate),
  test_hot_block_split.py, test_ann_subblock.py.
- Ingest: test_ingest.py, test_column_map.py, test_validate.py,
  test_smart_ingest.py unedited; NEW tests for `return_frame=True` both
  backends + the pipeline shim.
- New op fixtures in test_frame_relational_ops.py (both backends).
- Differential harness green (it drives the full pipeline through the
  ported blocker on both GOLDENMATCH_FRAME values).
- throughput-gate green; no wall gate needed (polars delegation identical;
  the W2c 1M covers the wrapper-overhead question for the same wrap pattern).

## Risks

| Risk | Mitigation |
| --- | --- |
| group_partitions first-appearance order differs from raw group_by's nondeterministic iteration (block ORDER changes; matched_pairs insertion order / log lines shift) | Blocks are an unordered set downstream (thread-pool scored, pairs canonicalized); unedited blocker suites + differential harness prove output equivalence. A deterministic order is a strict improvement |
| group_partitions arrow impl (python dict grouping) slower than polars hash group_by on huge frames | Arrow lane can't reach the blocker until W2e (recon verdict) — revisit with W2e's both-backends bench; >10% -> pc-based grouping or kernel per spec 4.3 |
| `return_frame` default-False forgotten at a new call site | The parameter is additive; external callers keep LazyFrame behavior by default (no breakage possible) |
| group_len_sizes semantic op hides a perf-relevant lazy pushdown (the fast path is measure-only but runs pre-blocking at scale) | PolarsFrame impl keeps the exact lazy `group_by(expr).agg(len)` pipeline; fixture asserts identical counts |
