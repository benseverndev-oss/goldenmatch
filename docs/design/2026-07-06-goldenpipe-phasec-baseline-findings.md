# GoldenPipe Phase C baseline — the engine boundary is real; in-engine wins

**Status:** done (measurement). **Created:** 2026-07-06.
**Gates:** Phase C of `2026-07-06-goldenpipe-relocatable-stage-contract.md`.

## The question

Phase C is a stage relocated to another engine (DuckDB / Postgres / a TS worker)
instead of in-process Python. Unlike the in-process handoff (**Stage 0**: 0.2% of
the wall) and out-of-core streaming (**Phase B**: the input frame is 200–300×
smaller than peak RSS), the **engine boundary** is where the handoff should become
a *real* cost: pulling a table out of DuckDB into Python and pushing the result
back is a full serialize + materialize. Does keeping a stage **in-engine** beat
that round-trip for engine-resident data?

## Method

`benchmarks/phasec_engine_boundary.py`, one **fresh process per mode/size**. A
DuckDB-resident table; one representative transform (normalize email =
`lower(trim(email))`) run three ways: `inengine` (`CREATE TABLE … SELECT`, data
never leaves), `pull` (`.pl()` → Polars transform → reinsert), and `crossing`
(just the `.pl()` extract + reinsert, to size the boundary).

## Results

| rows | in-engine | pull → Python | crossing (extract + reinsert) | transform only |
|--:|--:|--:|--:|--:|
| 1,000,000 | 170 ms | 236 ms | 214 ms (97 + 117) | 35 ms |
| 5,000,000 | **569 ms** | **1022 ms** | **906 ms (89% of pull)** | 116 ms |

- **The engine boundary is the cost.** At 5M rows the crossing is **89% of the
  pull path**; the actual transform is **11%**. The crossing scales linearly with
  data (~190 ms/M).
- **In-engine is ~1.4–1.8× faster**, and the gap **grows with data** (1.4× at 1M →
  1.8× at 5M), because in-engine skips the crossing entirely.
- Peak RSS was similar here (both hold the table in in-memory DuckDB); for a
  larger-than-memory / on-disk warehouse table, in-engine would also win on memory
  (DuckDB spills; a Python materialization does not).

## Verdict — Phase C is justified; build it

Unlike Stage 0 and Phase B, **this handoff is load-bearing** (89% of the pull path
for a cheap stage, scaling with data). For **engine-resident data**, running a
stage in-engine and keeping the data there is a real, growing win. This is the
first pillar-2 phase the measurement supports building — and it validates the
`location="remote"` seam from Phase A against a real engine.

**Where the win applies (be precise):**
- **Stages that have an in-engine surface** — the transform (`goldenflow-duckdb`),
  profiling (`goldencheck_*` P5 UDFs), and blocking (`goldenmatch_hnsw_pairs` /
  `_lsh_pairs`) already run in DuckDB/Postgres from the cross-surface work. Those
  can be `location="remote"` and skip the crossing.
- **Warehouse-resident pipelines** — when the data *originates* in and *lands* back
  in the engine, `pull` pays both extract **and** reinsert (the full 906 ms at 5M);
  in-engine pays neither. This is the strongest case.
- **Caveat — the dominant stage still crosses.** `goldenmatch.dedupe`'s scoring
  (blocking + Fellegi-Sunter) has no full in-engine surface (only the blockers do),
  so a full dedupe pipeline still pulls to Python (or the native kernel) for that
  stage. Phase C's win is real for every *other* stage and for keeping data
  in-engine *between* them — it does not (yet) make the whole ER pipeline in-engine.

## Recommended build — the `RemoteStage` (DuckDB first)

Implement Phase C behind the Phase-A seam:
1. A `RemoteStage` adapter that, for a `location="remote"` stage over
   engine-resident data, runs the stage's work as SQL/UDF in the engine (via the
   already-shipped `goldenflow_*` / `goldencheck_*` surfaces) and leaves the result
   in-engine — using `ctx.frame.arrow_batches()` / `from_arrow()` only at the true
   ingress/egress of the pipeline, not between in-engine stages.
2. The Runner's existing `location` dispatch (Phase A) routes to it; the
   `ExecutionPlan` is unchanged.
3. Byte-identical output vs the local stage (pillar-4 discipline) before the local
   path is considered replaceable for that stage.

Start with **one** relocated stage (the transform, into DuckDB) end-to-end, its own
before/after on a warehouse-resident table, then widen.

## Repro

`python packages/python/goldenpipe/benchmarks/phasec_engine_boundary.py --rows 5000000 --mode {inengine,pull,crossing}`
(each mode in a fresh process).
