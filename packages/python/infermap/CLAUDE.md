# infermap

## Environment
- Windows 11, bash shell (Git Bash) — use Unix paths in scripts
- Python 3.12 at `C:\Users\bsevern\AppData\Local\Programs\Python\Python312\python.exe`
- Project: `D:\show_case\infermap`
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

## Gotchas
- `print(polars_df)` crashes on Windows cp1252 terminal — use `.to_pandas().to_string()` instead
- PyPI `publish.yml` needs `skip-existing: true` to handle manual+workflow publish conflicts
- `conftest.py` exports `FIXTURES_DIR` not `FIXTURES` — check before importing in new test files
- Version must be bumped in both `pyproject.toml` and `infermap/__init__.py`

## Spec & Plan
- Design spec: `docs/superpowers/specs/2026-03-29-infermap-design.md`
- Implementation plan: `docs/superpowers/plans/2026-03-29-infermap-implementation.md`
