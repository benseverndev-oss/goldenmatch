# GoldenCheck

Data validation that discovers rules from your data. DQBench Score: 88.40.

## Commands

```bash
pip install -e ".[dev]"          # Dev install
pip install -e ".[llm]"          # With LLM boost
pip install -e ".[mcp]"          # With MCP server
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
├── cli/           # Typer CLI (9 commands: scan, validate, review, diff, watch, fix, learn, mcp-serve)
├── engine/        # Scanner, validator, confidence, fixer, differ, watcher
├── profilers/     # 10 column profilers (BaseProfiler ABC)
├── relations/     # Cross-column profilers (temporal, null correlation, numeric cross, age validation)
├── semantic/      # Type classifier + suppression engine + domain packs (healthcare, finance, ecommerce)
├── llm/           # LLM boost (providers, prompts, merger, budget, rule generator)
├── mcp/           # MCP server (9 tools incl. domain discovery)
├── config/        # Pydantic YAML config (goldencheck.yml)
├── models/        # Finding (with metadata dict), Profile dataclasses
├── notebook.py    # ScanResult wrapper + HTML renderers for Jupyter/Colab
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

## Environment

API keys for LLM testing live in `.testing/.env` (gitignored):
```bash
source .testing/.env   # loads OPENAI_API_KEY, TWINE credentials
```

## Benchmarks

```bash
python benchmarks/speed_benchmark.py                    # Speed test
python benchmarks/goldencheck_benchmark.py              # Detection (profiler-only)
source .testing/.env && python benchmarks/goldencheck_benchmark_llm.py  # With LLM
pip install dqbench && dqbench run goldencheck          # DQBench head-to-head
dqbench run all                                         # Compare against GX/Pandera/Soda
```

## Publishing

```bash
python -m build && source .testing/.env && python -m twine upload dist/*
```

## Gotchas

- `*.csv` is in `.gitignore` — test fixtures need `!tests/fixtures/*.csv` exception
- The CLI has a hand-rolled arg parser in `main()` callback for the `goldencheck data.csv` shorthand — update it when adding new flags
- `scan_file_with_llm` calls `scan_file(..., return_sample=True)` — suppression and boost run inside `scan_file`, not in the LLM path
- GitHub auth: `gh auth switch --user benzsevern` before pushing
- Ruff line length: 100 chars
