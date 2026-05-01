# TypeScript usage examples

| File | What | Imports |
|---|---|---|
| `01-quickstart.ts` | 30-second dedupe of a record array | `goldenmatch` |
| `02-edge-runtime.ts` | Vercel Edge / Cloudflare Workers route. Uses `goldenmatch/core` (no `node:*` imports). | `goldenmatch/core` |
| `03-mcp-client.ts` | Connect to the `goldensuite-mcp` container from a TS MCP client. | `@modelcontextprotocol/sdk` |

## Run

```bash
npm install goldenmatch
npx tsx examples/typescript/01-quickstart.ts
```

For 03, spin up the master MCP server first:

```bash
docker run -p 8300:8300 ghcr.io/benzsevern/goldensuite-mcp:latest
npx tsx examples/typescript/03-mcp-client.ts
```

## What the TS port supplies

Feature parity with Python — fuzzy scorers (10+), probabilistic Fellegi-Sunter, PPRL, graph ER, LLM reranking, MCP/REST/A2A servers, 11+ CLI commands, interactive TUI. Strict TypeScript (`noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`). Output parity with the Python implementation locked at 4-decimal tolerance via a parity harness.

## Edge-safe core vs Node layer

- **`goldenmatch/core`** — pure-JS, zero `node:*` imports. Runs in browsers, Vercel Edge, Cloudflare Workers, Deno.
- **`goldenmatch/node`** — Node-specific paths: file I/O, native ANN (hnswlib-node), worker threads (piscina), DB connectors. Import from `goldenmatch/node` only when running in Node.

Routes deployed to edge runtimes **must** import from `/core`; importing from the package root pulls in `goldenmatch/node` and breaks the edge build.

## Going to production

- **Bundle and deploy** — tsup builds five entrypoints (`index`, `core/index`, `node/index`, `cli`, `node/backends/score-worker`). Pick the bundler your framework uses and let it tree-shake.
- **Worker pool for blocks** — Node-side, `goldenmatch/node` lazy-loads `piscina` for parallel block scoring on multi-core machines.
- **Optional native deps** — peer-imported via `await import("...")` so the core package installs cleanly even when you don't need them. Add them when you need ANN (`hnswlib-node`), ONNX cross-encoder (`@huggingface/transformers`), or DB connectors (`pg`, `duckdb`, `snowflake-sdk`).

See [`packages/typescript/goldenmatch/README.md`](../../packages/typescript/goldenmatch/README.md) for the full TS API surface.
