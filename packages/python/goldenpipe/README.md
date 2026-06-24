<!-- mcp-name: io.github.benseverndev-oss/goldenpipe -->
# GoldenPipe

**Golden Suite orchestrator** -- Check quality, fix issues, deduplicate records. One command.
Built by [Ben Severn](https://bensevern.dev).

[![PyPI](https://img.shields.io/pypi/v/goldenpipe?color=d4a017)](https://pypi.org/project/goldenpipe/)
[![CI](https://github.com/benseverndev-oss/goldenpipe/actions/workflows/test.yml/badge.svg)](https://github.com/benseverndev-oss/goldenpipe/actions/workflows/test.yml)
[![codecov](https://codecov.io/gh/benseverndev-oss/goldenpipe/graph/badge.svg)](https://codecov.io/gh/benseverndev-oss/goldenpipe)
[![Downloads](https://static.pepy.tech/badge/goldenpipe/month)](https://pepy.tech/project/goldenpipe)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-benzsevern.github.io%2Fgoldenpipe-d4a017)](https://benseverndev-oss.github.io/goldenpipe/)
[![DQBench Pipeline](https://img.shields.io/badge/DQBench%20Pipeline-88.07-gold)](https://github.com/benseverndev-oss/dqbench)
[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/benseverndev-oss/goldenpipe/blob/main/scripts/goldenpipe_demo.ipynb)

## What It Does

```
Raw Data
  | GoldenCheck    -- profile & discover quality issues
  | GoldenFlow     -- fix issues, standardize, reshape
  | GoldenMatch    -- deduplicate, match, create golden records
  | GoldenIdentity -- persist stable entity IDs across runs   (optional)
  | GoldenAnalysis -- terminal reporting over the run's artifacts  (optional)
  v
Golden Records + AnalysisReport
```

GoldenPipe orchestrates the full pipeline with adaptive logic:
- **Skips** transformation if no quality issues found
- **Routes** to privacy-preserving matching if sensitive fields detected
- **Reports** reasoning for every decision

## Install

```bash
pip install goldenpipe
```

Optional extras:

```bash
pip install goldenpipe[analysis]      # adds the GoldenAnalysis terminal report stage
pip install goldenpipe[golden-suite]  # full suite, incl. goldenanalysis
pip install goldenpipe[tui,api,mcp]   # interactive TUI, REST API, MCP server
```

## Quick Start

```python
import goldenpipe as gp

result = gp.run("customers.csv")

print(result.status)        # "success"
print(result.check)         # Quality findings
print(result.transform)     # What was fixed
print(result.match)         # Deduplicated clusters
print(result.reasoning)     # Why each decision was made
```

## CLI

```bash
goldenpipe run customers.csv                # Full pipeline
goldenpipe run customers.csv --verbose      # Show reasoning
goldenpipe run customers.csv --skip-flow    # Check + Match only
goldenpipe run customers.csv --strategy pprl  # Force privacy mode
goldenpipe run customers.csv -o golden.csv  # Save golden records

goldenpipe interactive                       # Launch the 4-tab TUI
goldenpipe interactive customers.csv         # Load a source; press 'r' to run it
goldenpipe interactive customers.csv -c pipeline.yml  # Load source + YAML config
```

## Remote MCP Server

GoldenPipe is available as a hosted MCP server on [Smithery](https://smithery.ai/servers/benzsevern/goldenpipe) — connect from any MCP client without installing anything.

**Claude Desktop / Claude Code:**
```json
{
  "mcpServers": {
    "goldenpipe": {
      "url": "https://goldenpipe-mcp-production.up.railway.app/mcp/"
    }
  }
}
```

**Local server:**
```bash
pip install goldenpipe[mcp]
goldenpipe mcp-serve
```

4 tools available: list pipeline stages, validate wiring, run full check-transform-match pipeline, explain configs.

## Part of the Golden Suite

| Tool | Purpose | Install |
|------|---------|---------|
| [GoldenCheck](https://github.com/benseverndev-oss/goldencheck) | Validate & profile data quality | `pip install goldencheck` |
| [GoldenFlow](https://github.com/benseverndev-oss/goldenflow) | Transform & standardize data | `pip install goldenflow` |
| [GoldenMatch](https://github.com/benseverndev-oss/goldenmatch) | Deduplicate & match records | `pip install goldenmatch` |
| [GoldenPipe](https://github.com/benseverndev-oss/goldenpipe) | Orchestrate the full pipeline | `pip install goldenpipe` |

## Author

[Ben Severn](https://bensevern.dev)

## License

MIT
