# Contributing to GoldenFlow

Thanks for your interest in improving GoldenFlow!

## Getting Started

```bash
git clone https://github.com/benzsevern/goldenflow.git
cd goldenflow
pip install -e ".[dev]"
pytest
```

## Ways to Contribute

- **Bug reports** -- open an issue with reproduction steps
- **Feature requests** -- describe the problem you're solving
- **Code** -- fork, branch, PR. All PRs need tests.
- **New transforms** -- the most common contribution; see below
- **Documentation** -- README, docstrings, examples

## Development Standards

- **Python 3.11+** with type hints
- **Polars** for all data operations (not pandas)
- **Ruff** for linting: `ruff check .` (100 char line length)
- **Pytest** for testing: `pytest --tb=short`
- **Conventional commits**: `feat:`, `fix:`, `docs:`, `test:`, `chore:`

## Architecture

```
goldenflow/
├── cli/         # Typer CLI entry points
├── engine/      # TransformEngine, manifest, profiler bridge, selector, differ
├── transforms/  # Transform library (one file per category)
├── mapping/     # Schema mapping (name similarity, profile similarity, mapper)
├── config/      # YAML config schema, loader, learner
├── connectors/  # File and database connectors
├── domains/     # Domain packs (base, people_hr, ...)
├── llm/         # LLM-assisted config correction
├── mcp/         # MCP server for Claude Desktop
├── reporters/   # Rich console, JSON output
└── tui/         # Textual TUI
```

## Adding a New Transform

Transforms are registered via the `@register_transform` decorator:

1. Add the function to the appropriate file in `goldenflow/transforms/` (or create a new one)
2. Decorate it with `@register_transform(name=..., input_types=[...], mode=...)`
3. Add tests in `tests/transforms/test_<category>.py`
4. Import the module in `goldenflow/__init__.py` so it registers at import time

The `mode` parameter controls the execution path:
- `"expr"` -- returns a `pl.Expr` (stays in Rust, fastest)
- `"series"` -- receives and returns a `pl.Series` (optimized Python via `map_batches`)
- `"dataframe"` -- receives the full `pl.DataFrame` (for multi-column transforms like `split_name`)

Set `auto_apply=True` and an appropriate `priority` (0-100) to include the transform in zero-config mode.

## Pull Requests

1. Fork and create a feature branch
2. Write tests first (TDD)
3. Run `pytest` and `ruff check .`
4. Open a PR with a clear description
5. One approval required to merge
