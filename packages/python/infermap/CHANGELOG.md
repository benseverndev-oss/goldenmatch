# Changelog

All notable changes to this project will be documented in this file.

## [0.2.0] - 2026-04-09

### Added
- **Cross-language accuracy benchmark** (`benchmark/`) — manifest-driven case corpus, hand-anchored metrics (F1, top-1, MRR, ECE), Python and TypeScript runners with bit-identical scoring on the v0.1 scorer set, and a self-test corpus that gates CI.
- **`MapEngine.map_schemas()`** (Python) / **`MapEngine.mapSchemas()`** (TypeScript) — pre-extracted-schema entry point that bypasses provider re-extraction. Use this when you already hold a `SchemaInfo`.
- **`return_score_matrix=True`** flag — exposes the full M×N candidate score matrix on `MapResult.score_matrix`, enabling MRR computation, runner-up inspection, and override UIs.
- Synthetic case generator with deterministic seeding (Python is canonical; TypeScript loads the committed JSON).
- Regression-gating CI: PRs that drop F1 by more than the threshold require an explicit `regression-ack` label.
- New examples: `08_benchmark_introspection.py` and `typescript/08_score_matrix_introspection.ts`.
- Documentation: `docs/benchmark.md` (rendered on the docs site).

### Fixed
- Cross-language drift on the synthetic slice (~25pp F1 gap → 0.3pp): the Python engine was round-tripping `SchemaInfo` through polars and re-inferring dtypes; `map_schemas()` is the supported way to avoid that.
- `MapEngine.map_schemas()` now deep-copies the target schema before merging schema-file aliases (no caller mutation).

## [0.1.0] - 2026-03-30

### Added
- Core types: FieldInfo, SchemaInfo, ScorerResult, FieldMapping, MapResult
- 5 built-in scorers: ExactScorer, AliasScorer, PatternTypeScorer, ProfileScorer, FuzzyNameScorer
- Plugin system with `@infermap.scorer` decorator for custom scorers
- FileProvider (CSV, Parquet, Excel)
- InMemoryProvider (Polars, Pandas, list[dict])
- SchemaFileProvider (YAML/JSON definition files with aliases and required fields)
- DBProvider: SQLite (full), PostgreSQL (full), DuckDB (full), MySQL (stubbed)
- MapEngine orchestrator with weighted scorer pipeline and Hungarian optimal assignment
- Score combination with minimum 2-contributor threshold and None/0.0 distinction
- Config loading via `infermap.yaml` (scorer weights, alias extensions)
- Saved mapping configs with `to_config()` / `from_config()` roundtrip
- CLI: `infermap map`, `apply`, `inspect`, `validate` commands
- `--strict` mode for CI gate validation
- Public API: `infermap.map()`, `from_config()`, `extract_schema()`, `default_scorers()`
- 210 tests passing
- GitHub Pages documentation site (6 pages)
- GitHub Wiki (9 pages)
- CI/CD: test matrix (3.11/3.12/3.13), coverage, lint, smoke test
- PyPI publishing via trusted publishing workflow
- Colab demo notebook
