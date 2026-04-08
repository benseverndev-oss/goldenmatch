# infermap

**Inference-driven schema mapping for TypeScript.** TS port of the [Python `infermap` library](https://pypi.org/project/infermap/) with full parity — same scorer pipeline, same Hungarian assignment, same mapping decisions, verified by shared golden tests.

```bash
npm install infermap
```

- Zero runtime dependencies in the core entrypoint
- Edge-runtime compatible (Next.js Route Handlers, Server Components, Edge Functions)
- Full-parity port of 6 scorers (Exact, Alias, PatternType, Profile, FuzzyName, LLM) + O(n³) Hungarian assignment
- Optional Node-side DB providers (SQLite, Postgres, DuckDB) under `infermap/node`
- CLI with `map` / `apply` / `inspect` / `validate` commands

## Quick start

```ts
import { map } from "infermap";

const result = map(
  {
    records: [
      { fname: "John", lname: "Doe", email_addr: "j@d.co" },
      { fname: "Jane", lname: "Smith", email_addr: "j@s.co" },
    ],
  },
  {
    records: [
      { first_name: "", last_name: "", email: "" },
    ],
  }
);

console.log(result.mappings);
// [
//   { source: "fname",      target: "first_name", confidence: 0.44, ... },
//   { source: "lname",      target: "last_name",  confidence: 0.48, ... },
//   { source: "email_addr", target: "email",      confidence: 0.69, ... },
// ]
```

## Inputs

`map()` accepts any of these for both `source` and `target`:

```ts
// Pre-extracted schema
{ fields: [...], sourceName, requiredFields }

// Plain records
{ records: [{ ... }], sourceName? }

// CSV text
{ csvText: "id,name\n1,alice\n", sourceName? }

// JSON array text
{ jsonText: "[{...}]", sourceName? }

// JSON schema definition
{ schemaDefinition: "{\"fields\":[...]}", sourceName? }
```

Node users can also read files directly:

```ts
import { extractSchemaFromFile } from "infermap/node";
import { MapEngine } from "infermap";

const src = await extractSchemaFromFile("./crm.csv");
const tgt = await extractSchemaFromFile("./canonical.json");
const result = new MapEngine().mapSchemas(src, tgt);
```

## Next.js usage

Works in any Next.js context — Server Components, Route Handlers, Server Actions, Edge Functions.

```ts
// app/api/infer/route.ts
import { map } from "infermap";

export async function POST(req: Request) {
  const { sourceCsv, targetCsv } = await req.json();
  const result = map(
    { csvText: sourceCsv },
    { csvText: targetCsv }
  );
  return Response.json(result);
}
```

The default entrypoint has zero Node built-ins, so `edge` runtime works:

```ts
export const runtime = "edge";
```

Use `infermap/node` from Node-runtime code when you need database or filesystem access.

## Database sources

Optional Node-only providers. Install the driver you need as a peer dep:

```bash
npm install better-sqlite3          # for SQLite
npm install pg                       # for PostgreSQL
npm install @duckdb/node-api         # for DuckDB
```

```ts
import { extractDbSchema } from "infermap/node";

const schema = await extractDbSchema(
  "postgresql://user:pass@host/mydb",
  { table: "customers" }
);
```

## Config

Scorer weights and alias extensions via a JSON config:

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

## Custom scorers

```ts
import { MapEngine, defaultScorers, defineScorer, makeScorerResult } from "infermap";

const semanticMatch = defineScorer(
  "semantic",
  (source, target) => {
    // return null to abstain, or a ScorerResult in [0, 1]
    return makeScorerResult(0.8, "looks similar");
  },
  0.6 // weight
);

const engine = new MapEngine({
  scorers: [...defaultScorers(), semanticMatch],
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

## Parity with Python

This package is a faithful TS port of [`infermap` on PyPI](https://pypi.org/project/infermap/). Mapping decisions, confidence scores, and unmapped lists are verified to agree with the Python engine to 4 decimal places via shared golden tests living in the parent repo. If you find a parity drift, please file an issue.

## Exports

| Path | Contents | Runtime |
|------|----------|---------|
| `infermap` / `infermap/core` | Types, engine, all 6 scorers, Hungarian assignment, in-memory / CSV / JSON / schema-file providers, JSON config loader, `map()` | edge-safe |
| `infermap/node` | Filesystem file reader, DB providers (SQLite / Postgres / DuckDB) | Node only |

## License

MIT
