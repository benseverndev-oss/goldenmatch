# DataFusion backend spike — design

**Date:** 2026-05-30
**Status:** spike scoping (not committed)
**Author:** assistant
**Lane:** strategic — evaluate DataFusion as a third backend alongside
`polars-direct` and `bucket`. Pivoting away from Ray-as-load-bearing
(see prior session context on Distributed Plan v1 soft-revert + 25M
bucket landing at 6.5min/57GB).

## Goal

Decide whether `backend="datafusion"` is worth building as a
production backend. Output: one bench number per shape (10K, 100K, 1M)
comparing DataFusion vs bucket on wall, peak RSS, and result-set
parity, plus a written go/no-go recommendation.

Explicitly NOT in scope for the spike:
- Distributed DataFusion (Ballista / Sail). Single-node only.
- Replacing the planner. New backend is opt-in via explicit
  `config.backend="datafusion"`.
- Wiring DataFusion into the controller's runtime profile signals.
- Full scorer coverage. Spike covers ONE scorer (jaro_winkler) end to
  end; if it wins, we extend.

## Strategic context

GoldenMatch's current scale story:
- 25M on one box at 6.5min / 57GB peak (bucket + native, PR #526)
- Ray path soft-reverted, currently slower than bucket below ~50M
- Splink ships DuckDB as their popular backend; we don't have a
  comparable engine-class backend (polars-direct + bucket are tightly
  coupled to our pipeline shape, not a general query engine)

DataFusion gives us:
- Cost-based query planning (bucket has none)
- Arrow-native, zero JVM, zero shuffle (vs Spark)
- Native Rust UDF interface (vs Python scorer cost in
  polars-direct/bucket)
- Apache top-level project = credible institutional backing for the
  "we go to scale" pitch

The bet: at 1M+ scale, DataFusion's planner + Rust UDFs beat
bucket because (a) the planner picks the right join strategy per
block-size distribution, (b) Rust scorers eliminate the per-cdist
Python boundary cost that bucket pays in its inner loop.

The risk: DataFusion's UDF dispatch overhead plus the per-block
DataFrame construction cost swamps the win at small N. We need to
measure, not theorize.

## Path A vs Path B

**Path A** (rejected): DataFusion engine, Python UDF scorers via
`datafusion-python`'s Python UDF interface. Easy to wire, but pays
Python⇄Arrow boundary cost on every score call, defeating the entire
performance premise. Spike result would be uninterpretable: if it
loses, we can't tell whether DataFusion is bad or Python UDFs are
bad.

**Path B** (this spike): DataFusion engine, native scorers in the inner
loop. Two implementation tiers:

- **B1 — vectorized Python UDF wrapping existing pyo3 native scorer.**
  DataFusion calls the UDF once per record batch (~8K rows), passing
  Arrow arrays. The UDF converts Arrow→numpy zero-copy and calls
  `goldenmatch._native.jaro_winkler_similarity` (already exists). Python
  overhead is one-call-per-batch, amortized over thousands of rows. Get
  this running first.
- **B2 — true Rust UDF via datafusion-python's Rust FFI.** Only if B1
  is competitive but the per-batch Python boundary is measurably
  significant in cProfile. Pushes the spike toward the 5-day end.

B1 is NOT Path A. Path A is per-row Python (UDF called billions of
times, each call ~10µs of Python overhead). B1 is per-batch Python
(UDF called thousands of times, each call processing thousands of rows
in native code). The overhead delta is 1000-10000x.

## Architecture

```
+--------------------------------+
| pipeline.py                    |
|   _get_block_scorer(config)    |
|     case "datafusion":         |
|       return                   |
|       score_blocks_datafusion  |
+--------------------------------+
              |
              v
+--------------------------------+
| goldenmatch/backends/          |
|   datafusion_backend.py        |
|                                |
|   score_blocks_datafusion(     |
|     blocks: list[Block],       |
|     matchkey: MatchkeyConfig,  |
|     ...                        |
|   ) -> list[Pair]:             |
|     ctx = SessionContext()     |
|     register_native_udfs(ctx)  |
|     for block in blocks:       |
|       df = ctx.from_arrow(...)  |
|       result = df               |
|         .join(df, "block_key") |
|         .filter(id_a < id_b)    |
|         .select(                |
|           score_udf(a, b)       |
|         )                       |
|         .filter(score >= thr)   |
|         .collect()              |
|     return pairs                |
+--------------------------------+
              |
              v
+--------------------------------+
| packages/rust/extensions/      |
|   native/src/datafusion_udfs.rs|
|                                |
|   #[pyfunction]                |
|   fn register_native_udfs(     |
|     ctx: SessionContext        |
|   ) -> PyResult<()> {          |
|     ctx.register_udf(          |
|       jaro_winkler_udf()       |
|     );                         |
|     ctx.register_udf(          |
|       token_sort_udf()         |
|     );                         |
|   }                            |
+--------------------------------+
```

## Concrete deliverables

1. **`packages/python/goldenmatch/pyproject.toml`** — add
   `[project.optional-dependencies] datafusion = ["datafusion>=44"]`.

2. **`packages/rust/extensions/native/src/datafusion_udfs.rs`** — new
   module. Wraps existing jaro_winkler scorer as a DataFusion scalar
   UDF (Utf8 a, Utf8 b -> Float64). Registered on a SessionContext
   passed in from Python. Reuses the existing
   `score_field_matrix` kernel internals where possible.

3. **`packages/python/goldenmatch/goldenmatch/backends/datafusion_backend.py`**
   — new module. Defines `score_blocks_datafusion(blocks, matchkey,
   matched_pairs, ...)` matching the signature shape of
   `score_blocks_parallel` and `score_blocks_ray` exactly.

4. **`packages/python/goldenmatch/goldenmatch/core/pipeline.py`** —
   extend `_get_block_scorer(config)` to dispatch
   `config.backend == "datafusion"` to the new function. Lazy import
   to keep the extra truly optional (see CLAUDE.md note on `[web]`
   precedent).

5. **`packages/python/goldenmatch/scripts/bench_datafusion_vs_bucket.py`**
   — bench harness. Shapes: 10K, 100K, 1M. Same input frame, same
   matchkey config (single jaro_winkler scorer to start, since that's
   all we'll have wired). Outputs JSON with wall, RSS, output pair
   count, result-set Jaccard vs bucket reference.

6. **`packages/python/goldenmatch/tests/backends/test_datafusion_backend.py`**
   — minimal parity test. Same input, both backends, assert the
   produced pair set is identical (modulo ordering).

## Bench design

Inputs at each shape (10K, 100K, 1M):
- Synthetic person fixture from existing `_person_df(n)` helper in
  `tests/test_autoconfig_regressions.py`
- Single matchkey: weighted with one field
  `(name, scorer=jaro_winkler, threshold=0.85, weight=1.0)`
- Blocking: name-based (existing default)

Measurements per (shape, backend):
- Wall: median of 3 runs
- Peak RSS: psutil maxRSS over the run
- Pairs emitted: count
- Parity: Jaccard(pairs_datafusion, pairs_bucket) — must be 1.0 or we
  have a correctness bug to fix before publishing numbers

Output: markdown table at the end of the spike doc.

## Go / no-go gate

DataFusion advances to production-backend status if AT LEAST ONE of:

- **Wall:** >= 1.3x faster than bucket at 1M
- **RSS:** >= 20% lower peak RSS than bucket at 1M
- **Architectural:** parity at 1M with materially simpler code path
  that lets us drop bucket's per-block Python orchestration

If none of these are met at 1M, write up the lesson and shelve
DataFusion. No second spike unless something material changes
(e.g., DataFusion ships a major perf rev, or a customer constraint
forces an engine-backend rewrite).

## Day-by-day plan (3-5 day estimate)

- **Day 1:** wire the optional extra + Rust UDF skeleton + lazy
  registration. Get one block scored end to end via DataFusion in a
  REPL, no benchmarking yet.
- **Day 2:** flesh out `score_blocks_datafusion`, parity test against
  bucket on a 1K fixture. Iterate until Jaccard == 1.0.
- **Day 3:** bench harness, 10K/100K runs, observe shape. If 100K is
  >2x slower than bucket, stop and triage; if competitive, continue.
- **Day 4:** 1M bench, debug if slow. Likely culprits: per-block
  SessionContext recreation, Arrow conversion in inner loop, UDF
  dispatch overhead.
- **Day 5:** write up the result, update this doc with measurements,
  produce the go/no-go recommendation.

## Risks and open questions

1. **`datafusion-python`'s Rust UDF FFI quality.** Documentation is
   thin. May need to drop to `datafusion` crate directly and re-bind,
   which would push the spike toward 5 days.

2. **Per-block SessionContext cost.** DataFusion isn't designed for
   thousands of micro-queries; it expects fewer, larger queries. If
   we create one SessionContext per block (we have ~1.67M blocks at
   5M scale per CLAUDE.md), that overhead will dominate. The right
   shape may be to load all blocks at once and run a single query
   with `groupby(block_key)`. That's a bigger architectural shift
   than "per-block driver" but it's the shape DataFusion wants.

3. **Backend invariants.** The existing scorer functions return
   `list[tuple[int, int, float]]` (canonicalized). DataFusion's
   output is an Arrow table; we'll convert, but the conversion is
   non-trivial at 1M+ pairs and may eat the win.

4. **Pair canonicalization.** Bucket emits `(min, max)` pairs (per
   CLAUDE.md). The DataFusion query needs to enforce this with a
   `WHERE id_a < id_b` filter. Drop-or-keep semantics on equality
   need to match bucket exactly.

## What this spike does NOT prove

- That DataFusion-distributed (Ballista / Sail) is viable. Wholly
  separate question.
- That DataFusion beats bucket at 25M+. We're not testing past 1M.
  If 1M shows promise we'd do a follow-up at 5M/25M before commit.
- That the controller can pick DataFusion intelligently. Manual
  opt-in only via `config.backend="datafusion"`.

## Decision artifact

At spike completion, update the "## Go / no-go gate" section above
with the measured numbers and a one-paragraph recommendation. That
becomes the input to deciding whether to invest the multi-week effort
to make DataFusion a real production backend.
