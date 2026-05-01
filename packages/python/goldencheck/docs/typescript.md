---
title: TypeScript
layout: default
nav_order: 15
---

# TypeScript / JavaScript

GoldenCheck has a full TypeScript port with complete Python feature parity.

## Installation

```bash
npm install goldencheck
```

## Quick Start

```typescript
import { scanData, TabularData } from "goldencheck";

const data = new TabularData([
  { id: 1, email: "alice@example.com", age: 30 },
  { id: 2, email: "not-an-email", age: -5 },
]);

const { findings } = scanData(data);
findings.forEach(f => console.log(`[${f.check}] ${f.column}: ${f.message}`));
```

## Architecture

The TypeScript package is split into two layers:

| Layer | Import | Runtimes |
|-------|--------|----------|
| **Core** | `goldencheck/core` | Browsers, Cloudflare Workers, Vercel Edge Runtime, Deno, Bun |
| **Node** | `goldencheck/node` | Node.js (CSV/Parquet reading, CLI, MCP server, A2A server) |

### What's included

- **14 profilers** — all 10 column + 4 relation profilers ported from Python
- **Semantic types** — classifier, suppression engine, 3 domain packs (healthcare, finance, ecommerce)
- **Baseline/drift** — statistical profiling + 13 drift checks
- **LLM integration** — Anthropic + OpenAI via raw fetch (edge-safe, no SDK deps)
- **MCP server** — 7 tools for Claude Desktop integration
- **CLI** — `npx goldencheck-js scan data.csv`

## CLI Usage

```bash
npx goldencheck-js scan data.csv
npx goldencheck-js baseline data.csv
npx goldencheck-js scan data.csv --baseline goldencheck_baseline.yaml
npx goldencheck-js health-score data.csv
```

## Edge Runtime

The core module works in any JavaScript runtime without Node.js-specific APIs:

```typescript
import { scanData, TabularData } from "goldencheck/core";

export default {
  async fetch(request: Request) {
    const data = new TabularData(await request.json());
    const { findings } = scanData(data);
    return Response.json({ findings });
  },
};
```

## Domain Packs

```typescript
import { scanData, TabularData } from "goldencheck";

const { findings } = scanData(data, { domain: "healthcare" });
```

Available domains: `healthcare`, `finance`, `ecommerce`.

## LLM Boost

```typescript
import { scanDataWithLLM, TabularData } from "goldencheck";

const { findings } = await scanDataWithLLM(data, {
  provider: "anthropic",
  apiKey: process.env.ANTHROPIC_API_KEY,
});
```

## API Reference

### `scanData(data, options?)`

Scan tabular data for quality issues.

| Parameter | Type | Description |
|-----------|------|-------------|
| `data` | `TabularData` | The dataset to scan |
| `options.domain` | `string` | Domain pack: `"healthcare"`, `"finance"`, `"ecommerce"` |
| `options.baseline` | `string \| Baseline` | Path to baseline YAML or Baseline object |

Returns `{ findings: Finding[], profile: Profile }`.

### `scanDataWithLLM(data, options)`

Scan with LLM boost for additional rule discovery.

| Parameter | Type | Description |
|-----------|------|-------------|
| `data` | `TabularData` | The dataset to scan |
| `options.provider` | `string` | `"anthropic"` or `"openai"` |
| `options.apiKey` | `string` | API key for the provider |

Returns `Promise<{ findings: Finding[], profile: Profile }>`.

### `createBaseline(data)`

Create a statistical baseline from a dataset.

```typescript
import { createBaseline } from "goldencheck";

const baseline = createBaseline(data);
baseline.save("goldencheck_baseline.yaml");
```

### `healthScore(data)`

Get a letter grade and numeric score.

```typescript
import { healthScore } from "goldencheck";

const score = healthScore(data);
console.log(score); // e.g. "B (78/100)"
```

### `TabularData`

Wrapper for tabular data, accepts arrays of objects or column-oriented data.

```typescript
// Row-oriented
const data = new TabularData([
  { name: "Alice", age: 30 },
  { name: "Bob", age: 25 },
]);

// Column-oriented
const data = new TabularData({
  name: ["Alice", "Bob"],
  age: [30, 25],
});
```

### `Finding`

Each finding has these fields:

| Field | Type | Description |
|-------|------|-------------|
| `column` | `string` | Column name |
| `check` | `string` | Check identifier |
| `message` | `string` | Human-readable description |
| `severity` | `"ERROR" \| "WARNING" \| "INFO"` | Issue severity |
| `confidence` | `number` | 0.0 to 1.0 |
| `source` | `string \| null` | `null` = profiler, `"llm"` = LLM-generated |

## Links

- [npm package](https://www.npmjs.com/package/goldencheck)
- [GitHub source](https://github.com/benzsevern/goldencheck/tree/main/packages/goldencheck-js)
- [Python documentation]({% link installation.md %})
