---
layout: default
title: Home
nav_order: 1
---

# GoldenFlow

Data transformation toolkit — standardize, reshape, and normalize messy data before it hits your pipeline.

Works on files (CSV, Excel, Parquet), cloud storage (S3, GCS), or live databases. Zero-config mode auto-detects what needs fixing.

## Install

```bash
pip install goldenflow
```

## Quick Start

```bash
goldenflow transform data.csv
```

One command to clean what GoldenCheck found and prep what GoldenMatch needs.

## Features

- **76 built-in transforms** across 11 categories: text, phone, name, address, date, categorical, numeric, email, identifiers, URL, auto-correct
- **5 domain packs**: people/HR, healthcare, finance, e-commerce, real estate
- **Zero-config mode**: auto-detects and fixes common data issues
- **Config-driven mode**: YAML config with `goldenflow learn` to auto-generate
- **Schema mapping**: auto-map columns between source and target files
- **Streaming**: handle large files in chunks
- **Cloud connectors**: S3 and GCS
- **MCP server**: 10 tools for AI agent integration
- **REST API**, **TUI**, **Jupyter** support
- **DQBench Transform Score: 100/100**

## Links

- [GitHub Repository](https://github.com/benzsevern/goldenflow)
- [PyPI Package](https://pypi.org/project/goldenflow/)
- [Smithery MCP Server](https://smithery.ai/servers/benzsevern/goldenflow)
