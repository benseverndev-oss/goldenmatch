# goldenpipe (TypeScript)

npm package `goldenpipe`. Port of the Python sibling at `packages/python/goldenpipe/`. Suite orchestrator: chains `goldencheck` -> `goldenflow` -> `goldenmatch` (the TS siblings under `packages/typescript/`). Currently **v0.1.0** (initial port; v1 scope = the core check->flow->dedupe chain).

## What this composes

Reads the edge-safe cores of the three siblings (confirm exported shapes in their `src/core/index.ts` before changing adapters):

- `goldencheck/core` -> `scanData(new TabularData(rows), opts)` returns `{ findings, profile }`; `profile.columns` are `ColumnProfile[]`. `Finding.severity` is **numeric** (INFO=1/WARNING=2/ERROR=3) — normalized to a string label in the check adapter.
- `goldenflow/core` -> `new TransformEngine(config).transformDf(rows)` returns `{ rows, columns, manifest }`. (NOT `.transform(rows)` / `.data` — the spec's hint was approximate; the real method is `transformDf`.)
- `goldenmatch/core` -> `await dedupe(rows, { config })` returns `{ goldenRecords, clusters, unique, dupes, stats, scoredPairs, config }`. **`dedupe` is async**, so the whole runner + public API is async. Note `goldenRecords` (not `.golden`).

## Layout

- `src/core/**` — edge-safe (NO `node:` imports): `models.ts`, `columnContext.ts`, `decisions.ts`, `engine/{registry,resolver,router,runner,reporter}.ts`, `adapters/{load,check,flow,match,index}.ts`, `pipeline.ts`.
- `src/node/**` — Node-only: `csv.ts` (hand-rolled CSV reader), `run.ts` (`run(source)` file path), `loadConfig.ts` (YAML via optional `yaml` peer dep), `mcp/server.ts` (hand-rolled JSON-RPC 2.0 stdio MCP server, `goldenpipe-mcp` bin), `a2a/server.ts` (node:http A2A agent, port 8250), `api/server.ts` (node:http REST API, port 8000).
- `src/cli.ts` — commander CLI (`run`, `stages`, `validate`, `init`, `mcp-serve`, `agent-serve`, `serve`).

## Static registry, not entry points

Python discovers stages via importlib entry points. The TS port uses a STATIC registry: `buildDefaultRegistry()` in `adapters/index.ts` registers `load` + the three suite stages. Custom stages: `registry.register(stage(...))`.

## Deferred (document, don't silently drop)

- `identity_resolve` stage (GoldenMatch Identity Graph pipeline population) and `infer_schema` stage (InferMap) — not ported.
- MCP / A2A / REST servers: PORTED (v0.2.0). `src/node/mcp/server.ts` exposes the same 4 tools as the Python sibling (`list_stages`, `validate_pipeline`, `run_pipeline`, `explain_pipeline`) over hand-rolled JSON-RPC stdio (no MCP SDK dep), wired to a `goldenpipe-mcp` bin + `goldenpipe-js mcp-serve`. `a2a/server.ts` (4 skills, `agent-serve`) and `api/server.ts` (`/health`, `/stages`, `/validate`, `/run`, `serve`) mirror the Python `a2a/server.py` + `api/server.py`. All three delegate to the MCP tool handler. MCP Streamable-HTTP transport is not ported (stdio only).
- Textual TUI — not ported.
- `severityGate` / `piiRouter` are effectively no-ops vs current GoldenCheck-JS output (no `"critical"` severity, no `"pii_detection"` check). Ported for structural parity only. `rowCountGate` works but is not wired into the default chain.

## Sibling skew artifacts

`golden` <- `goldenRecords`; `scored_pairs` <- `scoredPairs`; `matchkey_used` derived from the built config (JS result carries no resolved matchkey list). The Python `goldencheck.scan` adapter uses `scan_file(path)` (fails the in-memory `run_df` path); the TS adapter scans rows so it succeeds in both `runDf` and `run`.

## Verify (from worktree root)

```bash
pnpm install
pnpm turbo run build --filter=goldenpipe^...   # builds the 3 sibling deps
pnpm turbo run build --filter=goldenpipe
cd packages/typescript/goldenpipe && npx tsc --noEmit && npx vitest run
```

The CLI smoke test (`tests/unit/cli.test.ts`) runs `dist/cli.cjs` via subprocess. It `describe.skipIf`s itself when the CLI isn't built, so the publish workflow (which runs vitest before `pnpm run build`) doesn't fail; it runs wherever the build precedes vitest (local dev, and the regular CI TS lane).

## Parity

`scripts/emit_ts_parity_fixtures.py` (in the Python package) runs `goldenpipe.run` on small CSVs and dumps skew-robust invariants to `tests/fixtures/pipe_parity.json`. The Python siblings must be installed in the uv venv first (`uv pip install -e packages/python/{goldencheck,goldenflow,goldenmatch}`) — they're optional deps of the Python goldenpipe. `tests/parity/pipe-parity.test.ts` asserts status / input_rows / per-stage status sequence / final golden+unique counts. Python `golden_count: null` (no golden records) is normalized to TS `0`.
