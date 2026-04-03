---
title: Quick Start
layout: default
nav_order: 3
---

Get started with GoldenCheck in 60 seconds.

## Install

```bash
pip install goldencheck
```

## Scan a File

```bash
goldencheck data.csv
```

This launches the interactive TUI with all discovered issues.

For CLI-only output:

```bash
goldencheck data.csv --no-tui
```

## What You'll See

GoldenCheck runs 10 column profilers and 2 cross-column profilers, then reports findings by severity:

```
─── GoldenCheck Results ───
File: data.csv (10,000 rows × 12 columns)
Health: B (82)

ERROR   email        format_detection   6% malformed emails (600 rows)
ERROR   age          range_distribution  Values outside [0, 120]: -5, 999
WARN    status       pattern_consistency 3 case variants: active, Active, ACTIVE
WARN    signup_date  temporal_order      12 rows where signup_date > last_login
INFO    id           uniqueness          100% unique — likely primary key
```

## Pin Rules → Export → Validate

In the TUI:
1. Press **Space** to pin findings you want to enforce
2. Press **F2** to save to `goldencheck.yml`
3. Validate in CI:

```bash
goldencheck validate data.csv
# Exit code 0 = pass, 1 = fail
```

## With LLM Boost

Add LLM intelligence for ~$0.01 per scan:

```bash
export OPENAI_API_KEY=sk-...
goldencheck data.csv --llm-boost --llm-provider openai --no-tui
```

The LLM catches semantic issues profilers miss and reduces false positives.

## JSON Output (CI)

```bash
goldencheck data.csv --no-tui --json
```

## Python API

```python
from goldencheck.engine.scanner import scan_file
from goldencheck.engine.confidence import apply_confidence_downgrade

findings, profile = scan_file("data.csv")
findings = apply_confidence_downgrade(findings, llm_boost=False)

for f in findings:
    print(f"{f.severity.name}: [{f.column}] {f.message}")
```

## Jupyter / Colab

```python
from goldencheck.notebook import ScanResult

result = ScanResult(findings=findings, profile=profile)
result  # Rich HTML table in notebooks
```

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/benzsevern/goldencheck/blob/main/scripts/goldencheck_demo.ipynb)

## Baseline & Drift Detection

Track drift against a known-good dataset by creating a statistical baseline:

```bash
pip install goldencheck[baseline]

# Step 1: Build a baseline from clean data
goldencheck baseline data.csv
# Saves goldencheck_baseline.yaml

# Step 2: Scan new data — drift surfaces automatically
goldencheck scan new_data.csv
```

GoldenCheck runs 6 deep analysis techniques (statistical profiler, constraint miner, semantic type inferrer, correlation analyzer, pattern grammar inducer, confidence prior builder) and saves a human-readable YAML baseline. On every subsequent scan it checks 13 drift types — null rate changes, mean shifts, enum violations, semantic type changes, and more.

See [Deep Profiling Baseline]({% link baseline.md %}) for full documentation.

---

## Next Steps

- [CLI Reference]({% link cli.md %}) — all commands and flags
- [Profilers]({% link profilers.md %}) — what each check detects
- [Deep Profiling Baseline]({% link baseline.md %}) — learn-once drift detection
- [Configuration]({% link configuration.md %}) — `goldencheck.yml` reference
- [MCP Server]({% link mcp-server.md %}) — Claude Desktop integration
