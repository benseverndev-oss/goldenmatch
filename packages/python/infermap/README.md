<h1 align="center">infermap</h1>

<p align="center"><strong>Inference-driven schema mapping engine.</strong><br>
Map messy source columns to a known target schema — accurately, explainably, with zero config.<br>
Built by <a href="https://bensevern.dev">Ben Severn</a>.</p>

<p align="center">
  <a href="https://pypi.org/project/infermap/"><img src="https://img.shields.io/pypi/v/infermap?color=d4a017&label=PyPI" alt="PyPI"></a>
  <a href="https://www.npmjs.com/package/infermap"><img src="https://img.shields.io/npm/v/infermap?color=cb3837&label=npm" alt="npm"></a>
  <a href="https://pypi.org/project/infermap/"><img src="https://img.shields.io/pypi/dm/infermap?color=d4a017&label=PyPI%20downloads" alt="PyPI downloads"></a>
  <a href="https://www.npmjs.com/package/infermap"><img src="https://img.shields.io/npm/dw/infermap?color=cb3837&label=npm%20downloads" alt="npm downloads"></a>
  <a href="https://github.com/benzsevern/infermap/actions/workflows/test.yml"><img src="https://github.com/benzsevern/infermap/actions/workflows/test.yml/badge.svg?branch=main" alt="CI"></a>
</p>

<p align="center">
  <a href="https://python.org"><img src="https://img.shields.io/badge/python-3.11%2B-blue?logo=python&logoColor=white" alt="Python 3.11+"></a>
  <a href="https://nodejs.org"><img src="https://img.shields.io/badge/node-20%2B-339933?logo=node.js&logoColor=white" alt="Node 20+"></a>
  <a href="https://www.typescriptlang.org/"><img src="https://img.shields.io/badge/typescript-strict-3178c6?logo=typescript&logoColor=white" alt="TypeScript"></a>
  <a href="https://nextjs.org/docs/app/api-reference/edge"><img src="https://img.shields.io/badge/edge%20runtime-compatible-000000?logo=vercel&logoColor=white" alt="Edge runtime"></a>
  <a href="https://github.com/benzsevern/infermap/wiki/Python-vs-TypeScript"><img src="https://img.shields.io/badge/parity-Python%20%E2%86%94%20TypeScript-d4a017" alt="Parity"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/benzsevern/infermap?color=green" alt="License: MIT"></a>
</p>

<p align="center">
  <a href="https://github.com/benzsevern/infermap/wiki">📖 Wiki</a> ·
  <a href="https://benzsevern.github.io/infermap/">🌐 Docs</a> ·
  <a href="examples/">🧪 Examples</a> ·
  <a href="https://github.com/benzsevern/infermap/discussions">💬 Discussions</a> ·
  <a href="https://github.com/benzsevern/infermap/issues">🐛 Issues</a>
</p>

---

`infermap` is a schema-mapping engine. Give it any two field collections (CSVs, DataFrames, database tables, in-memory records) and it figures out which source field corresponds to which target field, with confidence scores and human-readable reasoning. Available as a **Python package on PyPI** and a **TypeScript package on npm**, with mapping decisions verified bit-for-bit by a shared golden-test parity suite.

## Table of contents

- [Install](#install)
- [Quick start](#quick-start)
- [How it works](#how-it-works)
- [Features](#features)
- [Which package should I use?](#which-package-should-i-use)
- [Custom scorers](#custom-scorers)
- [CLI examples](#cli-examples)
- [Config reference](#config-reference)
- [Documentation](#documentation)
- [License](#license)

## Install

### Python

```bash
pip install infermap
```

Optional database extras:

```bash
pip install infermap[postgres]   # psycopg2-binary
pip install infermap[mysql]      # mysql-connector-python
pip install infermap[duckdb]     # duckdb
pip install infermap[all]        # all extras
```

### TypeScript / Next.js

```bash
npm install infermap
```

Zero runtime dependencies in the core entrypoint. Compatible with Next.js Server Components, Route Handlers, Server Actions, and the Edge Runtime out of the box. See the [package README](./packages/infermap-js/README.md) for the full reference.

## Quick start

### Python

```python
import infermap

# Map a CRM export CSV to a canonical customer schema
result = infermap.map("crm_export.csv", "canonical_customers.csv")

for m in result.mappings:
    print(f"{m.source} -> {m.target}  ({m.confidence:.0%})")
# fname -> first_name  (97%)
# lname -> last_name   (95%)
# email_addr -> email  (91%)

# Apply mappings to rename DataFrame columns
import polars as pl
df = pl.read_csv("crm_export.csv")
renamed = result.apply(df)

# Save mappings to a reusable config file
result.to_config("my_mapping.yaml")

# Reload later — no re-inference needed
saved = infermap.from_config("my_mapping.yaml")
```

### TypeScript

```ts
import { map } from "infermap";

const crm = [
  { fname: "John", lname: "Doe", email_addr: "j@d.co" },
  { fname: "Jane", lname: "Smith", email_addr: "j@s.co" },
];

const canonical = [
  { first_name: "", last_name: "", email: "" },
];

const result = map({ records: crm }, { records: canonical });

for (const m of result.mappings) {
  console.log(`${m.source} → ${m.target}  (${m.confidence.toFixed(2)})`);
}
// fname       → first_name  (0.44)
// lname       → last_name   (0.48)
// email_addr  → email       (0.69)
```

For Next.js, drop it directly into a Route Handler — works on Edge Runtime with zero config:

```ts
// app/api/infer/route.ts
import { map } from "infermap";
export const runtime = "edge";

export async function POST(req: Request) {
  const { sourceCsv, targetCsv } = await req.json();
  const result = map({ csvText: sourceCsv }, { csvText: targetCsv });
  return Response.json(result);
}
```

## How it works

Each field pair runs through a pipeline of **7 scorers**. Each scorer returns a score in `[0.0, 1.0]` or abstains (`None`/`null`). The engine combines scores via weighted average (requiring at least 2 contributors), then uses the **Hungarian algorithm** for optimal one-to-one assignment.

| Scorer | Weight | What it detects |
|---|---|---|
| **ExactScorer** | 1.0 | Case-insensitive exact name match |
| **AliasScorer** | 0.95 | Known field aliases (`fname` ↔ `first_name`, `tel` ↔ `phone`) + domain dictionaries |
| **InitialismScorer** | 0.75 | Abbreviation-style names (`assay_id` ↔ `ASSI`, `confidence_score` ↔ `CONSC`) |
| **PatternTypeScorer** | 0.7 | Semantic type from sample values — email, date_iso, phone, uuid, url, zip, currency |
| **ProfileScorer** | 0.5 | Statistical profile similarity — dtype, null rate, unique rate, length, cardinality |
| **FuzzyNameScorer** | 0.4 | Jaro-Winkler similarity on normalized field names (with common-prefix canonicalization) |
| **LLMScorer** | 0.8 | Pluggable LLM-backed scorer (stubbed by default) |

The engine also applies **common-prefix canonicalization** — automatically stripping schema-wide prefixes like `prospect_` so that `City` vs `prospect_City` is compared as `City` vs `City`. And **optional confidence calibration** transforms raw scores into calibrated probabilities post-assignment (ECE from 0.46 to 0.005 on real-world data).

[Read the full architecture →](https://github.com/benzsevern/infermap/wiki/Architecture)

## Features

| | Python | TypeScript |
|---|---|---|
| 7 built-in scorers | ✅ | ✅ |
| Hungarian assignment | ✅ (scipy) | ✅ (vendored) |
| Custom scorers | `@infermap.scorer` | `defineScorer()` |
| Domain dictionaries | ✅ (YAML) | ✅ (inlined) |
| Confidence calibration | ✅ (Identity/Isotonic/Platt) | ✅ |
| Score matrix inspection | ✅ | ✅ |
| In-memory data | Polars, Pandas, `list[dict]` | `Array<Record>` |
| File providers | CSV, Parquet, XLSX | CSV, JSON |
| Schema definition files | YAML + JSON | JSON |
| Database providers | SQLite, Postgres, DuckDB | SQLite, Postgres, DuckDB |
| Engine config | YAML | JSON |
| Saved mapping format | YAML | JSON |
| CLI | ✅ (Typer) | ✅ (`node:util`) |
| Apply to DataFrame | ✅ | ❌ (CSV rewrite via CLI) |
| Edge-runtime compatible | ❌ | ✅ |
| Zero runtime deps | n/a | ✅ |
| Accuracy benchmark | ✅ (162 cases, F1 0.84) | ✅ (parity within 0.0005) |

[Full feature parity matrix →](https://github.com/benzsevern/infermap/wiki/Python-vs-TypeScript)

## Which package should I use?

| If you are… | Use |
|---|---|
| Building a Python data pipeline or notebook | **Python** |
| Building a Next.js app, Node service, or browser tool | **TypeScript** |
| Running mapping in a serverless edge function | **TypeScript** (zero Node built-ins) |
| Doing ad-hoc CSV exploration on the command line | **Python CLI** has more features; **TS CLI** is leaner |
| Both — Python backend + Next.js admin UI | **Both** — outputs are interoperable via the JSON config format |

## What's new in v0.3

**+18.3pp F1 on real-world data** from four compounding improvements:

```
v0.2 baseline    F1 0.657
+ min_conf 0.2   F1 0.765  (+10.8pp — empirically tuned threshold)
+ prefix-strip   F1 0.821  (+5.6pp  — City vs prospect_City now works)
+ InitialismScorer F1 0.840 (+1.9pp  — ASSI, CONSC, RELATIT now work)
```

New features:
- **Domain dictionaries** — `MapEngine(domains=["healthcare"])` loads curated aliases for your domain. Ships: `generic` (default), `healthcare`, `finance`, `ecommerce`. See [`examples/09_domain_dictionaries.py`](./examples/09_domain_dictionaries.py).
- **Confidence calibration** — `MapEngine(calibrator=cal)` transforms raw scores into calibrated probabilities. Ships: `IsotonicCalibrator`, `PlattCalibrator`. Valentine ECE: 0.46 → 0.005. See [`examples/10_calibration.py`](./examples/10_calibration.py).
- **InitialismScorer** — matches abbreviation-style column names (`assay_id ↔ ASSI`). ChEMBL F1: 0.524 → 0.819.
- **Common-prefix canonicalization** — automatically strips `prospect_`, `assays_`, etc. before fuzzy matching.
- **Valentine corpus** — 82 real-world schema-matching cases from the Valentine benchmark for accuracy testing.
- **Full TypeScript parity** — all new features ported. 186 TS tests. Benchmark F1 within 0.0005 of Python.

## Custom scorers

### Python

```python
import infermap
from infermap.types import FieldInfo, ScorerResult

@infermap.scorer("prefix_scorer", weight=0.8)
def prefix_scorer(source: FieldInfo, target: FieldInfo) -> ScorerResult | None:
    if source.name[:3].lower() != target.name[:3].lower():
        return None
    return ScorerResult(score=0.85, reasoning=f"Shared prefix '{source.name[:3]}'")

from infermap.engine import MapEngine
from infermap.scorers import default_scorers

engine = MapEngine(scorers=[*default_scorers(), prefix_scorer])
```

### TypeScript

```ts
import { MapEngine, defaultScorers, defineScorer, makeScorerResult } from "infermap";

const prefixScorer = defineScorer(
  "prefix_scorer",
  (source, target) => {
    if (source.name.slice(0, 3).toLowerCase() !== target.name.slice(0, 3).toLowerCase()) {
      return null;
    }
    return makeScorerResult(0.85, `Shared prefix '${source.name.slice(0, 3)}'`);
  },
  0.8 // weight
);

const engine = new MapEngine({
  scorers: [...defaultScorers(), prefixScorer],
});
```

## CLI examples

The CLI works the same way in both packages:

```bash
# Map two files and print a report
infermap map crm_export.csv canonical_customers.csv

# Map and save the config (Python: --save, TS: -o)
infermap map crm_export.csv canonical_customers.csv -o mapping.json

# Apply a saved mapping to rename columns
infermap apply crm_export.csv --config mapping.json --output renamed.csv

# Inspect the schema of a file or DB table
infermap inspect crm_export.csv
infermap inspect "sqlite:///mydb.db" --table customers

# Validate a saved config against a source
infermap validate crm_export.csv --config mapping.json --required email,id --strict
```

## Config reference

Both packages accept an engine config (scorer weight overrides + alias extensions). Python uses YAML, TypeScript uses JSON; the **shape is identical**.

```yaml
# Python: infermap.yaml
domains:
  - healthcare
  - finance
scorers:
  LLMScorer:
    enabled: false
  FuzzyNameScorer:
    weight: 0.3
aliases:
  order_id:
    - order_num
    - ord_no
```

```json
// TypeScript: infermap.config.json
{
  "scorers": {
    "LLMScorer":       { "enabled": false },
    "FuzzyNameScorer": { "weight": 0.3 }
  },
  "aliases": {
    "order_id": ["order_num", "ord_no"]
  }
}
```

See [`infermap.yaml.example`](./infermap.yaml.example) for a full annotated reference.

## Documentation

- 📖 **[Wiki](https://github.com/benzsevern/infermap/wiki)** — full reference for both languages
  - [Getting Started](https://github.com/benzsevern/infermap/wiki/Getting-Started)
  - [Python API](https://github.com/benzsevern/infermap/wiki/Python-API)
  - [TypeScript API](https://github.com/benzsevern/infermap/wiki/TypeScript-API)
  - [Python vs TypeScript](https://github.com/benzsevern/infermap/wiki/Python-vs-TypeScript) — migration guide
  - [Scorers](https://github.com/benzsevern/infermap/wiki/Scorers)
  - [Architecture](https://github.com/benzsevern/infermap/wiki/Architecture)
  - [FAQ](https://github.com/benzsevern/infermap/wiki/FAQ)
- 🌐 **[Documentation site](https://benzsevern.github.io/infermap/)**
- 🧪 **Examples**
  - [Python examples](./examples/) — 10 numbered scripts covering basic mapping, databases, custom scorers, config, domain dictionaries, calibration, and score-matrix introspection
  - [TypeScript examples](./examples/typescript/) — basic mapping, Next.js Edge Runtime, custom scorer, databases, domain dictionaries, save/reuse
- 📓 **[Open in Colab](https://colab.research.google.com/github/benzsevern/infermap/blob/main/scripts/infermap_demo.ipynb)** — Python notebook
- 💬 **[GitHub Discussions](https://github.com/benzsevern/infermap/discussions)**
- 🐛 **[Issue tracker](https://github.com/benzsevern/infermap/issues)**

## Author

[Ben Severn](https://bensevern.dev)

## License

[MIT](LICENSE)
