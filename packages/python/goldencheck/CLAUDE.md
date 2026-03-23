# GoldenCheck

Data validation that discovers rules from your data. DQBench Score: 72.00.

## Commands

```bash
pip install -e ".[dev]"          # Dev install
pip install -e ".[llm]"          # With LLM boost
pytest --tb=short -v             # Run tests (166 passing)
ruff check .                     # Lint
ruff check . --fix               # Auto-fix lint
goldencheck data.csv --no-tui    # Scan a file (CLI output)
goldencheck data.csv             # Scan with TUI
goldencheck validate data.csv    # Validate against goldencheck.yml
```

## Architecture

```
goldencheck/
├── cli/           # Typer CLI (scan, validate, review commands)
├── engine/        # Scanner pipeline, validator, confidence scoring
├── profilers/     # 10 column profilers (BaseProfiler ABC)
├── relations/     # Cross-column profilers (temporal, null correlation)
├── semantic/      # Type classifier + suppression engine
├── llm/           # LLM boost (providers, prompts, merger, budget)
├── config/        # Pydantic YAML config (goldencheck.yml)
├── models/        # Finding, Profile dataclasses
├── reporters/     # Rich, JSON, CI output
└── tui/           # Textual TUI (4 tabs)
```

## Pipeline Flow

```
read_file → maybe_sample → run profilers → classify semantic types
→ apply suppression → corroboration boost → sort by severity
→ (optional) LLM boost → confidence downgrade → report/TUI
```

## Key Patterns

- **All profilers extend `BaseProfiler`** with `profile(df, column, *, context=None) -> list[Finding]`
- **Findings are dataclasses** — use `dataclasses.replace()`, never mutate
- **Confidence 0.0-1.0** on every Finding — high (≥0.8), medium (0.5-0.79), low (<0.5)
- **Severity: ERROR > WARNING > INFO** (IntEnum)
- **`source` field**: None = profiler, "llm" = LLM-generated
- **Polars-native** — all data ops use Polars, never pandas
- **stdlib `random` only** — no numpy for randomness

## Testing

- TDD: tests first, then implementation
- Fixtures: `tests/fixtures/simple.csv`, `tests/fixtures/messy.csv`
- Convention: `tests/{module}/test_{file}.py`
- Commit messages: conventional commits (`feat:`, `fix:`, `test:`, `docs:`, `chore:`)

## Gotchas

- `*.csv` is in `.gitignore` — test fixtures need `!tests/fixtures/*.csv` exception
- The CLI has a hand-rolled arg parser in `main()` callback for the `goldencheck data.csv` shorthand — update it when adding new flags
- `scan_file_with_llm` calls `scan_file(..., return_sample=True)` — suppression and boost run inside `scan_file`, not in the LLM path
- GitHub auth: `gh auth switch --user benzsevern` before pushing
- Ruff line length: 100 chars
