# Contributing to infermap

Thanks for your interest in improving infermap!

## Getting Started

```bash
git clone https://github.com/benzsevern/infermap.git
cd infermap
pip install -e ".[dev]"
pytest
```

## Ways to Contribute

- **Bug reports** -- open an issue with reproduction steps
- **Feature requests** -- describe the problem you're solving
- **Code** -- fork, branch, PR. All PRs need tests.
- **New scorers** -- the most common contribution; see below
- **New providers** -- DB connectors, cloud storage, etc.
- **Documentation** -- README, docstrings, examples

## Development Standards

- **Python 3.11+** with type hints
- **Polars** for all data operations (not pandas)
- **Ruff** for linting: `ruff check .` (100 char line length)
- **Pytest** for testing: `pytest --tb=short`
- **Conventional commits**: `feat:`, `fix:`, `docs:`, `test:`, `chore:`

## Adding a Custom Scorer

Scorers are registered via the `@infermap.scorer` decorator:

```python
import infermap
from infermap.types import FieldInfo, ScorerResult

@infermap.scorer(name="my_scorer", weight=0.7)
def my_scorer(source: FieldInfo, target: FieldInfo) -> ScorerResult | None:
    # Return ScorerResult(score, reasoning) or None to abstain
    return ScorerResult(score=0.8, reasoning="custom match logic")
```

## Pull Requests

1. Fork and create a feature branch (`feature/<name>`)
2. Write tests first (TDD)
3. Run `pytest` and `ruff check .`
4. Open a PR with a clear description and test plan
5. One approval required to merge
6. PRs are merged via squash merge to keep history clean
