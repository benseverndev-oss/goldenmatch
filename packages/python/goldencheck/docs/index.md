---
title: Home
layout: home
nav_order: 1
---

# GoldenCheck

**Data validation that discovers rules from your data so you don't have to write them.**

[![PyPI](https://img.shields.io/pypi/v/goldencheck?color=d4a017)](https://pypi.org/project/goldencheck/)
[![Downloads](https://static.pepy.tech/badge/goldencheck/month)](https://pepy.tech/project/goldencheck)
[![CI](https://github.com/benzsevern/goldencheck/actions/workflows/test.yml/badge.svg)](https://github.com/benzsevern/goldencheck/actions/workflows/test.yml)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![Tests](https://img.shields.io/badge/tests-296%20passing-brightgreen)

---

Every other data validation tool makes you write rules first. GoldenCheck flips it: **validate first, keep the rules you care about.**

## DQBench Score: 88.40

| Tool | Mode | Score |
|------|------|-------|
| **GoldenCheck** | **zero-config** | **88.40** |
| Pandera | best-effort rules | 32.51 |
| Soda Core | best-effort rules | 22.36 |
| Great Expectations | best-effort rules | 21.68 |

GoldenCheck's zero-config discovery outperforms every competitor — even when they have hand-written rules.

## How It Works

```
1. SCAN     →  goldencheck data.csv
               Profiles your data and discovers what "healthy" looks like

2. REVIEW   →  Interactive TUI shows findings sorted by severity
               Each finding has: description, affected rows, sample values

3. PIN      →  Press Space to promote findings into permanent rules
               Dismiss false positives — they won't come back

4. EXPORT   →  Press F2 to save rules to goldencheck.yml
               Human-readable YAML with your pinned rules

5. VALIDATE →  goldencheck validate data.csv
               Enforce rules in CI with exit codes (0 = pass, 1 = fail)
```

## Quick Install

```bash
pip install goldencheck
goldencheck data.csv
```

## Documentation

| Guide | Description |
|-------|-------------|
| [Installation]({% link installation.md %}) | Install GoldenCheck and optional extras |
| [Quick Start]({% link quick-start.md %}) | Get started in 60 seconds |
| [CLI Reference]({% link cli.md %}) | 14 commands, flags, and exit codes |
| [Interactive TUI]({% link interactive-tui.md %}) | 4-tab interactive terminal interface |
| [Profilers]({% link profilers.md %}) | 10 column + 4 cross-column profilers |
| [Configuration]({% link configuration.md %}) | `goldencheck.yml` reference |
| [Domain Packs]({% link domain-packs.md %}) | Healthcare, finance, e-commerce type definitions |
| [Schema Diff]({% link schema-diff.md %}) | Compare data versions, detect drift |
| [Auto-Fix]({% link auto-fix.md %}) | Automated data cleaning |
| [Watch Mode]({% link watch-mode.md %}) | Continuous directory monitoring |
| [REST API]({% link rest-api.md %}) | HTTP microservice |
| [Database Scanning]({% link database-scanning.md %}) | Scan Postgres, Snowflake, BigQuery directly |
| [Scheduled Runs]({% link scheduled-runs.md %}) | Cron-like scheduling with webhooks |
| [LLM Boost]({% link llm-boost.md %}) | LLM-enhanced validation (~$0.01/scan) |
| [MCP Server]({% link mcp-server.md %}) | Claude Desktop integration (9 tools) |
| [Jupyter & Colab]({% link jupyter-and-colab.md %}) | Rich HTML display in notebooks |
| [GitHub Action]({% link github-action.md %}) | CI integration with PR comments |
| [Benchmarks]({% link benchmarks.md %}) | Speed tests and DQBench results |
| [Architecture]({% link architecture.md %}) | Module layout and data flow |

---

**Part of the Golden Suite:**
[GoldenMatch](https://github.com/benzsevern/goldenmatch) ·
[dbt-goldencheck](https://github.com/benzsevern/dbt-goldencheck) ·
[goldencheck-types](https://github.com/benzsevern/goldencheck-types) ·
[goldencheck-action](https://github.com/benzsevern/goldencheck-action)
