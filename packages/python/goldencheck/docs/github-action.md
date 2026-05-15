---
title: GitHub Action
layout: default
nav_order: 18
---

Add data quality checks to your CI pipeline with one line.

## Quick Start

```yaml
- uses: benseverndev-oss/goldencheck-action@v1
  with:
    files: "data/*.csv"
```

## Features

- Scans all matching data files
- Posts a PR comment with findings summary
- Pass/fail status check based on severity threshold
- Updates existing comment on subsequent pushes
- Pip caching for fast installs

## Full Example

```yaml
name: Data Quality
on: [pull_request]

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: benseverndev-oss/goldencheck-action@v1
        with:
          files: "data/*.csv"
          fail-on: error
          config: goldencheck.yml
```

## With LLM Boost

```yaml
- uses: benseverndev-oss/goldencheck-action@v1
  with:
    files: "data/*.csv"
    llm-boost: true
    llm-provider: openai
  env:
    OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
```

## Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `files` | Yes | — | Glob pattern for data files |
| `fail-on` | No | `error` | Threshold: `error` or `warning` |
| `config` | No | — | Path to goldencheck.yml |
| `llm-boost` | No | `false` | Enable LLM enhancement |
| `llm-provider` | No | `anthropic` | LLM provider |
| `python-version` | No | `3.12` | Python version |
| `version` | No | latest | GoldenCheck version |

## Outputs

| Output | Description |
|--------|-------------|
| `errors` | Total error count |
| `warnings` | Total warning count |
| `health-grade` | Worst health grade |

## Repository

[benseverndev-oss/goldencheck-action](https://github.com/benseverndev-oss/goldencheck-action)
