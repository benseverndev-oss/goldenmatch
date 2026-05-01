# Changelog

All notable changes to this project will be documented in this file.

## [0.3.0] - 2026-04-10

### Added
- **Valentine corpus** — 82 real-world schema-matching cases extracted from the Valentine benchmark (BSD-3-Clause, Zenodo). Subcategories: Magellan (7), ChEMBL (25), OpenData (25), TPC-H (25). `infermap-bench rebuild-manifest` is now implemented (was a stub).
- **Confidence calibration** — `MapEngine(calibrator=...)` (Python) / `MapEngine({ calibrator })` (TypeScript). Post-assignment only: relabels confidence without changing which mappings are picked. Ships `IdentityCalibrator`, `IsotonicCalibrator` (PAV), `PlattCalibrator` (Nelder-Mead). JSON round-trip via `kind` discriminator. ECE on Valentine: 0.46 → 0.005 with isotonic.
- **`InitialismScorer`** — new scorer that matches abbreviation-style column names (e.g. `assay_id ↔ ASSI`, `confidence_score ↔ CONSC`). DP-based prefix-concat matcher. Abstains on non-matches. Weight 0.75. ChEMBL F1: 0.524 → 0.819.
- **Common-prefix canonicalization** — `FieldInfo.canonical_name` (Python) / `canonicalName` (TypeScript). Engine strips schema-wide common delimiter-bounded prefixes/suffixes before scoring so `City` vs `prospect_City` becomes `City` vs `City`. Deep-copies input schemas (no caller mutation).
- **Domain dictionaries** — curated alias YAMLs for `healthcare`, `finance`, `ecommerce` shipped with the package. `MapEngine(domains=["healthcare"])` loads them. Generic domain loaded by default. `infermap.yaml` gains a `domains:` key. `mrn ↔ patient_id` confidence: 0.27 → 0.95 with healthcare.
- `infermap-bench calibrate` CLI subcommand — fits a calibrator from labeled cases, reports holdout/full ECE.
- `infermap-bench run --calibrator cal.json` — applies a fitted calibrator during benchmark runs.
- New examples: `09_domain_dictionaries.py`, `09_domain_dictionaries.ts`, `10_calibration.py`.
- Documentation: `docs/domain-dictionaries.md` (Pages).

### Changed
- **Default `min_confidence` lowered from 0.3 to 0.2.** Empirically tuned on the combined Valentine + synthetic corpus. Combined F1: 0.657 → 0.765. Users who prefer the old behavior can pass `min_confidence=0.3` explicitly.
- **TypeScript parity** — all new features (calibration, prefix-norm, InitialismScorer, domain dictionaries) ported to TypeScript. Benchmark F1 within 0.0005 of Python; synthetic slice is bit-identical. 186 TS tests.

### Fixed
- Calibrator deserialization hardened: `IsotonicCalibrator.fromJSON` and `PlattCalibrator.fromJSON` now throw on malformed input instead of silently falling back to defaults.
- Calibrator output validated: engine throws if `transform()` returns wrong length or non-finite values.
- `isPrefixConcat` DP guarded against pathological inputs (max 200 chars target, 50 source tokens).

### Headline numbers

| Metric | v0.2 | v0.3 | Δ |
|---|---:|---:|---:|
| Overall F1 | 0.657 | **0.840** | **+18.3pp** |
| Valentine F1 | 0.578 | 0.794 | +21.6pp |
| ChEMBL F1 | 0.524 | 0.819 | +29.5pp |
| Valentine MRR | 0.870 | 0.957 | +8.7pp |
| Valentine ECE (calibrated) | 0.463 | 0.005 | −45.8pp |

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
