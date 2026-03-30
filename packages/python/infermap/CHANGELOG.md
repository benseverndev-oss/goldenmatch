# Changelog

All notable changes to this project will be documented in this file.

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
