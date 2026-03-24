# Installation

## Requirements

- Python 3.11 or later
- pip

## Standard Install

```bash
pip install goldencheck
```

This installs GoldenCheck with all core dependencies:

| Package | Version | Purpose |
|---------|---------|---------|
| polars | >=1.0 | Data operations |
| typer | >=0.12 | CLI framework |
| rich | >=13.0 | Console output |
| pyyaml | >=6.0 | Config file parsing |
| pydantic | >=2.0 | Config validation |
| openpyxl | >=3.1 | Excel file support |
| textual | >=1.0 | Interactive TUI |

## With LLM Boost

To use `--llm-boost` you need the optional `llm` extras:

```bash
pip install goldencheck[llm]
```

This adds:

| Package | Version | Purpose |
|---------|---------|---------|
| anthropic | >=0.30 | Anthropic Claude API |
| openai | >=1.30 | OpenAI GPT API |

You only need one provider installed, but both are included in the extras group.

## With MCP Server

To use `goldencheck mcp-serve` for Claude Desktop integration:

```bash
pip install goldencheck[mcp]
```

This adds:

| Package | Version | Purpose |
|---------|---------|---------|
| mcp | >=1.0 | Model Context Protocol server SDK |

See [MCP Server](MCP-Server) for setup instructions.

## All Extras

Install everything:

```bash
pip install goldencheck[llm,mcp]
```

## Verify Installation

```bash
goldencheck --version
# GoldenCheck 0.5.0
```

## Development Setup

Clone the repository and install in editable mode with dev dependencies:

```bash
git clone https://github.com/benzsevern/goldencheck.git
cd goldencheck
pip install -e ".[dev,llm,mcp]"
```

Dev dependencies include:

| Package | Purpose |
|---------|---------|
| pytest >=8.0 | Test runner |
| pytest-cov >=5.0 | Coverage reporting |
| ruff >=0.4 | Linter and formatter |

### Running Tests

```bash
pytest
```

With coverage:

```bash
pytest --cov=goldencheck --cov-report=term-missing
```

### Linting

```bash
ruff check goldencheck/
ruff format goldencheck/
```

### Project Structure

```
goldencheck/
├── goldencheck/
│   ├── cli/          # CLI entry points (Typer)
│   ├── config/       # YAML config loader, schema, writer
│   ├── engine/       # Scanner, validator, reader, sampler
│   ├── llm/          # LLM boost: providers, prompts, budget, merger
│   ├── mcp/          # MCP server (6 tools)
│   ├── models/       # Finding and DatasetProfile dataclasses
│   ├── notebook.py   # Jupyter/Colab display hooks
│   ├── profilers/    # 10 column profilers
│   ├── relations/    # 2 cross-column profilers
│   ├── semantic/     # Semantic type classification
│   ├── reporters/    # Rich console, JSON, CI reporters
│   └── tui/          # Textual TUI (4 tabs)
├── tests/            # pytest test suite (166 tests)
├── benchmarks/       # Speed and detection benchmarks
├── docs/wiki/        # This documentation
└── pyproject.toml
```
