# GoldenMatch (TypeScript)

**Entity resolution toolkit for Node.js and edge runtimes. Deduplicate, match, and create golden records — in TypeScript.**

```bash
npm install goldenmatch
```

[![npm](https://img.shields.io/npm/v/goldenmatch?color=d4a017)](https://www.npmjs.com/package/goldenmatch)
[![Node](https://img.shields.io/node/v/goldenmatch?color=339933)](https://nodejs.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](https://github.com/benseverndev-oss/goldenmatch/blob/main/LICENSE)
[![Tests](https://img.shields.io/badge/tests-590%20passing-brightgreen)](https://github.com/benseverndev-oss/goldenmatch/tree/main/packages/goldenmatch-js/tests)

---

## Why this port?

- **Edge-safe core** — the matching engine runs in browsers, Workers, Vercel Edge Runtime, Deno
- **Pure TypeScript** — no native dependencies required; peer deps unlock performance (hnswlib, ONNX, piscina)
- **Feature parity with Python goldenmatch** — same scorers, same clustering, same YAML configs
- **Strict TypeScript** — `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`

## Compatibility with the Python package

`goldenmatch` (npm) and `goldenmatch` (PyPI) are **independent semver lines** for the
same toolkit — we do not lockstep the version numbers. The TS package is an **edge-safe
subset**: it deliberately omits a few Python-only surfaces (below) so it can run in
browsers, Workers, and edge runtimes. Everything else is at **core parity**
(scoring, blocking, clustering, golden records, auto-config, identity graph, PPRL,
learning memory, MCP, A2A, CLI), validated by Python-generated parity fixtures.

| npm | ≈ PyPI | What the npm release covers |
|-----|--------|-----------------------------|
| **1.0.0** | 2.0.x | Stable API. Core ER + identity graph + MCP (45 tools) + A2A (bearer auth) + the AgentSession agent surface + the config-suggestion healer (WASM). |
| 0.4–0.13 | 1.6–1.30 | Pre-1.0 wave line (see `CHANGELOG.md`). |

**Python-only by design (not in the npm package):**
- Distributed engine (Ray / GPU / Vertex embeddings) — the npm package is single-node / edge.
- REST API + React web UI — npm ships a thin programmatic server only.
- Agent tools `sensitivity` / `incremental` / `certify_recall` — no TS core.

Rationale + the full policy: [`docs/versioning-policy.md`](https://github.com/benseverndev-oss/goldenmatch/blob/main/docs/versioning-policy.md).

## Quick Start

```typescript
import { dedupe } from "goldenmatch";

const rows = [
  { id: 1, name: "John Smith", email: "john@example.com", zip: "12345" },
  { id: 2, name: "Jon Smith",  email: "john@example.com", zip: "12345" },
  { id: 3, name: "Jane Doe",   email: "jane@example.com", zip: "54321" },
];

const result = dedupe(rows, {
  fuzzy: { name: 0.85 },
  blocking: ["zip"],
  threshold: 0.85,
});

console.log(result.stats);
// { totalRecords: 3, totalClusters: 2, matchRate: 0.67, ... }

for (const record of result.goldenRecords) {
  console.log(record);
}
```

## Auto-Config Verification (v0.3)

Auto-generated configs are now checked both before the pipeline runs and after
scoring finishes, so you get actionable diagnostics instead of silent failures
on edge-case data.

### Preflight — six static checks

When you call `autoConfigureRows(rows)`, the returned config ships with a
`_preflightReport` summarising six config-time checks:

1. **missing_column** — matchkey/blocking references a column not in the data
2. **cardinality_high** — a column is near-unique (poor blocking signal)
3. **cardinality_low** — a column has too few distinct values to discriminate
4. **block_size** — a blocking key would produce oversized blocks
5. **remote_asset** — a scorer requires a model download (gated offline)
6. **weight_confidence** — a weighted matchkey's weights look unbalanced

Many findings trigger **auto-repairs** (field dropped, scorer swapped,
weight clamped). `hasErrors === true` on unrepairable errors raises
`ConfigValidationError` with the full report attached.

```ts
import { autoConfigureRows, ConfigValidationError } from "goldenmatch";

const cfg = autoConfigureRows(rows);
for (const f of cfg._preflightReport!.findings) {
  console.log(`[${f.severity}] ${f.check}/${f.subject}: ${f.message}`);
}
```

Defaults are **offline-safe**: remote-asset scorers (cross-encoder, remote
embeddings) are dropped unless you opt in with `allowRemoteAssets: true`.

### Postflight — four runtime signals

Inside `dedupe()` / `match()`, after scoring but before clustering, the
pipeline computes four signals attached as `result.postflightReport`:

1. **scoreHistogram** — 100-bin pair-score distribution
2. **blockSizePercentiles** + **preliminaryClusterSizes** — p50/p95/p99/max
3. **thresholdOverlapPct** — fraction of pairs near the current threshold
4. **oversizedClusters** — components above size limit, with bottleneck pair

If the score distribution is clearly bimodal, postflight proposes a
threshold adjustment. In **strict mode** (`autoConfigureRows(rows, { strict: true })`
or manual `_strictAutoconfig: true`) the signals are still emitted but the
threshold is never touched — use this for reproducible CI pipelines.

See `examples/verificationInspection.ts` and `examples/strictModeParity.ts`
for runnable demos.

## Config suggestions (the healer)

The config-suggestion engine — the "healer" — reads a run's diagnostics and
proposes (or applies) config fixes: lower/raise a threshold, swap a scorer, add a
negative-evidence field. It runs on the TS/JS surface via the shared `suggest-core`
kernel compiled to WebAssembly (`suggest-wasm`), at full parity with the Python
default pipeline — same free trigger, same verify path, same bounded heal loop, on
every surface (core, CLI, MCP `review_config`, A2A `review_config`).

```typescript
import { dedupe } from "goldenmatch";
import { enableSuggestWasm } from "goldenmatch/core/suggest-wasm";

enableSuggestWasm();                                   // opt-in (the [native] analog)

const free = await dedupe(rows);          // free.suggestions (verified: false) when the trigger fires
const verified = await dedupe(rows, { suggest: true }); // verified: true
const healed = await dedupe(rows, { heal: true });      // healed.config + healTrail
```

- **Opt-in WASM kernel.** The healer reaches the kernel through a lean registry; the
  heavy WASM module is behind the opt-in subpath `goldenmatch/core/suggest-wasm`.
  `enableSuggestWasm()` registers it — the exact TS analog of `pip install
  goldenmatch[native]`. Default bundles stay lean (no inlined wasm) and edge-safe.
- **Graceful-empty.** With no backend registered, every healer surface returns `[]` /
  `undefined` and never throws; `dedupe()` works exactly as before.
- **Kill-switch.** The free suggestion pass honors `GOLDENMATCH_SUGGEST_ON_DEDUPE=0`
  (where `process.env` is available); `{ suggest }` / `{ heal }` are explicit opt-ins.

## Three entrypoints

```typescript
import { dedupe, match, scoreStrings } from "goldenmatch";         // edge-safe core
import { readFile, writeCsv } from "goldenmatch/node";              // Node-only file I/O
// CLI: `npx goldenmatch-js dedupe data.csv --output golden.csv`
```

## Feature matrix

### Scoring algorithms
- Exact, Jaro-Winkler, Levenshtein, Token-Sort, Soundex, Dice, Jaccard, Ensemble
- Probabilistic (Fellegi-Sunter with Splink-style EM)
- LLM scorer (OpenAI/Anthropic via fetch — edge-safe)
- Cross-encoder reranking (via @huggingface/transformers)

### Blocking strategies
- Static, multi-pass, sorted-neighborhood, adaptive
- ANN (approximate nearest neighbor via hnswlib-node peer dep or brute-force)
- Canopy (TF-IDF)
- Learned (data-driven predicate selection)

### Golden record strategies
- most_complete, majority_vote, source_priority, most_recent, first_non_null
- Full provenance tracking

### Pipeline features
- PPRL (privacy-preserving record linkage, 3 security levels with HMAC-SHA256)
- Graph ER (multi-table entity resolution with evidence propagation)
- Sensitivity analysis (parameter sweep with CCMS/TWI)
- Streaming (incremental single-record matching)
- Memory (persistent corrections + threshold learning)
- Review queue (human-in-the-loop)

## Optional peer deps

Zero-dep install works. These unlock advanced paths:

| Peer dep | What it enables |
|---|---|
| `yaml` | YAML config file loading |
| `hnswlib-node` | True sub-linear ANN blocking (vs brute-force) |
| `@huggingface/transformers` | ONNX cross-encoder reranking (MiniLM) |
| `piscina` | Worker-thread parallel block scoring |
| `ink` + `react` | Interactive terminal UI |
| `ink-table`, `ink-select-input`, `ink-text-input`, `ink-spinner`, `ink-gradient` | Richer TUI widgets |
| `pg` | Postgres connector + sync |
| `@duckdb/node-api` | DuckDB connector |
| `snowflake-sdk`, `@google-cloud/bigquery`, `@databricks/sql` | Cloud warehouse connectors |

### Optional WASM acceleration (opt-in)

The scorers run in pure TypeScript by default — zero dependencies, edge-safe.
For larger workloads you can opt into a WebAssembly backend (the same Rust
scorer kernel the Python package uses) for `jaro_winkler` / `levenshtein` /
`exact`:

```ts
import { enableWasm, dedupe } from "goldenmatch";

await enableWasm();   // loads + instantiates the WASM scorer; returns false (stays pure-TS) if unavailable
const result = await dedupe(rows, config);
```

Pure TypeScript stays the default and the automatic fallback — if the WASM
module can't load, scoring transparently continues in pure TS. Pass
`enableWasm({ require: true })` to fail hard instead.

## Servers

```bash
# MCP server (for Claude Desktop / Code)
npx goldenmatch-js mcp-serve

# REST API
npx goldenmatch-js serve --port 8000

# A2A agent server
npx goldenmatch-js agent-serve --port 8200

# Interactive TUI
npx goldenmatch-js tui data.csv
```

## CLI commands

```
goldenmatch-js dedupe <files...>    Deduplicate records
goldenmatch-js match <target> <ref> Match target against reference
goldenmatch-js score <a> <b>        Score similarity between two strings
goldenmatch-js info                 Show scorers, strategies, transforms
goldenmatch-js profile <file>       Profile a dataset
goldenmatch-js demo                 Run a quick demo on synthetic data
goldenmatch-js mcp-serve            Start MCP server (stdio)
goldenmatch-js serve                Start REST API
goldenmatch-js agent-serve          Start A2A agent
goldenmatch-js tui                  Interactive terminal UI
```

## Examples

See [`examples/`](./examples) for 10+ full examples covering basic dedupe, CSV pipelines,
probabilistic matching (Fellegi-Sunter), PPRL, streaming, LLM scoring, explanations, and evaluation.

## Documentation

Full docs: https://docs.bensevern.dev/goldenmatch/typescript

## License

MIT. See [LICENSE](https://github.com/benseverndev-oss/goldenmatch/blob/main/LICENSE).
