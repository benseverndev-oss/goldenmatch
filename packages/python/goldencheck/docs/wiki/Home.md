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
| [CLI Reference](CLI) | 11 commands, flags, and exit codes |
| [Interactive TUI](Interactive-TUI) | 4-tab interactive terminal interface |
| [Profilers](Profilers) | 10 column + 4 cross-column profilers |
| [Baseline](Baseline) | Deep analysis, drift detection, YAML profiles |
| [Configuration](Configuration) | `goldencheck.yml` reference |
| [Domain Packs](Domain-Packs) | Healthcare, finance, e-commerce type definitions |
| [Schema Diff](Schema-Diff) | Compare data versions, detect drift |
| [Auto Fix](Auto-Fix) | Automated data cleaning (safe/moderate/aggressive) |
| [Watch Mode](Watch-Mode) | Continuous directory monitoring |
| [LLM Boost](LLM-Boost) | LLM-enhanced validation (~$0.01/scan) |
| [MCP Server](MCP-Server) | Claude Desktop integration (9 tools) |
| [Jupyter & Colab](Jupyter-and-Colab) | Rich HTML display in notebooks |
| [GitHub Action](GitHub-Action) | CI integration with PR comments |
| [REST API](REST-API) | HTTP microservice (POST /scan, /scan/url) |
| [Database Scanning](Database-Scanning) | Scan Postgres, Snowflake, BigQuery directly |
| [Scheduled Runs](Scheduled-Runs) | Cron-like scheduling with webhooks |
| [Benchmarks](Benchmarks) | DQBench Score: 88.40, speed tests |
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

- **CLI** — 15 commands: scan, validate, review, diff, watch, fix, learn, init, history, serve, scan-db, schedule, mcp-serve, baseline
- **Baseline** — `goldencheck baseline data.csv` creates a YAML profile; `goldencheck scan --baseline` detects 13 types of drift
- **CI/CD** — `goldencheck-action@v1` for GitHub, `--json` + exit codes for any CI
- **MCP** — 9 tools for Claude Desktop integration
- **Domain Packs** — `--domain healthcare|finance|ecommerce`
- **Jupyter/Colab** — Rich HTML display with `ScanResult`
- **Python API** — `from goldencheck import scan_file, Finding`

---

**From the maker of [GoldenMatch](https://github.com/benzsevern/goldenmatch)** — entity resolution toolkit.
