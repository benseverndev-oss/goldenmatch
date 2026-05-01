<!-- mcp-name: io.github.benzsevern/goldencheck -->
# GoldenCheck

Data validation that discovers rules from your data so you don't have to write them.
Built by [Ben Severn](https://bensevern.dev).

[![PyPI](https://img.shields.io/pypi/v/goldencheck?logo=pypi&logoColor=white&label=PyPI&color=d4a017)](https://pypi.org/project/goldencheck/)
[![npm](https://img.shields.io/npm/v/goldencheck?logo=npm&logoColor=white&label=npm&color=cb3837)](https://www.npmjs.com/package/goldencheck)
[![CI](https://img.shields.io/github/actions/workflow/status/benzsevern/goldencheck/test.yml?logo=github&label=CI)](https://github.com/benzsevern/goldencheck/actions/workflows/test.yml)
[![codecov](https://img.shields.io/codecov/c/gh/benzsevern/goldencheck?logo=codecov&logoColor=white)](https://codecov.io/gh/benzsevern/goldencheck)
[![PyPI Downloads](https://img.shields.io/pypi/dm/goldencheck?logo=python&logoColor=white&label=PyPI%20downloads&color=3776ab)](https://pepy.tech/project/goldencheck)
[![npm Downloads](https://img.shields.io/npm/dm/goldencheck?logo=npm&logoColor=white&label=npm%20downloads&color=cb3837)](https://www.npmjs.com/package/goldencheck)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776ab?logo=python&logoColor=white)](https://python.org)
[![Node 20+](https://img.shields.io/badge/node-20%2B-5fa04e?logo=nodedotjs&logoColor=white)](https://nodejs.org)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.4%2B-3178c6?logo=typescript&logoColor=white)](https://typescriptlang.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green?logo=opensourceinitiative&logoColor=white)](LICENSE)
[![DQBench](https://img.shields.io/badge/DQBench-88.40-gold?logo=data:image/svg%2bxml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAxNiAxNiI+PHRleHQgeD0iMCIgeT0iMTQiIGZvbnQtc2l6ZT0iMTQiPuKtkTwvdGV4dD48L3N2Zz4=)](https://github.com/benzsevern/dqbench)
[![Docs](https://img.shields.io/badge/docs-benzsevern.github.io-d4a017?logo=github&logoColor=white)](https://benzsevern.github.io/goldencheck/)
[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/benzsevern/goldencheck/blob/main/scripts/goldencheck_demo.ipynb)

> Every competitor makes you write rules first. GoldenCheck flips it: **validate first, keep the rules you care about.**

## Why GoldenCheck?

|  | GoldenCheck | Great Expectations | Pandera | Pointblank |
|---|---|---|---|---|
| Rules | **Discovered from data** | Written by hand | Written by hand | Written by hand |
| Config | **Zero to start** | Heavy YAML/Python setup | Decorators/schemas | YAML/Python |
| Interface | **CLI + interactive TUI** | HTML reports | Exceptions | HTML/notebook |
| Learning curve | **One command** | Hours/days | Moderate | Moderate |
| LLM enhancement | **Yes ($0.01/scan)** | No | No | No |
| Fix suggestions | Yes, in TUI | No | No | No |
| Confidence scoring | Yes (H/M/L per finding) | No | No | No |
| DQBench Score | **88.40** | 21.68 (best-effort) | 32.51 (best-effort) | 6.94 (auto) |

## Install

```bash
pip install goldencheck
```

With LLM boost support:

```bash
pip install goldencheck[llm]
```

With deep profiling & baseline support (scipy, numpy):

```bash
pip install goldencheck[baseline]
```

With semantic type inference for baseline (sentence-transformers):

```bash
pip install goldencheck[baseline,semantic]
```

### JavaScript / TypeScript

```bash
npm install goldencheck
```

**Edge-safe core** (browsers, Cloudflare Workers, Vercel Edge):
```typescript
import { scanData, TabularData } from "goldencheck/core";
```

**Node.js** (file reading, CLI, MCP):
```typescript
import { readFile, scanData } from "goldencheck/node";
```

## Quick Start

```bash
# Scan a file — discovers issues, launches interactive TUI
goldencheck data.csv

# CLI-only output (no TUI)
goldencheck data.csv --no-tui

# With LLM enhancement (requires API key)
goldencheck data.csv --llm-boost --no-tui

# Validate against saved rules (for CI/pipelines)
goldencheck validate data.csv

# JSON output for CI integration
goldencheck data.csv --no-tui --json

# Learn baseline (one-time, deep analysis)
goldencheck baseline data.csv

# Scan with drift detection (fast, uses saved baseline)
goldencheck scan new_data.csv
```

## TypeScript Quick Start

```typescript
// Scan an array of records (edge-safe — works anywhere)
import { scanData, TabularData, Severity } from "goldencheck";

const data = new TabularData([
  { id: 1, email: "alice@example.com", age: 30, status: "active" },
  { id: 2, email: "bob@test.com", age: -5, status: "inactive" },
  { id: 3, email: "not-an-email", age: 25, status: "active" },
]);

const { findings, profile } = scanData(data);
for (const f of findings) {
  console.log(`[${f.severity === Severity.ERROR ? "ERROR" : "WARNING"}] ${f.column}: ${f.message}`);
}
```

```typescript
// Scan a CSV file (Node.js)
import { readFile, scanData, applyConfidenceDowngrade, healthScore } from "goldencheck/node";

const data = readFile("data.csv");
const result = scanData(data, { domain: "healthcare" });
const findings = applyConfidenceDowngrade(result.findings, false);

// Health score
const byCol = {};
for (const f of findings) {
  if (f.severity >= 2) {
    byCol[f.column] ??= { errors: 0, warnings: 0 };
    byCol[f.column][f.severity === 3 ? "errors" : "warnings"]++;
  }
}
const { grade, points } = healthScore(byCol);
console.log(`Health: ${grade} (${points}/100)`);
```

```typescript
// Validate against pinned rules
import { readFile, scanData, validateConfig, validateData } from "goldencheck/node";
import { readFileSync } from "node:fs";
import YAML from "yaml";

const config = validateConfig(YAML.parse(readFileSync("goldencheck.yml", "utf-8")));
const data = readFile("data.csv");
const findings = validateData(data, config);
```

```typescript
// Create baseline and detect drift
import { readFile, createBaseline, serializeBaseline, scanData } from "goldencheck/node";
import { runDriftChecks, deserializeBaseline } from "goldencheck";
import { writeFileSync, readFileSync } from "node:fs";

// Learn baseline
const data = readFile("reference.csv");
const baseline = createBaseline(data);
writeFileSync("baseline.json", serializeBaseline(baseline));

// Later: detect drift
const newData = readFile("production.csv");
const saved = deserializeBaseline(readFileSync("baseline.json", "utf-8"));
const driftFindings = runDriftChecks(newData, saved);
```

```typescript
// LLM-enhanced scanning (edge-safe)
import { scanData, TabularData, callLlm, parseLlmResponse, mergeLlmFindings, buildSampleBlocks } from "goldencheck";

const data = new TabularData(records);
const result = scanData(data, { returnSample: true });
const blocks = buildSampleBlocks(result.sample, result.findings);
const { text } = await callLlm("anthropic", JSON.stringify(blocks));
const llmResponse = parseLlmResponse(text);
if (llmResponse) {
  const enhanced = mergeLlmFindings(result.findings, llmResponse);
}
```

## How It Works

```
1. SCAN     →  goldencheck data.csv
                GoldenCheck profiles your data and discovers what "healthy" looks like

2. REVIEW   →  Interactive TUI shows findings sorted by severity
                Each finding has: description, affected rows, sample values

3. PIN      →  Press Space to promote findings into permanent rules
                Dismiss false positives — they won't come back

4. EXPORT   →  Press F2 to save rules to goldencheck.yml
                Human-readable YAML with your pinned rules

5. VALIDATE →  goldencheck validate data.csv
                Enforce rules in CI with exit codes (0 = pass, 1 = fail)
```

## What It Detects

### Column-Level Profilers

| Profiler | What It Catches | Example |
|----------|----------------|---------|
| **Type inference** | String columns that are actually numeric | "Column `age` is string but 98% are integer" |
| **Nullability** | Required vs. optional columns | "0 nulls across 50k rows — likely required" |
| **Uniqueness** | Primary key candidates, near-duplicates | "100% unique — likely primary key" |
| **Format detection** | Emails, phones, URLs, dates | "94% email format, 6% malformed" |
| **Range & distribution** | Outliers, min/max bounds | "3 rows have values >10,000" |
| **Cardinality** | Low-cardinality enum suggestions | "4 unique values — possible enum" |
| **Pattern consistency** | Mixed formats within a column | "3 phone formats detected" |

### Cross-Column Profilers

| Profiler | What It Catches |
|----------|----------------|
| **Temporal ordering** | start_date > end_date violations |
| **Null correlation** | Columns that are null together (e.g., address + city + zip) |
| **Numeric cross-column** | value > max violations (e.g., claim_amount > policy_max) |
| **Age vs DOB** | Age column doesn't match calculated age from date_of_birth |

### Baseline Deep Profiling & Drift Detection

Run `goldencheck baseline` once to build a statistical profile of healthy data. On every subsequent scan, GoldenCheck compares the new data against the saved baseline and reports drift across 13 check types:

| Check Type | What It Catches |
|------------|----------------|
| `distribution_drift` | Value distribution has shifted significantly |
| `entropy_drift` | Entropy of column values has changed |
| `bound_violation` | Values exceed historical min/max bounds |
| `benford_drift` | Leading-digit distribution deviates from Benford's Law |
| `fd_violation` | Functional dependency between columns is broken |
| `key_uniqueness_loss` | Previously unique column now has duplicates |
| `temporal_order_drift` | Historical column ordering constraint violated |
| `type_drift` | Dominant semantic type of column has changed |
| `correlation_break` | Previously correlated columns are no longer correlated |
| `new_correlation` | New unexpected correlation appeared |
| `pattern_drift` | Value format/pattern distribution has shifted |
| `new_pattern` | New structural patterns appeared in a column |

The baseline is built using 6 techniques: statistical profiler (distributions, Benford's Law, entropy), constraint miner (functional dependencies, temporal orders), semantic type inferrer (embeddings + keywords), correlation analyzer (Pearson, Cramér's V), pattern grammar inducer, and confidence prior builder.

## Domain Packs

Improve detection accuracy with domain-specific type definitions:

```bash
goldencheck scan data.csv --domain healthcare   # NPI, ICD, insurance, patient types
goldencheck scan data.csv --domain finance      # accounts, routing, CUSIP, transactions
goldencheck scan data.csv --domain ecommerce    # SKUs, orders, tracking, products
```

Domain packs add semantic types that reduce false positives and improve classification for industry-specific data.

## Schema Diff

Compare two versions of a data file:

```bash
goldencheck diff data.csv                  # compare against git HEAD
goldencheck diff old.csv new.csv           # compare two files
goldencheck diff data.csv --ref main       # compare against a branch
```

## Auto-Fix

Apply automated fixes to clean your data:

```bash
goldencheck fix data.csv                          # safe: trim, normalize, fix encoding
goldencheck fix data.csv --mode moderate          # + standardize case
goldencheck fix data.csv --mode aggressive --force # + coerce types
goldencheck fix data.csv --dry-run                # preview changes
```

## Watch Mode

Continuously monitor a directory for data quality:

```bash
goldencheck watch data/ --interval 30        # re-scan every 30s
goldencheck watch data/ --exit-on error      # CI mode: fail on first error
```

## REST API

Run GoldenCheck as a microservice:

```bash
goldencheck serve --port 8000

# Scan via file upload
curl -X POST http://localhost:8000/scan --data-binary @data.csv

# Scan via URL
curl -X POST http://localhost:8000/scan/url -d '{"url": "https://example.com/data.csv"}'
```

## Database Scanning

Scan tables directly — no CSV export needed:

```bash
pip install goldencheck[db]
goldencheck scan-db "postgresql://user:pass@host/db" --table orders
goldencheck scan-db "snowflake://..." --query "SELECT * FROM orders WHERE date > '2024-01-01'"
```

## Scheduled Runs

Cron-like scheduling with webhook notifications:

```bash
goldencheck schedule data/*.csv --interval hourly --webhook https://hooks.slack.com/...
goldencheck schedule data/*.csv --interval daily --notify-on grade-drop
```

## LLM Boost

Add `--llm-boost` to enhance profiler findings with LLM intelligence. The LLM receives a representative sample of your data and:

1. **Finds issues profilers miss** — semantic understanding (e.g., "12345" in a name column)
2. **Upgrades severity** — knows "emails should be required" even if the profiler only says "INFO"
3. **Discovers relationships** — identifies temporal ordering between columns like `signup_date` and `last_login`
4. **Downgrades false positives** — "mixed phone formats are common, not an error"

```bash
# Using OpenAI
export OPENAI_API_KEY=sk-...
goldencheck data.csv --llm-boost --llm-provider openai --no-tui

# Using Anthropic
export ANTHROPIC_API_KEY=sk-ant-...
goldencheck data.csv --llm-boost --no-tui
```

**Cost:** ~$0.01 per scan (one API call with representative samples, not per-row).

**Budget control:**
```bash
export GOLDENCHECK_LLM_BUDGET=0.50  # max spend per scan in USD
```

## Configuration (goldencheck.yml)

```yaml
version: 1

settings:
  sample_size: 100000
  fail_on: error

columns:
  email:
    type: string
    required: true
    format: email
    unique: true

  age:
    type: integer
    range: [0, 120]

  status:
    type: string
    enum: [active, inactive, pending, closed]

relations:
  - type: temporal_order
    columns: [start_date, end_date]

ignore:
  - column: notes
    check: nullability
```

Only pinned rules appear in this file — not every finding. The `ignore` list prevents dismissed findings from reappearing.

## CLI Reference

| Command | Description |
|---------|-------------|
| `goldencheck <file>` | Scan and launch TUI |
| `goldencheck scan <file>` | Explicit scan (supports `--smart`, `--guided`) |
| `goldencheck validate <file>` | Validate against goldencheck.yml |
| `goldencheck review <file>` | Scan + validate, launch TUI |
| `goldencheck init <file>` | Interactive setup wizard (scan → config → CI) |
| `goldencheck diff <file> [file2]` | Compare two files or against git HEAD |
| `goldencheck watch <dir>` | Poll directory, re-scan on change |
| `goldencheck fix <file>` | Auto-fix data quality issues |
| `goldencheck baseline <file>` | Deep-profile data and save statistical baseline to YAML |
| `goldencheck learn <file>` | Generate LLM validation rules |
| `goldencheck history` | Show scan history and trends |
| `goldencheck serve` | Start REST API server |
| `goldencheck scan-db <conn>` | Scan a database table directly |
| `goldencheck schedule <files>` | Run scans on a cron schedule |
| `goldencheck mcp-serve` | Start MCP server (19 tools) |

### Flags

| Flag | Description |
|------|-------------|
| `--no-tui` | Print results to console |
| `--json` | JSON output |
| `--fail-on <level>` | Exit 1 on severity: `error` or `warning` |
| `--domain <name>` | Domain pack: `healthcare`, `finance`, `ecommerce` |
| `--llm-boost` | Enable LLM enhancement |
| `--llm-provider <name>` | LLM provider: `anthropic` (default) or `openai` |
| `--mode <level>` | Fix mode: `safe`, `moderate`, `aggressive` |
| `--smart` | Auto-triage: pin high-confidence, dismiss low |
| `--guided` | Walk through findings one-by-one |
| `--webhook <url>` | POST findings to Slack/PagerDuty/any URL |
| `--notify-on <trigger>` | Webhook trigger: `grade-drop`, `any-error`, `any-warning` |
| `--baseline <path>` | Path to baseline YAML for drift detection |
| `--no-baseline` | Skip auto-discovery of `goldencheck_baseline.yaml` |
| `--skip <technique>` | Skip a baseline technique (can repeat) |
| `--update` | Update existing baseline instead of overwriting |
| `-o <path>` | Output path for baseline file (default: `goldencheck_baseline.yaml`) |
| `--version` | Show version |

## TypeScript CLI

```bash
npx goldencheck-js scan data.csv --json
npx goldencheck-js scan data.csv --domain healthcare
npx goldencheck-js health-score data.csv
npx goldencheck-js profile data.csv
npx goldencheck-js validate data.csv --config goldencheck.yml
npx goldencheck-js baseline data.csv --output baseline.json
npx goldencheck-js fix data.csv --mode safe
npx goldencheck-js diff old.csv new.csv
npx goldencheck-js demo
```

## TypeScript Architecture

```
goldencheck (npm)
├── goldencheck/core    # Edge-safe: browsers, Workers, Edge Runtime
│   ├── types           # Finding, Severity, DatasetProfile, Config types
│   ├── data            # TabularData — zero-dep columnar abstraction
│   ├── profilers       # 10 column profilers + 4 relation profilers
│   ├── semantic        # Type classifier, suppression, 3 domain packs
│   ├── engine          # Scanner, confidence, validator, triage, differ, fixer
│   ├── baseline        # Statistical profiling, constraints, correlation, patterns
│   ├── drift           # 13 drift checks against saved baseline
│   ├── llm             # Anthropic + OpenAI via fetch(), merger, budget
│   ├── agent           # Strategy, handoff, review queue
│   └── reporters       # JSON, CI
└── goldencheck/node    # Node.js >= 20
    ├── reader          # CSV, Parquet (via nodejs-polars)
    ├── mcp             # MCP server (7 tools)
    ├── a2a             # Agent-to-Agent HTTP server
    ├── tui             # ANSI terminal output
    ├── db-scanner      # Postgres, MySQL, SQLite
    └── watcher         # Directory polling
```

## Benchmarks

### Speed

| Dataset | Time | Throughput |
|---------|------|------------|
| 1K rows | 0.05s | 19K rows/sec |
| 10K rows | 0.23s | 43K rows/sec |
| 100K rows | 2.29s | 44K rows/sec |
| **1M rows** | **2.07s** | **482K rows/sec** |

### DQBench v1.0 — Head-to-Head

| Tool | Mode | DQBench Score |
|------|------|---------------|
| **GoldenCheck** | **zero-config** | **88.40** |
| Pandera | best-effort rules | 32.51 |
| Soda Core | best-effort rules | 22.36 |
| Great Expectations | best-effort rules | 21.68 |

> GoldenCheck's zero-config discovery outperforms every competitor — even when they have hand-written rules.

Run the benchmark yourself:
```bash
pip install dqbench goldencheck
dqbench run goldencheck
```

### Detection Accuracy

| Mode | Column Recall | Cost |
|------|--------------|------|
| Profiler-only (v0.1.0) | 87% | $0 |
| Profiler-only (v0.2.0 with confidence) | **100%** | $0 |
| With LLM Boost | **100%** | ~$0.003-0.01 |

Tested on a custom benchmark with 341 planted data quality issues across 9 categories.

> v0.2.0 improvements: minority wrong-type detection, range profiler chaining, broader temporal heuristics, and confidence scoring pushed profiler-only recall from 87% to 100%.

### Raha Benchmark Datasets

| Dataset | Column Recall |
|---------|--------------|
| Flights (2,376 rows) | **100%** (4/4 columns) |
| Beers (2,410 rows) | **80%** (4/5 columns) |

## Tech Stack

| Dependency | Purpose |
|-----------|---------|
| [Polars](https://pola.rs/) | All data operations |
| [Typer](https://typer.tiangolo.com/) | CLI framework |
| [Textual](https://textual.textualize.io/) | Interactive TUI |
| [Rich](https://rich.readthedocs.io/) | CLI output formatting |
| [Pydantic 2](https://docs.pydantic.dev/) | Config validation |

**Optional:** [Anthropic SDK](https://docs.anthropic.com/) / [OpenAI SDK](https://platform.openai.com/) for LLM Boost | [MCP SDK](https://modelcontextprotocol.io/) for MCP server | [scipy](https://scipy.org/) + [numpy](https://numpy.org/) for deep baseline profiling (`[baseline]`) | [sentence-transformers](https://www.sbert.net/) for semantic type inference in baseline (`[semantic]`)

### TypeScript / Node.js

| Dependency | Purpose |
|-----------|---------|
| Zero runtime deps | Core package has no dependencies (edge-safe) |
| [nodejs-polars](https://github.com/pola-rs/nodejs-polars) | Parquet reading (optional, Node.js only) |
| [csv-parse](https://csv.js.org/) | CSV reading (Node.js only) |
| [@modelcontextprotocol/sdk](https://modelcontextprotocol.io/) | MCP server (Node.js only) |

## MCP Server (Claude Desktop)

GoldenCheck includes an MCP server for Claude Desktop integration:

```bash
pip install goldencheck[mcp]
```

Add to your Claude Desktop config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "goldencheck": {
      "command": "goldencheck",
      "args": ["mcp-serve"]
    }
  }
}
```

**Available tools:**

| Tool | Description |
|------|-------------|
| `scan` | Scan a file for data quality issues (with optional LLM boost) |
| `validate` | Validate against pinned rules in goldencheck.yml |
| `profile` | Get column-level statistics and health score |
| `health_score` | Quick A-F grade for a data file |
| `get_column_detail` | Deep-dive into a specific column |
| `list_checks` | List all available profiler checks |

## Remote MCP Server

GoldenCheck is available as a hosted MCP server on [Smithery](https://smithery.ai/servers/benzsevern/goldencheck) — connect from any MCP client without installing anything.

**Claude Desktop / Claude Code:**
```json
{
  "mcpServers": {
    "goldencheck": {
      "url": "https://goldencheck-mcp-production.up.railway.app/mcp/"
    }
  }
}
```

**Local server:**
```bash
pip install goldencheck[mcp]
goldencheck mcp-serve
```

19 tools available: scan files, validate rules, profile columns, health-score datasets, auto-configure validation, explain findings, compare domains, suggest fixes.

## Jupyter / Colab

GoldenCheck renders rich HTML in Jupyter notebooks:

```python
from goldencheck.engine.scanner import scan_file
from goldencheck.engine.confidence import apply_confidence_downgrade
from goldencheck.notebook import ScanResult

findings, profile = scan_file("data.csv")
findings = apply_confidence_downgrade(findings, llm_boost=False)

# Rich HTML display in notebooks
ScanResult(findings=findings, profile=profile)
```

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/benzsevern/goldencheck/blob/main/scripts/goldencheck_demo.ipynb)

## API Quick Reference

### Python

```python
import goldencheck

# Scan a CSV for quality issues
findings = goldencheck.scan_file("data.csv")
for f in findings:
    print(f"[{f.severity}] {f.column}: {f.check} — {f.message}")

# Create baseline and detect drift
from goldencheck import create_baseline, scan_file
baseline = create_baseline("data.csv")
baseline.save("goldencheck_baseline.yaml")
findings, profile = scan_file("data.csv", baseline="goldencheck_baseline.yaml")

# Health score
score = goldencheck.health_score("data.csv")
print(score)  # e.g. "B (78/100)"
```

### TypeScript

```typescript
import { scanData, TabularData, Severity } from "goldencheck";

// Scan records (edge-safe)
const data = new TabularData(records);
const { findings, profile } = scanData(data);
for (const f of findings) {
  console.log(`[${f.severity === Severity.ERROR ? "ERROR" : "WARNING"}] ${f.column}: ${f.message}`);
}
```

```typescript
import { readFile, scanData, applyConfidenceDowngrade, healthScore } from "goldencheck/node";

// Scan a CSV file (Node.js)
const data = readFile("data.csv");
const result = scanData(data, { domain: "healthcare" });
const findings = applyConfidenceDowngrade(result.findings, false);

// Health score
const byCol = {};
for (const f of findings) {
  if (f.severity >= 2) {
    byCol[f.column] ??= { errors: 0, warnings: 0 };
    byCol[f.column][f.severity === 3 ? "errors" : "warnings"]++;
  }
}
const { grade, points } = healthScore(byCol);
console.log(`Health: ${grade} (${points}/100)`);
```

```typescript
import { readFile, createBaseline, serializeBaseline } from "goldencheck/node";
import { runDriftChecks, deserializeBaseline } from "goldencheck";
import { writeFileSync, readFileSync } from "node:fs";

// Create baseline and detect drift
const data = readFile("reference.csv");
const baseline = createBaseline(data);
writeFileSync("baseline.json", serializeBaseline(baseline));

const newData = readFile("production.csv");
const saved = deserializeBaseline(readFileSync("baseline.json", "utf-8"));
const driftFindings = runDriftChecks(newData, saved);
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and guidelines.

## Author

[Ben Severn](https://bensevern.dev)

## License

MIT — see [LICENSE](LICENSE)

---

## Part of the Golden Suite

| Tool | Purpose | Install |
|------|---------|---------|
| [GoldenCheck](https://github.com/benzsevern/goldencheck) | Validate & profile data quality | `pip install goldencheck` / `npm install goldencheck` |
| [GoldenFlow](https://github.com/benzsevern/goldenflow) | Transform & standardize data | `pip install goldenflow` |
| [GoldenMatch](https://github.com/benzsevern/goldenmatch) | Deduplicate & match records | `pip install goldenmatch` |
| [GoldenPipe](https://github.com/benzsevern/goldenpipe) | Orchestrate the full pipeline | `pip install goldenpipe` |

**Companion projects:**
- [dbt-goldencheck](https://github.com/benzsevern/dbt-goldencheck) — data validation as a dbt test.
- [goldencheck-types](https://github.com/benzsevern/goldencheck-types) — community-contributed domain type packs.
- [goldencheck-action](https://github.com/benzsevern/goldencheck-action) — GitHub Action for CI with PR comments.
