# Changelog

All notable changes to `dbt-goldensuite`. This package ships inside the Golden
Suite monorepo and is consumed from there (not published to PyPI).

## [Unreleased]

### Changed
- Relocated from `packages/python/goldenmatch/dbt-goldensuite/` to the top-level
  `packages/dbt/goldensuite/` per the documented monorepo layout. Update the
  `subdirectory:` in your `packages.yml` to `packages/dbt/goldensuite`.
- Synced `dbt_project.yml` version to `0.5.0` (was stale at `0.1.0`) and bumped
  the `goldenmatch` floor to `>=2.0.0`.

### Fixed
- README install instructions: the package is not on PyPI, so `pip install
  dbt-goldensuite` was replaced with the `packages.yml` git-subdirectory install
  (macros) and a `pip install "git+...#subdirectory=..."` for the Python helper.

### Added
- **`grain_strict` on the `goldenmatch_key_integrity` test.** Opt-in argument
  (default `false` = byte-identical to the prior key-only test). When `true` and a
  `grain` is supplied, uniqueness / fan-out is evaluated on `key + grain` — true
  "unique at grain", so a fact that legitimately repeats a key across grain buckets
  (e.g. daily rows per customer) is certified instead of being reported as fan-out.
  Mirrors the Python `certify_key_integrity(grain_strict=True)` in lockstep so the
  SQL test and the capability stay semantically identical.
- **`run_goldenmatch_crosswalk` Python helper (semantic-layer wedge B).** Resolves
  identity once and materializes the durable `{source, source_pk, resolved_entity_id}`
  crosswalk table in DuckDB — the conformed join key a semantic layer (dbt/MetricFlow,
  Cube, OSI) groups metrics by, keyed on the control-plane `entity_id` (not the
  run-local cluster id `goldenmatch_dedupe` emits). Pass a `store_path` for entity ids
  durable across runs ("resolve once, every metric inherits correct joins"). No pure-SQL
  UDF equivalent — the resolved id comes from the stateful Identity Control Plane — so
  it's a Python-helper materialization (call from a dbt Python model). Wraps
  `goldenmatch.semantic.build_resolved_crosswalk` (ADR 0050).
- **`goldenmatch_match` materialization (two-table record linkage).** Links a target
  model against a `reference` table and outputs matched pairs `(target_id, reference_id,
  score)` (best match per target). Backed by a new table-returning `goldenmatch_match_pairs`
  pgrx UDF (`goldenmatch_pg` 0.8.0 -> 0.9.0). Postgres-first (DuckDB raises a clear error).
  `match_config` optional (omit for zero-config). `reference_id` is normalized to a 0-based
  reference-table index.
- **`goldenmatch_match_quality` dbt test.** A pure-SQL generic test that fails the
  build when a dedupe model's pairwise precision/recall/F1 (vs a ground-truth pairs
  table) drops below configured floors. Handles the `pairs` and `clusters` output
  shapes; portable (no UDF); `input: clusters|pairs`, `min_f1`/`min_precision`/`min_recall`.
- **Zero-config Fellegi-Sunter dedupe.** `probabilistic=true` on the
  `goldenmatch_dedupe` materialization (and `run_goldenmatch_dedupe(probabilistic=True)`)
  builds an FS model from the data with no hand-written config; `match_config` is
  now optional (omitting it runs standard zero-config dedupe). Backed by a new
  `mode` parameter on `goldenmatch_autoconfig` across DuckDB + Postgres
  (`goldenmatch-duckdb` 0.7.0, `goldenmatch_pg` 0.8.0). Snowflake + `probabilistic`
  raises a clear error (explicit `match_config` still works there).
- Restored the CI lane removed in #464: `dbt parse` smoke (in-memory DuckDB
  profile) plus the package's pytest suite now run on changes under `packages/dbt/**`.
- Standalone `profiles.yml` + `profile:` key so the project parses on its own.

## [0.5.0]
- GoldenFlow transform macros, Snowflake Cortex macros, and `infermap_apply`.

## [0.4.0]
- `goldenmatch_dedupe` custom materialization (golden / clusters / pairs output).

## [0.3.0]
- Identity Graph read macros (`identity_resolve`, `identity_list`, `identity_view`,
  `identity_history`, `identity_conflicts`).

## [0.2.0]
- Folded `dbt-goldenmatch` + the standalone `packages/dbt/goldencheck` into a single
  `dbt-goldensuite` package; added GoldenCheck quality-gate macros (#464).
