# Porting GoldenFlow to TypeScript — Playbook

> This document describes the exact process used to port GoldenCheck (Python) to TypeScript with full feature parity, and how to replicate it for GoldenFlow. Written for a Claude Code session that starts fresh.

## What was done (GoldenCheck)

GoldenCheck (Python, ~11,700 LOC, 86 files) was ported to TypeScript (~10,000 LOC, 72 files) in `packages/goldencheck-js/`. The port follows the **infermap dual-package pattern** — an edge-safe core with zero Node dependencies, plus a Node layer for file I/O and CLI.

**Reference repo**: `D:\show_case\goldencheck\packages\goldencheck-js\`

## The pattern

```
repo-root/
├── goldenflow/              # Python package (existing, untouched)
├── packages/goldenflow-js/  # TypeScript port (new)
│   ├── package.json         # npm package config
│   ├── tsconfig.json        # Strict TypeScript
│   ├── tsup.config.ts       # Build: 4 entry points, dual ESM+CJS
│   ├── vitest.config.ts     # Test runner
│   ├── src/
│   │   ├── index.ts         # Re-exports core
│   │   ├── cli.ts           # Commander.js CLI
│   │   ├── core/            # Edge-safe (browsers, Workers, Edge Runtime)
│   │   │   ├── index.ts     # Public API surface
│   │   │   ├── types.ts     # All interfaces, enums, factory functions
│   │   │   ├── data.ts      # TabularData (Polars replacement)
│   │   │   └── ...          # Module-per-module port
│   │   └── node/            # Node 20+ only
│   │       ├── index.ts     # Re-exports core + Node-only
│   │       ├── reader.ts    # CSV/Parquet file reading
│   │       └── ...          # MCP, TUI, etc.
│   └── tests/
│       ├── unit/            # Per-module tests
│       ├── parity/          # Python-TS equivalence tests
│       └── smoke.test.ts    # Basic import sanity
├── package.json             # Root orchestrator (NOT a workspace)
└── scripts/
    └── gen_parity_goldens.py  # Python generates golden outputs for TS parity tests
```

## Step-by-step process

### Phase 0: Understand the Python codebase

Before writing any TypeScript, thoroughly explore the Python source:
- Read every file in `goldenflow/`
- Map the dependency graph (what imports what)
- Identify the pipeline flow (entry point → transforms → output)
- Note all Polars operations — each needs a TabularData equivalent
- List all external dependencies (Polars, Pydantic, Typer, etc.)

### Phase 1: Scaffolding

Create the package structure with these exact config files:

**package.json** — key decisions:
```json
{
  "name": "goldenflow",
  "type": "module",
  "exports": {
    ".": { "types": "...", "import": "...", "require": "..." },
    "./core": { ... },
    "./node": { ... }
  },
  "bin": { "goldenflow-js": "./dist/cli.cjs" },
  "engines": { "node": ">=20" },
  "dependencies": { "commander": "^13.0.0" },
  "peerDependencies": { "nodejs-polars": "*", "yaml": "*" },
  "peerDependenciesMeta": { ... optional: true ... },
  "devDependencies": { "tsup": "^8.5.1", "typescript": "^5.4.0", "vitest": "^4.1.0", ... }
}
```

**tsconfig.json** — strict mode with `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`.

**tsup.config.ts** — 4 entry points (index, core/index, node/index, cli), dual ESM+CJS, dts, sourcemap, treeshake.

**Root package.json** — orchestrator only:
```json
{ "private": true, "scripts": { "build:js": "npm --prefix packages/goldenflow-js run build", ... } }
```

### Phase 2: Types first

Port all Python dataclasses/Pydantic models to TypeScript interfaces in `src/core/types.ts`:

| Python | TypeScript |
|--------|-----------|
| `@dataclass` | `interface` + factory function (`makeXxx()`) |
| `IntEnum` | `const Severity = { INFO: 1, ... } as const` + `type Severity = ...` |
| `Pydantic BaseModel` | `interface` + `validateConfig()` runtime validator |
| `dataclasses.replace()` | `replaceFinding()` using spread: `{ ...f, ...overrides }` |
| `Optional[str]` | `string \| null` (use `null`, not `undefined`, for Pydantic parity) |

**Critical rule**: All types are `readonly`. Findings are immutable — never mutate, always create new objects.

### Phase 3: TabularData (Polars replacement)

This is the most important abstraction. `TabularData` wraps `Record<string, unknown>[]` and provides every column operation the profilers/transforms need:

```typescript
class TabularData {
  constructor(rows: readonly Record<string, unknown>[]) { ... }
  
  // Column access
  column(name: string): readonly ColumnValue[]
  get columns(): readonly string[]
  get rowCount(): number
  
  // Null handling (treats null, undefined, "", "null", "nan", "none", "N/A" as null)
  nullCount(col: string): number
  dropNulls(col: string): ColumnValue[]
  
  // Type inference (checks JS runtime types first, then content)
  dtype(col: string): "string" | "integer" | "float" | "boolean" | "date" | "datetime" | "null"
  
  // Aggregation
  nUnique(col: string): number
  valueCounts(col: string): Map<ColumnValue, number>
  min(col: string): number | null  // MUST use loop, NOT Math.min(...array)
  max(col: string): number | null  // MUST use loop, NOT Math.max(...array)
  mean(col: string): number | null
  
  // Filtering, sampling (seedable PRNG), string ops, casting
  filter(pred): TabularData
  sample(n: number, seed?: number): TabularData
  strContains(col: string, pattern: RegExp): boolean[]
  castFloat(col: string): (number | null)[]
  numericValues(col: string): number[]
  stringValues(col: string): string[]
  sortedNumeric(col: string): number[]
}
```

**Critical**: `Math.min(...array)` and `Math.max(...array)` will crash with `RangeError` on arrays >65K elements. Always use a for-loop.

### Phase 4: Port modules bottom-up

Port in dependency order — leaves first, then composites:

1. **Types + data layer** — types.ts, data.ts, stats.ts
2. **Leaf modules** — individual transforms, profilers, etc.
3. **Orchestrators** — engine, pipeline, scanner
4. **Semantic/config** — type classifier, config validation
5. **Confidence/post-processing** — boost, downgrade, triage
6. **LLM integration** — providers (raw fetch, no SDK), parser, merger
7. **Baseline/drift** — statistical profiling, drift checks
8. **Agent** — intelligence, handoff, review queue
9. **Reporters** — JSON, CI
10. **Node layer** — file reader, MCP, TUI, A2A, CLI, watcher
11. **Parity tests** — golden-file validation

### Phase 5: CSV reader with type coercion

The CSV reader must auto-detect types (matching Polars behavior):

```typescript
function coerceValue(raw: string): string | number | boolean {
  if (raw === "true" || raw === "True" || raw === "TRUE") return true;
  if (raw === "false" || raw === "False" || raw === "FALSE") return false;
  if (raw.length > 0 && raw === raw.trim()) {
    const n = Number(raw);
    if (Number.isFinite(n) && raw !== "") return n;
  }
  return raw;
}
```

Without this, `dtype()` returns "string" for everything and profilers/transforms produce wrong results.

### Phase 6: Parity testing

Create `scripts/gen_parity_goldens.py` that:
1. Reads `tests/fixtures/parity_cases.json` (shared test manifest)
2. Runs Python engine on each case
3. Writes output to `tests/fixtures/_goldens/<name>.json`

TypeScript `tests/parity/parity.test.ts` loads the same manifest, runs the TS engine, and compares:
- Finding identity (column + check): exact match
- Severity: exact match
- Confidence: tolerance of 0.05 (float precision)

CI regenerates goldens from Python first, then runs TS parity suite.

### Phase 7: CI/CD

**.github/workflows/test.yml** — add job:
```yaml
goldenflow-js:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-node@v4
      with: { node-version: '20' }
    - run: npm install
      working-directory: packages/goldenflow-js
    - run: npm run typecheck && npm run test && npm run build
      working-directory: packages/goldenflow-js
```

**.github/workflows/npm-publish.yml** — trigger on `goldenflow-js-v*` tags.

## Hard-won lessons (do NOT skip these)

### Edge-safety rules for `src/core/`
- **NEVER** import `node:fs`, `node:path`, `node:http`, or use `process.on()`
- **NEVER** use `require()` — only `import` (tsup handles CJS conversion)
- If a module needs Node APIs, put it in `src/node/` and only re-export **types** from `core/index.ts`
- Test edge-safety: the `core/index.js` bundle should work in Cloudflare Workers

### Error handling rules
- **NEVER** use bare `catch {}` — always log the error: `catch (e) { console.warn("...", e instanceof Error ? e.message : String(e)); }`
- Separate `require()` (for optional peer deps) from the operation that uses the module — otherwise corrupt files get masked as "package not installed"
- LLM providers must throw on empty response content, not return empty string
- HTTP servers must try/catch `JSON.parse` on request bodies and return 400, not 500

### Math/numeric rules
- **NEVER** `Math.min(...array)` or `Math.max(...array)` — use a for-loop
- `ksTwoSample()` must short-circuit when `maxD === 0` → return `pValue: 1`
- PRNG is Mulberry32 (NOT Python's Mersenne Twister) — sampling results will differ

### Type system rules
- `Row = Readonly<Record<string, unknown>>` — accept `unknown` values, coerce in `column()` getter
- `Finding` interface is fully `readonly` — use `replaceFinding()` spread for updates
- `exactOptionalPropertyTypes: true` means `field?: T` requires explicit `undefined`, not just omission
- Export types with `export type { ... }` where possible for tree-shaking

### Differ/merger rules
- Group findings by `(column, check)` using `Map<string, Finding[]>` (array per key) — NOT `Map<string, Finding>` which drops duplicates
- LLM merger must strip `(suppressed: ...)` suffixes before matching
- `cross_column` findings must contain keyword "mismatch"/"inconsistent"/"doesn't match"

### Publishing checklist
1. `npm run typecheck` — 0 errors
2. `npm run test` — all pass
3. `npm run build` — clean ESM + CJS + .d.ts
4. `git tag goldenflow-js-v0.1.0 && git push origin goldenflow-js-v0.1.0`
5. Requires `NPM_TOKEN` GitHub secret (`gh secret set NPM_TOKEN --body "..."`)

## GoldenFlow-specific mapping

| GoldenFlow Python module | Suggested TS location | Notes |
|---|---|---|
| `goldenflow/config/schema.py` | `src/core/types.ts` + `src/core/config/schema.ts` | GoldenFlowConfig, TransformSpec, etc. |
| `goldenflow/engine/transformer.py` | `src/core/engine/transformer.ts` | TransformEngine — the main orchestrator |
| `goldenflow/engine/selector.py` | `src/core/engine/selector.ts` | select_transforms() |
| `goldenflow/engine/manifest.py` | `src/core/engine/manifest.ts` | Manifest, TransformRecord |
| `goldenflow/engine/profiler_bridge.py` | `src/core/engine/profiler-bridge.ts` | DatasetProfile, ColumnProfile |
| `goldenflow/engine/differ.py` | `src/core/engine/differ.ts` | diff_dataframes() |
| `goldenflow/transforms/*.py` | `src/core/transforms/*.ts` | One file per transform category |
| `goldenflow/mapping/schema_mapper.py` | `src/core/mapping/schema-mapper.ts` | SchemaMapper |
| `goldenflow/domains/*.py` | `src/core/domains/*.ts` | Domain-specific transform configs |
| `goldenflow/llm/*.py` | `src/core/llm/*.ts` | Edge-safe via fetch() |
| `goldenflow/mcp/server.py` | `src/node/mcp/server.ts` | MCP tools |
| `goldenflow/cli/*.py` | `src/cli.ts` | Commander.js |
| `goldenflow/tui/*.py` | `src/node/tui/app.ts` | ANSI terminal output |
| `goldenflow/a2a/*.py` | `src/node/a2a/server.ts` | HTTP + SSE |
| `goldenflow/connectors/*.py` | `src/node/connectors/*.ts` | DB connectors |
| `goldenflow/reporters/*.py` | `src/core/reporters/*.ts` | JSON, CI |
| `goldenflow/notebook.py` | `src/core/notebook.ts` | HTML rendering |
| `goldenflow/history.py` | `src/core/engine/history.ts` | JSONL scan history |
| `goldenflow/streaming.py` | `src/core/engine/streaming.ts` | Streaming transforms |

## Parallelization strategy

Use Claude Code subagents for independent modules. In the GoldenCheck port, we ran 3 subagents in parallel for:
1. Config + validator + reporters
2. LLM integration (7 files)
3. Baseline + drift (9 files)

Then another 3 for:
1. TUI + notebook
2. Agent + review + triage
3. Engine utilities + DB scanner

This cut total wall-clock time significantly. Launch subagents with specific file lists and complete Python source context — they have no conversation history.

## Estimated effort

GoldenFlow is ~5,400 LOC across 61 Python files — roughly half the size of GoldenCheck. Expected TypeScript output: ~4,500-5,000 LOC. With the pattern established, expect ~60% faster than GoldenCheck's port.
