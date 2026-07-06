# GoldenPipe Stage 0 findings — the handoff is not the bottleneck

**Status:** done (measurement). **Created:** 2026-07-06.
**Feeds:** `2026-07-06-goldenpipe-orchestrator-pivot.md` (Stage 0 gate).

## The question

The orchestrator-pivot roadmap gates pillar 2 (a Rust streaming executor) on a
Stage-0 measurement: **in the single-process pipeline, are the DataFrame handoffs
between stages a real bottleneck?** If they aren't, a streaming Arrow executor
would optimize a non-bottleneck, and pillar 2's value lies elsewhere.

## Method

`benchmarks/stage0_handoff_profile.py` generates a dirty entity table (name /
email / city with typos, casing noise, ~20% near-duplicate rows) and runs the
real auto-config pipeline `goldencheck.scan → goldenflow.transform →
goldenmatch.dedupe`. It reads the Runner's per-stage `ctx.timing`, the total wall,
and isolates the two concrete handoff / re-materialization costs a shared Arrow
buffer would remove:
1. the CSV **re-read** `goldencheck.scan` does (it takes the source *path*, not
   `ctx.df`, so the data is parsed twice), and
2. the full-df **`cast({col: Utf8})`** `goldenmatch.dedupe` does at its boundary.

## Results (median wall)

| | 20,000 rows | 50,000 rows |
|---|--:|--:|
| **TOTAL wall** | 2176 ms | 4806 ms |
| `goldencheck.scan` (compute) | 307 ms · 14.1% | 613 ms · 12.7% |
| `goldenflow.transform` (compute) | 232 ms · 10.6% | 551 ms · 11.5% |
| **`goldenmatch.dedupe` (compute)** | **1626 ms · 74.7%** | **3622 ms · 75.3%** |
| orchestration gap (plan/route/ctx) | 12 ms · 0.5% | 21 ms · 0.4% |
| handoff: CSV re-read | 3.5 ms · 0.2% | 5.7 ms · 0.1% |
| handoff: full-df Utf8 cast | 1.6 ms · 0.1% | 2.2 ms · 0.0% |
| **handoff total** | **5.1 ms · 0.2%** | **7.9 ms · 0.2%** |

## Verdict — do NOT build the single-process streaming executor

The wall is **~99% per-stage kernel compute**, and `goldenmatch.dedupe` alone is
**~75%** of it. The handoff / re-materialization costs a streaming Arrow data
plane would eliminate are **0.2% of the wall** (and *shrinking* as data grows —
the compute is superlinear, the handoff linear). The orchestration gap is 0.4%.

So a Rust streaming executor that made the between-stage handoff free would win
**<1% end-to-end** in the single-process case. That directly disconfirms the
thesis framing for this workload: the cost is not "translating objects across the
boundary" — it's the ER math inside the kernels. This is the same lesson
GoldenCheck's zero-copy FFI work produced: once Arrow's C Data Interface is in
play, the boundary is nearly free; the wall lives in compute.

**Corollary — where the leverage actually is:** the highest-value perf lever for a
pipeline is `goldenmatch.dedupe` (blocking + scoring), which is exactly what the
*other* pillars already target (the `-core` kernels + native acceleration + the
HNSW/LSH blockers). The pipe is already a thin, cheap orchestrator; making it
"smarter" in Rust optimizes the wrong 1%.

## Where pillar 2 *should* aim

The streaming Arrow plane becomes load-bearing only where the handoff stops being
a free in-process reference pass:
- **Out-of-core** — data larger than memory, where stages must stream
  record-batches rather than hold a whole DataFrame (today's design materializes
  the full frame in `ctx.df`).
- **Cross-process / cross-language** — a stage on another engine (DuckDB /
  Postgres / a TS worker), where the handoff becomes real serialization and Arrow
  IPC / the C Data Interface earns its keep.
- **Parallel DAG branches** — independent stages that could run concurrently; the
  current `Runner` is a sequential `for`-loop, but that's a *scheduling* win
  (thread pool over the existing `ExecutionPlan`), not an *Arrow-streaming* win,
  and it's only worth it when branches are both independent and heavy.

Recommended next step is therefore **not** Stage 1/2 of the executor as written,
but to re-scope pillar 2 around one of the above — most likely an **out-of-core /
batched-streaming** target with a measured workload, or the parallel-DAG
scheduler if real pipelines have independent heavy branches. Either should get its
own Stage-0-style baseline before any build.

## Two cheap tidy-ups noted (not worth an executor)

Independent of pillar 2, the profile surfaced two real (if negligible) micro-costs
that a shared buffer would remove and that could be tidied trivially if ever
touched: `goldencheck.scan` re-reading the file instead of consuming `ctx.df`
(~0.2%), and the whole-frame Utf8 cast in `goldenmatch.dedupe` (~0.1%). Left as-is;
they are noise against the dedupe wall.

## Repro

`python packages/python/goldenpipe/benchmarks/stage0_handoff_profile.py --rows 50000`
