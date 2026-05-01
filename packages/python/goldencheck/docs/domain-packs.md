---
title: Domain Packs
layout: default
nav_order: 8
---

Domain packs are YAML-based semantic type definitions tailored for specific industries. They improve detection accuracy by teaching GoldenCheck about domain-specific column types.

## Available Domains

| Domain | Types | Includes |
|--------|-------|----------|
| `healthcare` | NPI, ICD codes, insurance IDs, patient demographics, CPT, DRG, clinical notes |
| `finance` | Account numbers, routing numbers, CUSIP/ISIN, currency, transactions |
| `ecommerce` | SKUs, order IDs, tracking numbers, categories, shipping |

## Usage

```bash
goldencheck scan data.csv --domain healthcare
goldencheck scan data.csv --domain finance
goldencheck scan data.csv --domain ecommerce
```

## How It Works

Domain packs extend the base semantic types (`types.yaml`) with industry-specific type definitions. When you use `--domain healthcare`, GoldenCheck:

1. Loads base types (identifier, email, phone, etc.)
2. Loads healthcare types (NPI, ICD codes, insurance IDs, etc.)
3. Domain types take priority over base types for name matching
4. User custom types (`goldencheck_types.yaml`) override everything

## MCP Integration

Browse and install domains from Claude Desktop:

- `list_domains` — see all available domain packs
- `get_domain_info` — view types in a specific pack
- `install_domain` — download community domain packs

## Custom Types

Create your own `goldencheck_types.yaml` in your project:

```yaml
description: "My custom domain types"

types:
  internal_id:
    name_hints: ["internal_id", "corp_id"]
    value_signals:
      min_unique_pct: 0.95
    suppress: ["cardinality", "pattern_consistency"]
```

This file takes highest priority — it overrides both base and domain types.
