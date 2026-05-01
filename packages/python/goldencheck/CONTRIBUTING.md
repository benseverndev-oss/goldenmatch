# Contributing to GoldenCheck

Thanks for your interest in improving GoldenCheck!

## Getting Started

```bash
git clone https://github.com/benzsevern/goldencheck.git
cd goldencheck
pip install -e ".[dev]"
pytest
```

## Ways to Contribute

- **Bug reports** -- open an issue with reproduction steps
- **Feature requests** -- describe the problem you're solving
- **Code** -- fork, branch, PR. All PRs need tests.
- **Documentation** -- README, docstrings, examples

## Development Standards

- **Python 3.11+** with type hints
- **Polars** for all data operations (not pandas)
- **Ruff** for linting: `ruff check .` (100 char line length)
- **Pytest** for testing: `pytest --tb=short`
- **Conventional commits**: `feat:`, `fix:`, `docs:`, `test:`, `chore:`

## Architecture

```
goldencheck/
├── cli/         # Typer CLI entry points
├── profilers/   # Column-level profilers (one per type)
├── relations/   # Cross-column profilers
├── engine/      # Scanner, validator, reader, sampler
├── config/      # YAML config schema, loader, writer
├── tui/         # Textual TUI
├── reporters/   # Rich, JSON, CI output
└── models/      # Finding, Profile data models
```

Each profiler is independent. Add a new one by:
1. Create `goldencheck/profilers/your_profiler.py` extending `BaseProfiler`
2. Add tests in `tests/profilers/test_your_profiler.py`
3. Register it in `goldencheck/engine/scanner.py`

## Pull Requests

1. Fork and create a feature branch
2. Write tests first (TDD)
3. Run `pytest` and `ruff check .`
4. Open a PR with a clear description
5. One approval required to merge
