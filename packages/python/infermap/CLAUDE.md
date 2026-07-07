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
- Optimal 1:1 assignment via `scipy.optimize.linear_sum_assignment` (Hungarian algorithm)
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

## Gotchas
- `print(polars_df)` crashes on Windows cp1252 terminal — use `.to_pandas().to_string()` instead
- PyPI `publish.yml` needs `skip-existing: true` to handle manual+workflow publish conflicts
- `conftest.py` exports `FIXTURES_DIR` not `FIXTURES` — check before importing in new test files
- Version must be bumped in both `pyproject.toml` and `infermap/__init__.py`

## Spec & Plan
- Design spec: `docs/superpowers/specs/2026-03-29-infermap-design.md`
- Implementation plan: `docs/superpowers/plans/2026-03-29-infermap-implementation.md`
