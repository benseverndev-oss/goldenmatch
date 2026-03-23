# Quick Start

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

## Next Steps

- [CLI Reference](CLI) — all commands and flags
- [Profilers](Profilers) — what each check detects
- [Configuration](Configuration) — `goldencheck.yml` reference
- [MCP Server](MCP-Server) — Claude Desktop integration
