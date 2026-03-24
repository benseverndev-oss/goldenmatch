# GoldenCheck

Data validation that discovers rules from your data so you don't have to write them.

[![PyPI](https://img.shields.io/pypi/v/goldencheck?color=d4a017)](https://pypi.org/project/goldencheck/)
[![Downloads](https://img.shields.io/pypi/dm/goldencheck?color=blue&label=downloads)](https://pypi.org/project/goldencheck/)
[![CI](https://github.com/benzsevern/goldencheck/actions/workflows/test.yml/badge.svg)](https://github.com/benzsevern/goldencheck/actions/workflows/test.yml)
[![codecov](https://codecov.io/gh/benzsevern/goldencheck/graph/badge.svg)](https://codecov.io/gh/benzsevern/goldencheck)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![Tests](https://img.shields.io/badge/tests-189%20passing-brightgreen)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/benzsevern/goldencheck/blob/main/scripts/goldencheck_demo.ipynb)

> Every competitor makes you write rules first. GoldenCheck flips it: **validate first, keep the rules you care about.**

## Why GoldenCheck?

|  | GoldenCheck | Great Expectations | Pandera | Pointblank |
|---|---|---|---|---|
| Rules | **Discovered from data** | Written by hand | Written by hand | Written by hand |
| Config | **Zero to start** | Heavy YAML/Python setup | Decorators/schemas | YAML/Python |
| Interface | **CLI + interactive TUI** | HTML reports | Exceptions | HTML/notebook |
| Learning curve | **One command** | Hours/days | Moderate | Moderate |
| LLM enhancement | **Yes ($0.01/scan)** | No | No | No |
| Fix suggestions | Yes, in TUI | No | No | No |
| Confidence scoring | Yes (H/M/L per finding) | No | No | No |
| DQBench Score | **87.71** | 21.68 (best-effort) | 32.51 (best-effort) | 6.94 (auto) |

## Install

```bash
pip install goldencheck
```

With LLM boost support:

```bash
pip install goldencheck[llm]
```

## Quick Start

```bash
# Scan a file — discovers issues, launches interactive TUI
goldencheck data.csv

# CLI-only output (no TUI)
goldencheck data.csv --no-tui

# With LLM enhancement (requires API key)
goldencheck data.csv --llm-boost --no-tui

# Validate against saved rules (for CI/pipelines)
goldencheck validate data.csv

# JSON output for CI integration
goldencheck data.csv --no-tui --json
```

## How It Works

```
1. SCAN     →  goldencheck data.csv
                GoldenCheck profiles your data and discovers what "healthy" looks like

2. REVIEW   →  Interactive TUI shows findings sorted by severity
                Each finding has: description, affected rows, sample values

3. PIN      →  Press Space to promote findings into permanent rules
                Dismiss false positives — they won't come back

4. EXPORT   →  Press F2 to save rules to goldencheck.yml
                Human-readable YAML with your pinned rules

5. VALIDATE →  goldencheck validate data.csv
                Enforce rules in CI with exit codes (0 = pass, 1 = fail)
```

## What It Detects

### Column-Level Profilers

| Profiler | What It Catches | Example |
|----------|----------------|---------|
| **Type inference** | String columns that are actually numeric | "Column `age` is string but 98% are integer" |
| **Nullability** | Required vs. optional columns | "0 nulls across 50k rows — likely required" |
| **Uniqueness** | Primary key candidates, near-duplicates | "100% unique — likely primary key" |
| **Format detection** | Emails, phones, URLs, dates | "94% email format, 6% malformed" |
| **Range & distribution** | Outliers, min/max bounds | "3 rows have values >10,000" |
| **Cardinality** | Low-cardinality enum suggestions | "4 unique values — possible enum" |
| **Pattern consistency** | Mixed formats within a column | "3 phone formats detected" |

### Cross-Column Profilers

| Profiler | What It Catches |
|----------|----------------|
| **Temporal ordering** | start_date > end_date violations |
| **Null correlation** | Columns that are null together (e.g., address + city + zip) |

## LLM Boost

Add `--llm-boost` to enhance profiler findings with LLM intelligence. The LLM receives a representative sample of your data and:

1. **Finds issues profilers miss** — semantic understanding (e.g., "12345" in a name column)
2. **Upgrades severity** — knows "emails should be required" even if the profiler only says "INFO"
3. **Discovers relationships** — identifies temporal ordering between columns like `signup_date` and `last_login`
4. **Downgrades false positives** — "mixed phone formats are common, not an error"

```bash
# Using OpenAI
export OPENAI_API_KEY=sk-...
goldencheck data.csv --llm-boost --llm-provider openai --no-tui

# Using Anthropic
export ANTHROPIC_API_KEY=sk-ant-...
goldencheck data.csv --llm-boost --no-tui
```

**Cost:** ~$0.01 per scan (one API call with representative samples, not per-row).

**Budget control:**
```bash
export GOLDENCHECK_LLM_BUDGET=0.50  # max spend per scan in USD
```

## Configuration (goldencheck.yml)

```yaml
version: 1

settings:
  sample_size: 100000
  fail_on: error

columns:
  email:
    type: string
    required: true
    format: email
    unique: true

  age:
    type: integer
    range: [0, 120]

  status:
    type: string
    enum: [active, inactive, pending, closed]

relations:
  - type: temporal_order
    columns: [start_date, end_date]

ignore:
  - column: notes
    check: nullability
```

Only pinned rules appear in this file — not every finding. The `ignore` list prevents dismissed findings from reappearing.

## CLI Reference

| Command | Description |
|---------|-------------|
| `goldencheck <file>` | Scan and launch TUI |
| `goldencheck scan <file>` | Explicit scan |
| `goldencheck validate <file>` | Validate against goldencheck.yml |
| `goldencheck review <file>` | Scan + validate, launch TUI |
| `goldencheck learn <file>` | Generate LLM validation rules |
| `goldencheck mcp-serve` | Start MCP server for Claude Desktop |

### Flags

| Flag | Description |
|------|-------------|
| `--no-tui` | Print results to console |
| `--json` | JSON output |
| `--fail-on <level>` | Exit 1 on severity: `error` or `warning` |
| `--llm-boost` | Enable LLM enhancement |
| `--llm-provider <name>` | LLM provider: `anthropic` (default) or `openai` |
| `--verbose` | Show info-level logs |
| `--debug` | Show debug-level logs |
| `--version` | Show version |

## Benchmarks

### Speed

| Dataset | Time | Throughput |
|---------|------|------------|
| 1K rows | 0.05s | 19K rows/sec |
| 10K rows | 0.23s | 43K rows/sec |
| 100K rows | 2.29s | 44K rows/sec |
| **1M rows** | **2.07s** | **482K rows/sec** |

### DQBench v1.0 — Head-to-Head

| Tool | Mode | DQBench Score |
|------|------|---------------|
| **GoldenCheck** | **zero-config** | **87.71** |
| Pandera | best-effort rules | 32.51 |
| Soda Core | best-effort rules | 22.36 |
| Great Expectations | best-effort rules | 21.68 |

> GoldenCheck's zero-config discovery outperforms every competitor — even when they have hand-written rules.

Run the benchmark yourself:
```bash
pip install dqbench goldencheck
dqbench run goldencheck
```

### Detection Accuracy

| Mode | Column Recall | Cost |
|------|--------------|------|
| Profiler-only (v0.1.0) | 87% | $0 |
| Profiler-only (v0.2.0 with confidence) | **100%** | $0 |
| With LLM Boost | **100%** | ~$0.003-0.01 |

Tested on a custom benchmark with 341 planted data quality issues across 9 categories.

> v0.2.0 improvements: minority wrong-type detection, range profiler chaining, broader temporal heuristics, and confidence scoring pushed profiler-only recall from 87% to 100%.

### Raha Benchmark Datasets

| Dataset | Column Recall |
|---------|--------------|
| Flights (2,376 rows) | **100%** (4/4 columns) |
| Beers (2,410 rows) | **80%** (4/5 columns) |

## Tech Stack

| Dependency | Purpose |
|-----------|---------|
| [Polars](https://pola.rs/) | All data operations |
| [Typer](https://typer.tiangolo.com/) | CLI framework |
| [Textual](https://textual.textualize.io/) | Interactive TUI |
| [Rich](https://rich.readthedocs.io/) | CLI output formatting |
| [Pydantic 2](https://docs.pydantic.dev/) | Config validation |

**Optional:** [Anthropic SDK](https://docs.anthropic.com/) / [OpenAI SDK](https://platform.openai.com/) for LLM Boost | [MCP SDK](https://modelcontextprotocol.io/) for MCP server

## MCP Server (Claude Desktop)

GoldenCheck includes an MCP server for Claude Desktop integration:

```bash
pip install goldencheck[mcp]
```

Add to your Claude Desktop config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "goldencheck": {
      "command": "goldencheck",
      "args": ["mcp-serve"]
    }
  }
}
```

**Available tools:**

| Tool | Description |
|------|-------------|
| `scan` | Scan a file for data quality issues (with optional LLM boost) |
| `validate` | Validate against pinned rules in goldencheck.yml |
| `profile` | Get column-level statistics and health score |
| `health_score` | Quick A-F grade for a data file |
| `get_column_detail` | Deep-dive into a specific column |
| `list_checks` | List all available profiler checks |

## Jupyter / Colab

GoldenCheck renders rich HTML in Jupyter notebooks:

```python
from goldencheck.engine.scanner import scan_file
from goldencheck.engine.confidence import apply_confidence_downgrade
from goldencheck.notebook import ScanResult

findings, profile = scan_file("data.csv")
findings = apply_confidence_downgrade(findings, llm_boost=False)

# Rich HTML display in notebooks
ScanResult(findings=findings, profile=profile)
```

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/benzsevern/goldencheck/blob/main/scripts/goldencheck_demo.ipynb)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and guidelines.

## License

MIT — see [LICENSE](LICENSE)

---

**From the maker of [GoldenMatch](https://github.com/benzsevern/goldenmatch)** — entity resolution toolkit.
