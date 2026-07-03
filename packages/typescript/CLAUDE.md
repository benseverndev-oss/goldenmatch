# TypeScript packages — Claude notes

## pnpm workspace (post-2026-05-02 fold)

These packages are a real pnpm workspace — `pnpm-workspace.yaml` globs
`packages/typescript/*`, and cross-package deps resolve via the workspace
protocol (`"goldencheck-types": "workspace:^"`), which pnpm links to the
sibling source (`link:../goldencheck-types` in `pnpm-lock.yaml`). See the root
CLAUDE.md "TypeScript: pnpm + Turborepo" section for the toolchain pins.

### Retired: the `.vendor/` tarball pattern

Pre-fold, each package installed independently under plain npm and cross-package
deps were vendored as committed `.tgz` tarballs (`npm pack` into `.vendor/`,
consumed via `"B": "file:../../../.vendor/B-<version>.tgz"`) because plain
`file:../B` symlinks fail on Windows with `EISDIR`. The pnpm workspace replaces
this — `workspace:^` handles the linking cross-platform. The stale
`.vendor/goldencheck-types-0.1.0.tgz` (unreferenced after the fold) was removed.

## Style / convention

- camelCase for fields (`sampleValues`, `sourceName`, `typeName`) — even though Python siblings use `snake_case`.
- **Exception:** `goldencheck-types/src/types.ts` keeps snake_case (`name_hints`, `value_signals`, `confidence_threshold`, `source_col`, `schema_version`) because those types pass through YAML on the producer side and JSON wire on the consumer side without remapping. Cross-language parity with the Python sibling at `packages/python/goldencheck-types/goldencheck_types/types.py` is more valuable here than language-idiomatic case style. The same exception applies to any TS code that constructs / consumes those interfaces directly.
- Type-only imports: `import type { … }` to stay edge-safe.
- `.js` suffix on relative imports (NodeNext / Bundler module resolution).

## Tooling

- Test: `npm test` in each package (vitest). To run a single test file: `npx vitest run tests/<file>.test.ts`.
- Build: `tsup` (or `tsc` for goldencheck-types). Build artifacts to `dist/`, gitignored.

## Shared opt-in WASM runtime (`goldenmatch-wasm-runtime`)

- `goldenmatch-wasm-runtime` is a tiny zero-dep workspace package holding the
  shared opt-in WASM plumbing: `resolveWasmBytes(opts, fallbackUrl)` (edge-safe
  byte loader + env detection), `enableWasmBackend<B>(opts, instantiate, register,
  fallbackUrl)` (the generic enable skeleton), and `createBackendRegistry<B>()`
  (the module-singleton). It owns NO domain logic, NO artifact URL, NO glue import.
- **Each consumer owns its artifact URL + glue import + backend interface.** The
  `new URL('./artifacts/<name>_bg.wasm', import.meta.url)` and the dynamic glue
  `import('./artifacts/<name>.js')` MUST live in the consumer's own module so
  `import.meta.url` resolves to that package's `dist` — passing the URL into the
  shared package would resolve to the wrong location.
- Consumers: `goldenmatch` (score-wasm → `scoreMatrix`, `enableWasm`) and
  `goldenanalysis` (analysis-wasm → `histogram`/`quantile`, `enableAnalysisWasm`).
  Both depend via `workspace:^`. Pure-TS stays the default + fallback; the `.wasm`
  is built in CI (`wasm_score` / `analysis_wasm` lanes), never committed.
- Adding a new accelerated core: new `*-wasm` crate (mirror `score-wasm`), a
  consumer `src/core/wasm/` (backend + loader + index using the shared runtime),
  wire the batch boundary, a skip-guarded parity test + bench, and a CI lane.

## Committed wasm parity fixtures: the `fixture_drift` backstop
A committed `build_*_wasm.mjs`-generated fixture (`tests/parity/fixtures/<core>/`)
can silently drift from its Rust core. Each surface has a per-core `*_wasm` drift
guard (rebuild + diff), but those are path-filtered — they only fire when that
surface's own paths change, so a stale fixture carried by a branch that never
touched those paths slips through until a full-matrix run (this is exactly how a
stale goldengraph fixture failed PR #1398 four times in the merge queue). The
**`fixture_drift` CI job** (root `ci.yml`, gated on the broad `rust_cores` filter
= any `packages/rust/extensions/**` change) is the catch-all: it regenerates
EVERY committed wasm parity fixture from its live core and diffs, so a stale
sibling fails on the PR. New `build_*_wasm.mjs` scripts are picked up
automatically (it globs `packages/typescript/*/scripts/build_*_wasm.mjs`) — no
job edit needed. It diffs ONLY `tests/parity/fixtures/**`, never the
toolchain-variant wasm bytes.

## Known pre-existing failures

- `infermap` parity test references `packages/tests/fixtures/parity_cases.json` (path was valid pre-monorepo, now broken). Not introduced by handoff work.
