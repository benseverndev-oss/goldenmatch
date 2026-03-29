<!-- mcp-name: io.github.benzsevern/goldenpipe -->
# GoldenPipe

**Golden Suite orchestrator** -- Check quality, fix issues, deduplicate records. One command.

[![PyPI](https://img.shields.io/pypi/v/goldenpipe?color=d4a017)](https://pypi.org/project/goldenpipe/)
[![Python](https://img.shields.io/pypi/pyversions/goldenpipe)](https://pypi.org/project/goldenpipe/)

## What It Does

```
Raw Data
  | GoldenCheck   -- profile & discover quality issues
  | GoldenFlow    -- fix issues, standardize, reshape
  | GoldenMatch   -- deduplicate, match, create golden records
  v
Golden Records
```

GoldenPipe orchestrates the full pipeline with adaptive logic:
- **Skips** transformation if no quality issues found
- **Routes** to privacy-preserving matching if sensitive fields detected
- **Reports** reasoning for every decision

## Install

```bash
pip install goldenpipe
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

| Package | What It Does |
|---------|-------------|
| [GoldenCheck](https://github.com/benzsevern/goldencheck) | Data validation |
| [GoldenFlow](https://github.com/benzsevern/goldenflow) | Data transformation |
| [GoldenMatch](https://github.com/benzsevern/goldenmatch) | Entity resolution |
| **GoldenPipe** | Orchestrates all three |

## License

MIT
