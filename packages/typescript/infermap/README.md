# infermap

> Map messy source columns to a known target schema — accurately, explainably, with zero config.

[![npm version](https://img.shields.io/npm/v/infermap?color=cb3837&label=npm)](https://www.npmjs.com/package/infermap)
[![npm downloads](https://img.shields.io/npm/dw/infermap?color=cb3837&label=downloads%2Fweek)](https://www.npmjs.com/package/infermap)
[![bundle size](https://img.shields.io/bundlephobia/minzip/infermap?label=min%2Bgzip)](https://bundlephobia.com/package/infermap)
[![install size](https://packagephobia.com/badge?p=infermap)](https://packagephobia.com/result?p=infermap)
[![types: included](https://img.shields.io/npm/types/infermap?color=blue)](https://www.npmjs.com/package/infermap)

[![CI](https://github.com/benzsevern/infermap/actions/workflows/test.yml/badge.svg?branch=main)](https://github.com/benzsevern/infermap/actions/workflows/test.yml)
[![tests](https://img.shields.io/badge/tests-135%20passing-brightgreen)](https://github.com/benzsevern/infermap/tree/main/packages/infermap-js/tests)
[![parity with Python](https://img.shields.io/badge/parity-Python%20bit--for--bit-d4a017)](https://github.com/benzsevern/infermap/wiki/Python-vs-TypeScript)
[![Node](https://img.shields.io/node/v/infermap?color=339933&logo=node.js&logoColor=white)](https://nodejs.org)
[![Edge runtime](https://img.shields.io/badge/edge%20runtime-compatible-000000)](https://nextjs.org/docs/app/api-reference/edge)
[![License: MIT](https://img.shields.io/npm/l/infermap?color=green)](./LICENSE)

```bash
npm install infermap
```

`infermap` is a schema-mapping engine: give it any two field collections (records, CSVs, database tables) and it figures out which source field corresponds to which target field, with confidence scores and human-readable reasoning. Built as a faithful TypeScript port of the [Python `infermap` package](https://pypi.org/project/infermap/), with mapping decisions verified bit-for-bit by a shared golden-test parity suite.

- 🪶 **Zero runtime dependencies** in the core entrypoint
- ⚡ **Edge-runtime compatible** — Next.js Server Components, Route Handlers, Edge Functions
- 🧠 **Six built-in scorers** — exact name, alias, semantic-type regex, statistical profile, fuzzy name, LLM (pluggable)
- ⚖️ **Optimal 1:1 assignment** via vendored O(n³) Hungarian algorithm
- 🗄️ **Optional Node DB providers** — SQLite, Postgres, DuckDB
- 🛠️ **CLI** — `map`, `apply`, `inspect`, `validate`
- 📐 **Strict TypeScript** — `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`, full `.d.ts`

## Table of contents

- [What it does](#what-it-does)
- [Install](#install)
- [Quick start](#quick-start)
- [Inputs](#inputs)
- [Next.js usage](#nextjs-usage)
- [Database sources](#database-sources)
- [Config](#config)
- [Custom scorers](#custom-scorers)
- [CLI](#cli)
- [Parity with Python](#parity-with-python)
- [Exports](#exports)
- [Links](#links)

## What it does

You have data with messy column names. You want it mapped to a clean canonical schema. Without `infermap`:

```ts
// 50 lines of brittle if/else, hardcoded synonyms, and regret
if (col === "fname" || col === "first_nm") canonical[i] = "first_name";
else if (col === "email_addr" || col === "e_mail" || col === "mail") canonical[i] = "email";
// ...
```

With `infermap`:

```ts
import { map } from "infermap";

const result = map(
  { records: [{ fname: "John", lname: "Doe", email_addr: "j@d.co", tel: "555-0100" }] },
  { records: [{ first_name: "", last_name: "", email: "", phone: "" }] }
);

for (const m of result.mappings) {
  console.log(`${m.source} → ${m.target}  (${m.confidence.toFixed(2)})`);
}
// fname       → first_name  (0.44)
// lname       → last_name   (0.48)
// email_addr  → email       (0.69)
// tel         → phone       (0.39)
```

Each mapping comes with a per-scorer confidence breakdown, so when something goes wrong you can see exactly which signal contributed.

## Install

```bash
npm install infermap
# or
pnpm add infermap
# or
yarn add infermap
```

Requires Node ≥ 20. The default entrypoint is edge-runtime compatible.

## Quick start

```ts
import { map } from "infermap";

const crm = [
  { fname: "John", lname: "Doe", email_addr: "j@d.co", signup_dt: "2024-01-15" },
  { fname: "Jane", lname: "Smith", email_addr: "j@s.co", signup_dt: "2024-02-20" },
];

const canonical = [
  { first_name: "", last_name: "", email: "", created_at: "" },
];

const result = map({ records: crm }, { records: canonical });

console.log(result.mappings);
// [
//   { source: "fname",      target: "first_name", confidence: 0.44, breakdown: {...}, reasoning: "..." },
//   { source: "lname",      target: "last_name",  confidence: 0.48, breakdown: {...}, reasoning: "..." },
//   { source: "email_addr", target: "email",      confidence: 0.69, breakdown: {...}, reasoning: "..." },
//   { source: "signup_dt",  target: "created_at", confidence: 0.41, breakdown: {...}, reasoning: "..." },
// ]
```

## Inputs

`map()` accepts any of these shapes for both `source` and `target`:

```ts
type MapInput =
  | SchemaInfo                                                   // pre-extracted
  | { records: Array<Record<string, unknown>>;  sourceName? }    // plain records
  | { csvText: string;                          sourceName? }    // CSV as string
  | { jsonText: string;                         sourceName? }    // JSON array as string
  | { schemaDefinition: string | object;        sourceName? };   // JSON schema file
```

Node users can read files directly:

```ts
import { extractSchemaFromFile } from "infermap/node";
import { MapEngine } from "infermap";

const src = await extractSchemaFromFile("./crm.csv");
const tgt = await extractSchemaFromFile("./canonical.json");
const result = new MapEngine().mapSchemas(src, tgt);
```

## Next.js usage

Works in any Next.js context — Server Components, Route Handlers, Server Actions, Edge Functions. The default entrypoint has zero Node built-ins, so the Edge Runtime works without any special config.

```ts
// app/api/infer/route.ts
import { map, mapResultToReport } from "infermap";

export const runtime = "edge"; // remove if you need Node APIs

export async function POST(req: Request) {
  const { sourceCsv, targetCsv } = await req.json();
  const result = map(
    { csvText: sourceCsv },
    { csvText: targetCsv }
  );
  return Response.json(mapResultToReport(result));
}
```

For filesystem or database access, switch to Node runtime and import from `infermap/node`.

## Database sources

Optional Node-only providers. Install only the driver you need:

```bash
npm install better-sqlite3          # for sqlite://
npm install pg                       # for postgresql://
npm install @duckdb/node-api         # for duckdb://
```

```ts
import { extractDbSchema } from "infermap/node";

const schema = await extractDbSchema(
  "postgresql://user:pass@host/mydb",
  { table: "customers" }
);
```

## Config

Reweight scorers and extend the alias table via a JSON config object:

```ts
import { map } from "infermap";

const result = map(source, target, {
  config: {
    scorers: {
      LLMScorer: { enabled: false },
      FuzzyNameScorer: { weight: 0.3 },
    },
    aliases: {
      order_id: ["order_num", "ord_no"],
      customer_id: ["cust_id", "customer_number"],
    },
  },
});
```

You can also persist a computed mapping and reload it:

```ts
import { mapResultToConfigJson, fromConfig } from "infermap";
import { writeFile, readFile } from "node:fs/promises";

await writeFile("mapping.json", mapResultToConfigJson(result));
// later:
const restored = fromConfig(await readFile("mapping.json", "utf8"));
```

## Custom scorers

```ts
import { MapEngine, defaultScorers, defineScorer, makeScorerResult } from "infermap";

const domainScorer = defineScorer(
  "DomainMatcher",
  (source, target) => {
    // return null to abstain, or a ScorerResult in [0, 1]
    if (source.name.startsWith("cust_") && target.name.startsWith("customer_")) {
      return makeScorerResult(0.9, "shared customer prefix");
    }
    return null;
  },
  0.6 // weight
);

const engine = new MapEngine({
  scorers: [...defaultScorers(), domainScorer],
});
```

## CLI

```bash
npx infermap map ./crm.csv ./canonical.csv
npx infermap inspect ./crm.csv
npx infermap map ./crm.csv ./canonical.csv --format json -o mapping.json
npx infermap apply ./crm.csv --config mapping.json --output renamed.csv
npx infermap validate ./crm.csv --config mapping.json --required email,id --strict
```

The CLI uses only `node:util/parseArgs` — no extra runtime deps.

## Parity with Python

This package is a faithful port of [`infermap` on PyPI](https://pypi.org/project/infermap/). Mapping decisions, confidence scores, and unmapped lists are verified to agree with the Python engine to 4 decimal places via shared golden tests that run on every CI build.

If a Python scorer changes, the golden generator must be re-run and the TS parity tests must pass before anything merges. **You can't accidentally ship drift.** If you find a parity bug, please [file an issue](https://github.com/benzsevern/infermap/issues/new) with both inputs and both outputs.

See the [Python vs TypeScript wiki page](https://github.com/benzsevern/infermap/wiki/Python-vs-TypeScript) for a feature parity matrix and migration guide.

## Exports

| Path | Contents | Runtime |
|------|----------|---------|
| `infermap` / `infermap/core` | Types, engine, all 6 scorers, Hungarian assignment, in-memory / CSV / JSON / schema-file providers, JSON config loader, `map()` | edge-safe |
| `infermap/node` | Filesystem file reader, DB providers (SQLite / Postgres / DuckDB) | Node only |

## Links

- 📦 [npm package](https://www.npmjs.com/package/infermap)
- 📘 [TypeScript API reference](https://github.com/benzsevern/infermap/wiki/TypeScript-API)
- 🔄 [Python vs TypeScript migration guide](https://github.com/benzsevern/infermap/wiki/Python-vs-TypeScript)
- 🧪 [Runnable examples](https://github.com/benzsevern/infermap/tree/main/examples/typescript)
- 🐍 [Python sister package](https://pypi.org/project/infermap/)
- 🐛 [Issue tracker](https://github.com/benzsevern/infermap/issues)
- 💬 [Discussions](https://github.com/benzsevern/infermap/discussions)

## License

[MIT](./LICENSE)
