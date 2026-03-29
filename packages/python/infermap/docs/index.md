---
layout: default
title: Home
nav_order: 1
---

# infermap

Inference-driven schema mapping engine. Map messy source columns to a known target schema -- accurately, explainably, and with zero config.

## Quick Start

```bash
pip install infermap
```

```python
import infermap

result = infermap.map("source.csv", "target.csv")
print(result.report())
```

## How It Works

infermap uses a weighted scorer pipeline:

1. **ExactScorer** -- exact column name match
2. **AliasScorer** -- synonym registry (email_addr -> email, tel -> phone)
3. **PatternTypeScorer** -- regex-based semantic type detection (email, phone, date, zip)
4. **ProfileScorer** -- statistical profile comparison (dtype, null rate, cardinality)
5. **FuzzyNameScorer** -- Jaro-Winkler name similarity

Each scorer independently evaluates every (source, target) pair. The engine combines scores with configurable weights, then applies the Hungarian algorithm for globally optimal 1:1 assignment.

## Links

- [PyPI](https://pypi.org/project/infermap/)
- [GitHub](https://github.com/benzsevern/infermap)
