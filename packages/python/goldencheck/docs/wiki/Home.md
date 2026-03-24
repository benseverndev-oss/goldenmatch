# GoldenCheck

**Data validation that discovers rules from your data so you don't have to write them.**

[![PyPI](https://img.shields.io/pypi/v/goldencheck?color=d4a017)](https://pypi.org/project/goldencheck/)
[![Downloads](https://img.shields.io/pypi/dm/goldencheck?color=blue&label=downloads)](https://pypi.org/project/goldencheck/)
[![CI](https://github.com/benzsevern/goldencheck/actions/workflows/test.yml/badge.svg)](https://github.com/benzsevern/goldencheck/actions/workflows/test.yml)

## Quick Links

| Page | Description |
|------|-------------|
| [Installation](Installation) | Install GoldenCheck and optional extras |
| [Quick Start](Quick-Start) | Get started in 60 seconds |
| [CLI Reference](CLI) | All commands, flags, and exit codes |
| [Interactive TUI](Interactive-TUI) | 4-tab interactive terminal interface |
| [Profilers](Profilers) | 10 column + 2 cross-column profilers |
| [Configuration](Configuration) | `goldencheck.yml` reference |
| [LLM Boost](LLM-Boost) | LLM-enhanced validation (~$0.01/scan) |
| [MCP Server](MCP-Server) | Claude Desktop integration (6 tools) |
| [Jupyter & Colab](Jupyter-and-Colab) | Rich HTML display in notebooks |
| [Benchmarks](Benchmarks) | DQBench Score: 72.00, speed tests |
| [Architecture](Architecture) | Module layout and data flow |

## What Makes GoldenCheck Different

Every other data validation tool makes you write rules first. GoldenCheck flips it:

1. **Scan** — GoldenCheck profiles your data and discovers issues automatically
2. **Review** — Interactive TUI shows findings sorted by severity with confidence scores
3. **Pin** — Keep the rules you care about, dismiss the rest
4. **Export** — Save to `goldencheck.yml` and validate in CI

Zero config to start. No schemas. No decorators. No YAML. Just point it at a file.

## DQBench Score: 87.71

| Tool | Mode | Score |
|------|------|-------|
| **GoldenCheck** | **zero-config** | **87.71** |
| Pandera | best-effort rules | 32.51 |
| Soda Core | best-effort rules | 22.36 |
| Great Expectations | best-effort rules | 21.68 |

GoldenCheck's zero-config discovery outperforms every competitor — even when they have hand-written rules.

## Integration Points

- **CLI** — `goldencheck data.csv` for terminal workflows
- **CI/CD** — `--json` output + exit codes for pipelines
- **MCP** — `goldencheck mcp-serve` for Claude Desktop
- **Jupyter/Colab** — Rich HTML display with `ScanResult`
- **LLM Rules** — `goldencheck learn` generates domain-specific rules
- **Python API** — Import `scan_file()` directly

---

**From the maker of [GoldenMatch](https://github.com/benzsevern/goldenmatch)** — entity resolution toolkit.
