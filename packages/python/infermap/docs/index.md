---
layout: default
title: Home
nav_order: 1
---

# infermap

Inference-driven schema mapping engine. Map messy source columns to a known target schema -- accurately, explainably, and with zero config.

[![PyPI](https://img.shields.io/pypi/v/infermap?color=d4a017)](https://pypi.org/project/infermap/)
[![CI](https://github.com/benzsevern/infermap/actions/workflows/test.yml/badge.svg)](https://github.com/benzsevern/infermap/actions/workflows/test.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](https://github.com/benzsevern/infermap/blob/main/LICENSE)

## Install

```bash
pip install infermap
```

## Quick Start

```python
import infermap

result = infermap.map("source.csv", "target.csv")
print(result.report())           # see what matched and why
remapped = result.apply(df)      # remap a DataFrame
result.to_config("mapping.yaml") # save for reuse
```

## CLI

```bash
infermap map source.csv target.csv
infermap inspect source.csv
infermap apply source.csv --config mapping.yaml --output remapped.csv
infermap validate source.csv --config mapping.yaml --strict --required email,phone
```

## How It Works

infermap runs a **weighted scorer pipeline** against every possible (source, target) column pair, then uses the Hungarian algorithm to find the globally optimal 1:1 assignment.

| Scorer | Weight | Signal |
|--------|--------|--------|
| ExactScorer | 1.0 | Exact column name match |
| AliasScorer | 0.95 | Known synonyms + [domain dictionaries](domain-dictionaries) |
| InitialismScorer | 0.75 | Abbreviation matching (assay_id ↔ ASSI) |
| PatternTypeScorer | 0.7 | Regex-based semantic type detection (email, phone, date, zip) |
| ProfileScorer | 0.5 | Statistical profile comparison (dtype, null rate, cardinality) |
| FuzzyNameScorer | 0.4 | Jaro-Winkler fuzzy name similarity (with prefix canonicalization) |

Each scorer returns a confidence score and human-readable reasoning. The engine combines them using a weighted average (with a minimum 2-contributor threshold), builds a cost matrix, and solves the optimal assignment.

## New in v0.3

- **[Domain Dictionaries](domain-dictionaries)** -- `MapEngine(domains=["healthcare"])` loads curated alias sets for healthcare, finance, and ecommerce.
- **[Confidence Calibration](benchmark#public-api-additions-in-v02)** -- post-assignment calibration transforms raw scores into calibrated probabilities (ECE 0.46 → 0.005).
- **[Accuracy Benchmark](benchmark)** -- 162-case cross-language benchmark gating every release (F1 0.84).
- **InitialismScorer** -- matches abbreviation-style column names like `assay_id ↔ ASSI`.
- **Common-prefix canonicalization** -- strips schema-wide prefixes like `prospect_` before fuzzy matching.

## Links

- [GitHub](https://github.com/benzsevern/infermap)
- [PyPI](https://pypi.org/project/infermap/)
- [npm](https://www.npmjs.com/package/infermap)
- [Open in Colab](https://colab.research.google.com/github/benzsevern/infermap/blob/main/scripts/infermap_demo.ipynb)
