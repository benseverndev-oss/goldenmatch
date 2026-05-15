# Contributing to GoldenFlow

Thanks for your interest in improving GoldenFlow!

## Getting Started

```bash
git clone https://github.com/benseverndev-oss/goldenflow.git
cd goldenflow
pip install -e ".[dev]"
pytest
```

## Ways to Contribute

- **Bug reports** -- open an issue with reproduction steps
- **Feature requests** -- describe the problem you're solving
- **Code** -- fork, branch, PR. All PRs need tests.
- **New transforms** -- the most common contribution; see below
- **New domain packs** -- see the 5 existing packs as examples
- **Cloud connectors** -- S3 and GCS exist; more connectors welcome
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
├── cli/         # Typer CLI entry points (14 commands)
├── engine/      # TransformEngine, manifest, profiler bridge, selector, differ
├── transforms/  # Transform library (one file per category)
├── mapping/     # Schema mapping (name similarity, profile similarity, mapper)
├── config/      # YAML config schema, loader, learner
├── connectors/  # file.py, database.py, s3.py, gcs.py
├── domains/     # Domain packs (base.py + 5 domain implementations)
├── llm/         # LLM-assisted config correction
├── mcp/         # MCP server for Claude Desktop
├── reporters/   # Rich console, JSON output
├── tui/         # Textual TUI
├── streaming.py # StreamProcessor for batch/incremental processing
├── history.py   # Run history tracking
└── notebook.py  # Jupyter _repr_html_ support
```

---

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

Example:

```python
from goldenflow.transforms import register_transform
import polars as pl

@register_transform(
    name="my_transform",
    input_types=["text"],
    auto_apply=False,
    priority=50,
    mode="series",
)
def my_transform(series: pl.Series) -> pl.Series:
    return series.str.replace_all(r"\s+", " ")
```

---

## Adding a Domain Pack

Domain packs bundle transforms for a specific industry vertical. All 5 existing packs (`people_hr`, `healthcare`, `finance`, `ecommerce`, `real_estate`) are good starting points.

1. Create `goldenflow/domains/<your_domain>.py` subclassing `DomainPack`:

```python
from goldenflow.domains.base import DomainPack
from goldenflow.config.schema import GoldenFlowConfig, TransformSpec

class MyDomainPack(DomainPack):
    name = "my_domain"
    description = "Transforms for the my-domain vertical"

    @property
    def default_config(self) -> GoldenFlowConfig:
        return GoldenFlowConfig(
            transforms=[
                TransformSpec(column="phone", ops=["phone_e164"]),
                TransformSpec(column="date", ops=["date_iso8601"]),
                # ... domain-specific ops
            ]
        )
```

2. Register it in `goldenflow/domains/__init__.py`:

```python
from goldenflow.domains.my_domain import MyDomainPack

_PACKS = {
    ...,
    "my_domain": MyDomainPack(),
}
```

3. Add tests in `tests/domains/test_my_domain.py`

4. Document the pack in the README under "Domain Packs"

---

## Adding a Cloud Connector

Cloud connectors live in `goldenflow/connectors/`. The existing `s3.py` and `gcs.py` are the reference implementations.

1. Create `goldenflow/connectors/<provider>.py`:

```python
"""<Provider> connector for GoldenFlow."""
from __future__ import annotations
from pathlib import Path
import polars as pl

def read_<provider>(uri: str) -> pl.DataFrame:
    """Read a DataFrame from <provider>://<bucket>/<path>."""
    try:
        import <provider_sdk>
    except ImportError:
        raise ImportError(
            "Install the <provider> extra: pip install goldenflow[<provider>]"
        )
    # ... implementation

def write_<provider>(df: pl.DataFrame, uri: str) -> None:
    """Write a DataFrame to <provider>://<bucket>/<path>."""
    # ... implementation
```

2. Add URI scheme detection in `goldenflow/connectors/file.py` so the file connector delegates automatically:

```python
if str(path).startswith("<provider>://"):
    from goldenflow.connectors.<provider> import read_<provider>
    return read_<provider>(str(path))
```

3. Add the optional dependency to `pyproject.toml`:

```toml
[project.optional-dependencies]
<provider> = ["<provider-sdk>>=x.y"]
```

4. Add tests in `tests/connectors/test_<provider>.py` (mock the SDK)

---

## Working with the Streaming Module

`StreamProcessor` in `goldenflow/streaming.py` wraps `TransformEngine` for incremental processing. Use it when:

- Files are too large to load into memory at once
- You need to process records as they arrive (event streams)
- You want progress feedback during long-running transforms

```python
from goldenflow.streaming import StreamProcessor

processor = StreamProcessor(config=config)

# Single record
result = processor.transform_one({"name": "  John  "})

# Batch
result = processor.transform_batch(df_chunk)

# Full file in chunks
for result in processor.stream_file("large.csv", chunk_size=10_000):
    # result.df is a Polars DataFrame for this chunk
    # result.manifest has per-chunk transform records
    save_chunk(result.df)
```

The `goldenflow stream` CLI command uses `StreamProcessor` with a Rich progress bar. If you add features to `StreamProcessor`, update `cli/main.py:stream()` accordingly.

---

## LLM Integration Guidelines

LLM-enhanced transforms live in `goldenflow/llm/corrector.py`. Keep these rules:

- **Opt-in only** -- LLM transforms must NOT be `auto_apply=True`. They activate only when `--llm` is passed or `GOLDENFLOW_LLM=1` is set.
- **Graceful fallback** -- if no API key is found, skip the LLM transform silently (log a warning, don't crash).
- **Never block** -- LLM calls must not block the main transform pipeline. Use batching or async where possible.
- **Test with mocks** -- `tests/llm/` uses mocked API responses. Never make real API calls in tests.
- **API key sources** -- check `OPENAI_API_KEY` first, then `ANTHROPIC_API_KEY`. Document which model is used.

To add a new LLM-assisted transform:

1. Add it to `goldenflow/llm/corrector.py` using `@register_transform` with `auto_apply=False`
2. Ensure it handles missing API keys gracefully
3. Add mock-based tests in `tests/llm/`
4. Document the env vars needed in the README

---

## Pull Requests

1. Fork and create a feature branch (`feature/<name>`)
2. Write tests first (TDD)
3. Run `pytest` and `ruff check .`
4. Open a PR with a clear description and test plan
5. One approval required to merge
6. PRs are merged via squash merge to keep history clean
