---
title: Jupyter & Colab
layout: default
nav_order: 17
---

GoldenCheck renders rich HTML tables in Jupyter notebooks and Google Colab.

## Quick Start

```python
from goldencheck.engine.scanner import scan_file
from goldencheck.engine.confidence import apply_confidence_downgrade
from goldencheck.notebook import ScanResult

findings, profile = scan_file("data.csv")
findings = apply_confidence_downgrade(findings, llm_boost=False)

# Rich HTML display
ScanResult(findings=findings, profile=profile)
```

## Colab Demo

Try GoldenCheck without installing anything:

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/benzsevern/goldencheck/blob/main/scripts/goldencheck_demo.ipynb)

The demo notebook creates sample data with planted issues, scans it, and shows findings with rich HTML formatting.

## Display Components

### `ScanResult`

The main wrapper for notebook display. Combines profile + findings into a single rich view.

```python
from goldencheck.notebook import ScanResult

result = ScanResult(findings=findings, profile=profile)
result  # displays HTML in notebook
```

Shows:
- File info (path, rows, columns)
- Health badge (A-F with color)
- Column statistics table
- Findings table with severity colors, confidence indicators, and sample values

### Individual Objects

`Finding` and `DatasetProfile` also have `_repr_html_()` methods:

```python
# Display a single finding
findings[0]  # renders as colored HTML badge

# Display the profile
profile  # renders as column statistics table with health score
```

### `findings_to_html()` / `profile_to_html()`

For embedding in custom HTML:

```python
from goldencheck.notebook import findings_to_html, profile_to_html

html = findings_to_html(findings)
html = profile_to_html(profile, findings=findings)
```

## Confidence Indicators

Findings display confidence as:
- **H** — High (≥0.8) — strong detection
- **M** — Medium (0.5-0.79) — moderate confidence
- **L** — Low (<0.5) — consider using `--llm-boost`

LLM-sourced findings show **[LLM]** tag.

## Severity Colors

| Severity | Color |
|----------|-------|
| ERROR | Red |
| WARNING | Orange |
| INFO | Blue |

## With LLM Boost

```python
from goldencheck.engine.scanner import scan_file_with_llm

# Requires OPENAI_API_KEY or ANTHROPIC_API_KEY env var
findings, profile = scan_file_with_llm("data.csv", provider="openai")
ScanResult(findings=findings, profile=profile)
```
