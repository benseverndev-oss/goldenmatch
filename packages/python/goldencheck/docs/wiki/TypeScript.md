# TypeScript

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

- **`goldencheck/core`** — Edge-safe core that works in browsers, Cloudflare Workers, Vercel Edge Runtime, Deno, and Bun
- **`goldencheck/node`** — Node.js layer with CSV/Parquet reading, CLI, MCP server, and A2A server

## What's Included

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

## Links

- [npm package](https://www.npmjs.com/package/goldencheck)
- [GitHub source](https://github.com/benzsevern/goldencheck/tree/main/packages/goldencheck-js)
- [Python docs](https://benzsevern.github.io/goldencheck/)
