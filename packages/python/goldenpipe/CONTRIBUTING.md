# Contributing to GoldenPipe

Thanks for your interest in contributing! Here's how to get started.

## Quick Start

```bash
git clone https://github.com/benzsevern/goldenpipe.git
cd goldenpipe
pip install -e ".[dev,golden-suite]"
pytest --tb=short
```

## Ways to Contribute

### Report Bugs

Open an issue with your version, config, and full error output.

### Add or Improve a Stage Adapter

GoldenPipe discovers adapters via `goldenpipe.stages` entry points. To add one, create a module that implements the adapter protocol and register it in your package's `pyproject.toml`:

```toml
[project.entry-points."goldenpipe.stages"]
my_stage = "my_package.stage:MyAdapter"
```

### Fix Bugs or Add Features

1. Fork the repo
2. Create a branch (`git checkout -b feat/my-feature`)
3. Make your changes
4. Run tests (`pytest --tb=short`) -- all tests must pass
5. Submit a PR

## Development Guidelines

### Key Files

- `goldenpipe/pipeline.py` -- core orchestration logic
- `goldenpipe/decisions.py` -- decision/routing logic
- `goldenpipe/cli/main.py` -- Typer CLI entry point

### Code Style

- Python 3.11+, type hints encouraged
- `ruff` for linting (configured in `pyproject.toml`)
- Line length: 100 characters

### Testing

- `pytest --tb=short` from project root
- New features need tests -- follow patterns in existing test files

### Commit Messages

Use conventional commits:

```
feat: add new stage adapter for dedup
fix: handle missing config in pipeline router
docs: update adapter registration example
```

### Pull Requests

- Squash merge all PRs (clean history on main)
- PR title follows conventional commit format: `feat: ...` or `fix: ...`
- Include a summary and test plan in the PR body

## Questions?

Open a Discussion or issue on the repo.
