# infermap — TypeScript Examples

Runnable TypeScript examples for the `infermap` npm package. Each example is self-contained and progressively demonstrates more advanced usage.

## Prerequisites

From the repo root:

```bash
cd packages/infermap-js
npm install
npm run build
npm link                       # makes `infermap` resolvable from anywhere
cd ../..
npm install --no-save tsx      # runner for TypeScript
```

Alternatively, run from inside a project that already depends on `infermap` from npm:

```bash
npm install infermap tsx
npx tsx path/to/example.ts
```

## The examples

| # | File | What it shows |
|---|------|---------------|
| 01 | [01_basic_mapping.ts](./01_basic_mapping.ts) | Basic `map()` call with plain record arrays. The minimum viable integration. |
| 02 | [02_csv_file_mapping.ts](./02_csv_file_mapping.ts) | Read CSV files from disk via `extractSchemaFromFile` (`infermap/node`), then call `MapEngine.mapSchemas`. |
| 03 | [03_config_and_aliases.ts](./03_config_and_aliases.ts) | Teach the AliasScorer about domain vocabulary via `config.aliases`; reweight or disable scorers via `config.scorers`. |
| 04 | [04_custom_scorer.ts](./04_custom_scorer.ts) | Define a domain-specific scorer with `defineScorer()` and plug it into the engine alongside the defaults. Jaccard over prefix/suffix synonyms. |
| 05 | [05_save_and_reuse.ts](./05_save_and_reuse.ts) | Persist a computed mapping to JSON with `mapResultToConfigJson` and re-hydrate it later with `fromConfig` — useful for caching expensive mapping computations. |
| 06 | [06_nextjs_api_route.ts](./06_nextjs_api_route.ts) | Next.js App Router Route Handler running on the Edge Runtime. Accepts CSV text over HTTP POST, returns a normalized JSON report. Zero Node built-ins. |
| 07 | [07_database_mapping.ts](./07_database_mapping.ts) | Connect to a SQLite database, extract its schema via `extractDbSchema`, and map it onto a canonical target loaded from a JSON schema definition file. Requires `better-sqlite3`. |

## Which example should I start with?

- **Trying it for the first time:** 01 → 02 → 06
- **Integrating into a Next.js app:** 06 → 03
- **Moving an existing CSV pipeline:** 02 → 05 → 03
- **Adding domain-specific logic:** 03 → 04
- **Talking to a real database:** 07

## See also

- [Python examples](../README.md) for equivalent patterns in the Python package
- [Package README](../../packages/infermap-js/README.md) for API reference
- [npm](https://www.npmjs.com/package/infermap) for install instructions
