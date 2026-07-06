# In-engine dedupe scoring — scope + Stage 0 verdict

**Date:** 2026-07-06
**Status:** SCOPED — **DO NOT BUILD** (Stage 0 measured the ceiling at <1% and shrinking)
**Context:** closes the open caveat from the relocatable-stage contract Phase C
(`2026-07-06-goldenpipe-phasec-baseline-findings.md`): "the dominant
`goldenmatch.dedupe` scoring stage has no in-engine surface, so a full ER pipeline
still crosses for it." This doc asks whether that caveat is a gap worth closing.
**Reproducer:** `packages/python/goldenpipe/benchmarks/stage0_inengine_dedupe_probe.py`

---

## The question

Phase C proved that keeping a cheap stage *in-engine* (DuckDB) pays, because the
DuckDB<->Python crossing was **~89% of the pull path** for a plain projection. The
natural next target is the one stage Phase C could not place in-engine: the dedupe
*scoring* stage. Should we build an in-engine dedupe so a full ER pipeline never has
to leave the warehouse?

The measure-first discipline says: **do not assume it wins.** Phase C's 89% was for a
*compute-trivial* projection. Dedupe is *compute-heavy* (scoring is O(candidate
pairs) of rapidfuzz work). Before designing anything, measure whether the crossing is
even a material fraction of a dedupe.

## What "in-engine dedupe" would actually be

`dedupe_df` (`goldenmatch/_api.py:400` -> `core/pipeline.py:1127`) is a four-stage
machine, not one kernel:

| Stage | Today | In-engine equivalent |
|---|---|---|
| **Blocking** (candidate-pair gen) | Polars `group_by("__block_key__")` (`core/blocker.py:315`) | `GROUP BY` / self-join — already SQL-shaped |
| **Scoring** (fuzzy / Fellegi-Sunter) | rapidfuzz `cdist`; native block-scorer `score_block_pairs_arrow` (`backends/score_buckets.py:778`) over **`score-core`** | scalar UDF per candidate pair |
| **Threshold** | Polars filter | `WHERE score >= t` — pure SQL |
| **Clustering** (connected components) | native `connected_components` (`core/cluster.py:832`) over **`graph-core`** | UDF over the pair list |

The two hard kernels — scoring and clustering — **already have pyo3-free Rust core
crates with Arrow entry points** (`goldenmatch-score-core`, `goldenmatch-graph-core`),
and the `datafusion-udf` + Postgres extensions already link them. So this is not
"rewrite dedupe in Rust." It is "compose kernels that mostly exist into an in-engine
plan." The work splits sharply by surface.

### Surface map (what already runs in-engine vs. new work)

**Postgres (pgrx) is ~80% there** — native-direct, no embedded CPython:
- `goldenmatch_score(a,b,scorer)` for jaro_winkler/levenshtein/token_sort/exact
  (`postgres/src/quick.rs:156`, over `score-core`).
- `goldenmatch_connected_components` + `goldenmatch_pair_dedup`
  (`postgres/src/kernels.rs:202`, over `graph-core`).
- LSH / HNSW blocking already native-direct (`kernels.rs:99` / `:37`).
- Composing these into a block -> score -> threshold -> CC SQL is **mostly wiring,
  near-zero new Rust.**

**DuckDB is greenfield** — there is **no compiled goldenmatch cdylib at all** (unlike
`goldenflow-duckdb`). Every `goldenmatch_*` DuckDB UDF marshals to Python; e.g.
`goldenmatch_dedupe_table` does `cursor.sql(...).pl()` then `dedupe_df` in the Python
process (`extensions/duckdb/goldenmatch_duckdb/functions.py:275`). A zero-Python
in-engine path would be a **new compiled `goldenmatch-duckdb` cdylib** (score-core +
graph-core), 5-platform release, CI parity lane — a real, disproportionate lift.

### The honest boundary (smart pipe / dumb kernels)

Only the *mechanical* dedupe could ever go in-engine. The *smart* parts stay host:
auto-config profiling, Fellegi-Sunter EM training, MST splitting of oversized
clusters (`core/cluster.py:183`), correction memory, `explain`. Those run **once on
samples/summaries, not per-pair**, so they were never the crossing bottleneck. An
in-engine path is therefore a **reference-simplified** dedupe (explicit config only),
NOT byte-identical to `dedupe_df` across every blocking nuance — it would have to be
parity-gated and documented, never silently divergent (the #688 lesson).

### The phased plan (what we WOULD build, if Stage 0 said go)

- **Stage 0** — measure the crossing fraction of a warehouse-resident dedupe. *Gates
  everything.*
- **Phase 1 (Postgres)** — a goldenpipe `EngineDedupeStage` emitting block -> score ->
  threshold -> `connected_components` SQL, reusing the native-direct UDFs. Minimal new
  Rust. Parity-gated vs `dedupe_df` on explicit configs.
- **Phase 2 (DuckDB)** — new compiled `goldenmatch-duckdb` cdylib (the greenfield
  lift), only if Phase 1 wins *and* per-value Python is shown to be the residual cost.
- **Phase 3** — wire it as the in-engine dedupe `RemoteStage`, keeping auto-config /
  FS / MST host-side.

---

## Stage 0 measurement — the ceiling on any in-engine-dedupe win

The compute (block + score + cluster) runs at the **same speed** whether the kernel is
called from Python-over-Arrow or from an in-engine UDF — both are `score-core` /
`graph-core` underneath. So the ONLY thing an in-engine dedupe can save is the
**crossing**: pulling the warehouse table into Python (ingress) and pushing the result
back (egress). Therefore:

> `crossing_fraction = (ingress + egress) / total`  **is the CEILING on any
> in-engine-dedupe speedup** for a warehouse-resident dedupe.

Explicit config (auto-config excluded — it is host-side "smart pipe" either way), data
originating in a DuckDB table and written back to one, median of fresh runs
(`goldenmatch 2.8.0`, `duckdb 1.5.4`, 760 surnames so blocking on `last_name` yields
bounded block sizes):

| rows | ingress | compute | egress | total | **crossing %** |
|---:|---:|---:|---:|---:|---:|
| 10,000 | 3.2 ms | 1356 ms | 5.5 ms | 1365 ms | **0.64 %** |
| 40,000 | 9.0 ms | 3549 ms | 9.2 ms | 3568 ms | **0.51 %** |
| 100,000 | 20.1 ms | 7643 ms | 10.9 ms | 7674 ms | **0.40 %** |

## Verdict — DO NOT BUILD

The crossing is **0.4–0.64 % of the dedupe wall, and shrinking** as data grows
(compute is superlinear O(pairs); the crossing is linear O(rows), so the ratio decays).
Against Phase C's **89 %** for a plain projection, this is the opposite regime: the
rapidfuzz scoring compute utterly dominates, and the DuckDB<->Python crossing is
rounding error.

An in-engine dedupe could save **at most ~0.5 % of wall, less at scale** — and because
the in-engine kernels *are* the same `score-core` / `graph-core` the host path already
calls, there is no compute speedup on top. Building it (a compiled `goldenmatch-duckdb`
cdylib + Postgres SQL composition + reference-simplified parity gates) would be a large,
high-maintenance surface for a sub-1 % win. **The Phase C v2 caveat is not a gap to
close; it is correct by construction.** The right architecture is exactly "smart pipe,
dumb kernels": dedupe stays a host stage that calls native kernels, and the ~0.5 %
crossing it pays when it sits among in-engine stages is negligible and paid once.

This is the fourth measure-first "don't" (Stage 0 handoff 0.2 %, Phase B frame a
rounding error, in-engine dedupe crossing 0.4 %) against Phase C's one "do" — the
discipline earning its keep: three baselines said don't, and we did not build them.

### When to revisit

Only if a future workload inverts the ratio — a *cheap* dedupe (tiny blocks, exact/
hash scoring, no fuzzy) sitting inside a long chain of OTHER in-engine stages, where
forcing it through Python would break engine-residency and the crossing compounds
across stages. That is not entity resolution's cost shape. Re-run the probe against the
real workload before reconsidering; do not build on the assumption.
