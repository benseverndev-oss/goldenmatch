# infermap

## Environment
- Windows 11, bash shell (Git Bash) — use Unix paths in scripts
- Python 3.12 at `C:\Users\bsevern\AppData\Local\Programs\Python\Python312\python.exe`
- Project lives in the goldenmatch monorepo at `packages/python/infermap/`. Pre-fold standalone path was `D:\show_case\infermap` — `_archive/goldenmatch-pre-fold/` retains that history.
- Two GitHub accounts: `benzsevern` (owner) and `benzsevern-mjh` (work)
- Always `gh auth switch --user benzsevern` before push, switch back after
- PyPI: `infermap` v0.1.0 published (trusted publishing configured)

## Testing
- `pytest --tb=short` from project root — 210 tests, ~2s
- Optional deps (psycopg2, duckdb, pandas) must use `pytest.importorskip()` — CI only installs `.[dev]`
- `ruff check infermap/ tests/` must pass — CI lint job fails on any error
- Run `ruff check --fix` before committing to auto-fix most issues
- `import polars` hangs under heavy CPU load (parallel subagents) — kill stale python processes first

## Architecture
- Weighted scorer pipeline: ExactScorer → AliasScorer → PatternTypeScorer → ProfileScorer → FuzzyNameScorer
- Score combination: weighted average, None = abstain, 0.0 = real negative, min 2 contributors
- Optimal 1:1 assignment via `infermap-core::linear_sum_assignment` (Hungarian/LAP; single-sourced 2026-07-26). Python dispatches native→`_lsa_pure` (byte-identical); TS uses `core/assignment/hungarian.ts` (the reference the Rust kernel was ported from). This REPLACED `scipy.optimize.linear_sum_assignment` on the assignment path (scipy remains only for `calibration`) — the old scipy path disagreed with TS on ties (both optimal, different pick). Cross-language locked by `tests/fixtures/assignment_parity.json` (read directly by `test_assignment.py` + TS `assignment-parity.test.ts`). `linear_sum_assignment` is gated in `_native_loader._GATED_ON`/`_COMPONENT_SYMBOLS`; native==pure in `test_native_parity.py`.
- Providers: FileProvider, InMemoryProvider, SchemaFileProvider, DBProvider (SQLite/Postgres/DuckDB)
- Config: `infermap.yaml` for scorer weights + alias extensions, schema definition files for target metadata
- CLI: `infermap map`, `apply`, `inspect`, `validate` via Typer
- Public API: `infermap.map()`, `from_config()`, `extract_schema()`, `@infermap.scorer` decorator

## Key Files
- `infermap/engine.py` — MapEngine orchestrator (scorer pipeline + assignment)
- `infermap/scorers/alias.py` — ALIASES dict + _ALIAS_LOOKUP (extended by config)
- `infermap/scorers/pattern_type.py` — SEMANTIC_TYPES regex registry + classify_field()
- `infermap/providers/db.py` — SQLite/Postgres/DuckDB extraction (MySQL stubbed)
- `infermap/types.py` — FieldInfo, SchemaInfo, ScorerResult, FieldMapping, MapResult
- `tests/conftest.py` — FIXTURES_DIR (not FIXTURES), make_field(), make_schema()

## Native / WASM kernels (cross-surface Rust core)
- Scorers + `detect_domain` share a pyo3-free Rust core `infermap-core` (`packages/rust/extensions/infermap-core`). Two thin wrappers: `infermap-native` (pyo3/abi3 wheel → `infermap[native]`) and `infermap-wasm` (wasm-bindgen → TS opt-in backend). The core is the single source of truth; pure Python/TS are byte-identical lossy fallbacks.
- Python dispatch: `infermap/_native_loader.py`. `INFERMAP_NATIVE=auto` (default) uses native per-component when the wheel symbol exists; `=1` requires it (raises); `=0` forces pure. A new kernel joins `_GATED_ON`/`_COMPONENT_SYMBOLS` only after `tests/test_native_parity.py` proves byte-identity. `check_native_symbols.py` reconciles host references vs kernel exports (silent-fallback guard).
- TS/WASM is **opt-in** — `enableInfermapWasm()` must be called or the WASM path stays dormant (the MCP servers now call it at startup; a plain consumer must too). The `infermap://scorer-info` MCP resource reports the live backend on both surfaces.
- pattern_type is the sharpest parity surface: three regex engines (Python `re`, Rust `regex`, JS `RegExp`) — the contract is ASCII-domain byte-identity; the `\d`/`\s` Unicode divergence is the documented edge. currency `\£\€` are dropped to `[$£€]` in the Rust pattern (the crate rejects those escapes).
- When adding a TS re-export the barrel doesn't surface (e.g. `detectDomainDetailed` lived in `detect.ts` but not `core/index.ts`), surface it — cross-package consumers import from the barrel. Cross-surface `InferredSchema` must stamp `schema_version` (the Python dataclass defaults it; TS must set it explicitly).

## Scorer parity discipline (`parity/infermap.yaml`, 2026-07-25)
- infermap is the **second package after goldenmatch** to model a compute-parity surface: `scorers` (the 7 scorer-class identities) + `scorer_kernels` (the infermap-core-backed subset) are declared in `parity/infermap.yaml`, so the `check_scorer_coverage` floor gates infermap too — every scorer is EITHER kernel-backed OR classified in `scorer_kernels_deferred` with a reason, and a new/regressed scorer FAILS the `api_parity` lane.
- **Single source per side, mirrored:** Python `infermap/scorers/__init__.py` exports `SCORER_NAMES` (derived from the class tuple) + `SCORER_KERNELS` (static dict scorer→kernel symbol, mirrors goldenmatch's `_NATIVE_SCORER_IDS`); TS `core/scorers/registry.ts` exports the matching `SCORER_NAMES`/`SCORER_KERNELS` sets (barrel-exported from `core/index.ts`). The api_parity emitters read these (`emit_python_surface.py` / `emit_ts_surface.mjs`); the TS drift guard is `tests/unit/scorer-parity-surface.test.ts` (asserts the declared sets vs the instantiated scorer classes).
- **Kernel-backed (shared):** `ExactScorer`/`FuzzyNameScorer`/`InitialismScorer`/`PatternTypeScorer`/`ProfileScorer` — one infermap-core kernel fed to Python-native (`_native_loader._COMPONENT_SYMBOLS`) AND TS-WASM (`core/wasm/backend.ts InfermapBackend`). **Declined:** `AliasScorer` (dictionary-lookup: the whole computation is `strip().lower()` + a runtime-DATA `ALIASES` lookup + a fixed 0.95/0.0 decision — trivial string ops not worth Arrow/FFI, the goldenflow `category_standardize` precedent; no meaningful kernel to single-source) and `LLMScorer` (n/a — model/network-backed, stays host).
- Adding a scorer = add the class + its `.name` to BOTH registries; if it has a kernel, add to both `SCORER_KERNELS` + `scorer_kernels.shared`; else add to `scorer_kernels_deferred` with a `deferred --`/`n/a --` reason. The `api_parity` path filter now watches `scorers/__init__.py` + `core/scorers/registry.ts`.

## Gotchas
- `print(polars_df)` crashes on Windows cp1252 terminal — use `.to_pandas().to_string()` instead
- PyPI `publish.yml` needs `skip-existing: true` to handle manual+workflow publish conflicts
- `conftest.py` exports `FIXTURES_DIR` not `FIXTURES` — check before importing in new test files
- Version must be bumped in both `pyproject.toml` and `infermap/__init__.py`

## Spec & Plan
- Design spec: `docs/superpowers/specs/2026-03-29-infermap-design.md`
- Implementation plan: `docs/superpowers/plans/2026-03-29-infermap-implementation.md`
