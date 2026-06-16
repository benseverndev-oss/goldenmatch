# 0017 — dbt-goldensuite parity program (P0–P4)

**Status:** accepted (2026-06-16) • PRs #1020, #1022, #1024, #1027, #1028

## Context
`dbt-goldensuite` had drifted: frozen at the 2026-05-24 `#464` scope, orphaned from CI
(the dbt lane was deleted in #464 and never re-pointed at the merged package), nested under
`packages/python/goldenmatch/` (outside the workspace glob + CI matrix), and far behind the
suite's breadth (no probabilistic, no match, no ER-quality gate). The goal: align the dbt
surface in quality and breadth with the rest of the suite. Decomposed into P0–P5; P0–P4 shipped.

## Decision
1. **Relocate + un-rot (P0, #1020).** Move the package to the documented top-level
   `packages/dbt/goldensuite/`; restore the CI `dbt` lane (`dbt parse` + the package pytest);
   sync versions; fix the README's impossible `pip install` to the monorepo git-subdirectory
   install. `Private :: Do Not Upload` is intentional — it's a monorepo-internal package.
2. **Plan/execute, parameterized (P1, #1022).** Generalize `goldenmatch_autoconfig(table)` →
   `autoconfig(table, mode)` (`'standard'` | `'probabilistic'` → `auto_configure_probabilistic_df`);
   the materialization nests it as a scalar subquery into the untouched `dedupe_*` execute UDFs
   (`dedupe_full(staging, (SELECT autoconfig(staging,'probabilistic')))`). Config is the only
   interface; new auto-config modes are enum values, never new functions. This keeps the warehouse
   surface flat as breadth grows.
3. **Pipeline UDFs stay JSON over the CPython bridge.** `dedupe`/`match`/`autoconfig` invoke the
   Python ER pipeline via `bridge/src/api.rs`, which marshals JSON. The native-direct kernels
   (graph/fingerprint/embed, #509 / [0005](0005-sql-native-direct-udfs.md)) went pyo3-free + Arrow,
   but the pipeline UDFs can't — the logic is Python. The Arrow-IPC-over-bridge alternative was
   measured and shelved (~1.05x for +30% RSS, see [0001](0001-gate-reframe-engine-portability.md)),
   so JSON-over-bridge is the deliberate posture for these UDFs.
4. **dbt tests are pure SQL (P2, #1024).** `goldenmatch_match_quality` computes pairwise
   precision/recall/F1 in portable SQL (`LEAST/GREATEST` + `SUM(CASE WHEN)` + `LEFT JOIN` +
   `NULLIF`) vs a ground-truth pairs table — adapter-portable, transparent, no UDF. The JSON
   `goldenmatch_evaluate` UDF stays for programmatic use.
5. **Table-returning match, Postgres-first (P3, #1027).** New `goldenmatch_match_pairs(...)
   RETURNS TABLE(target_id, reference_id, score)` pgrx UDF + a `goldenmatch_match` materialization
   (target model body + `reference` ref). `reference_id` is normalized to a 0-based reference index
   (`__ref_row_id__ - len(target)`, because match_df uses a combined `__row_id__` space). DuckDB
   stays JSON (mirrors the dedupe pairs/clusters Postgres-first posture).
6. **GoldenAnalysis stays Python-side (P4, #1028).** No dbt surface: `analyze_match`/`analyze_pipeline`
   consume in-memory `DedupeResult`/`PipeResult` objects, and `analyze(table)`'s frame analyzers are
   `frame.summary` + `quality.*` — the latter ARE GoldenCheck, already surfaced as the `quality_*`
   tests. A `goldenmatch_analyze` UDF would be thin + redundant. Documented alongside the GoldenPipe
   Python-side note.

## Consequences
- The dbt surface now covers: dedupe + two-table match materializations (incl. zero-config FS), an
  ER match-quality build gate, quality gates, transforms, identity-graph reads — all CI-gated.
- `goldenmatch_pg` bumped `0.7.0 → 0.8.0` (P1) → `0.9.0` (P3); `goldenmatch-duckdb` `0.6.0 → 0.7.0`.
- The pgrx version ritual (immutable published bases; new base + migration + control + Cargo + cp
  lines) was exercised twice; Postgres is verified CI-only (`rust_pgrx`).
- **P5 (GoldenFlow transform breadth) deferred** — additive/incremental (wrap more of ~76 transforms),
  not a capability gap; do it as a batched sweep when a concrete transform is needed.
- Follow-ups: zero-config/`probabilistic` *match* (`autoconfig_match`), DuckDB table-returning match,
  `match_mode='all'`, Snowflake parity for the new UDFs.
