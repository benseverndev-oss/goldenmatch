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

## Known pre-existing failures

- `infermap` parity test references `packages/tests/fixtures/parity_cases.json` (path was valid pre-monorepo, now broken). Not introduced by handoff work.
