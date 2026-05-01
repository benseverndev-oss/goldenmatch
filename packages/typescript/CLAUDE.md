# TypeScript packages — Claude notes

## Not a real npm workspace

Each package has its own `package.json` and installs independently. This is intentional — Windows symlinks fail with EISDIR on `file:../sibling` paths.

## Cross-package deps: the `.vendor/` tarball pattern

When package A depends on package B (in this monorepo):

1. In B: `npm run build && npm pack --pack-destination /d/mr/cleanup-staging/.vendor/`
2. In A: `"dependencies": { "B": "file:../../../.vendor/B-<version>.tgz" }`
3. `npm install` in A.

Plain `file:../B` will fail on Windows with `EISDIR: illegal operation on a directory, symlink ...`.

Tarballs in `.vendor/` are committed to git (small, deterministic, build-output only).

## Style / convention

- camelCase for fields (`sampleValues`, `sourceName`, `typeName`) — even though Python siblings use `snake_case`.
- Type-only imports: `import type { … }` to stay edge-safe.
- `.js` suffix on relative imports (NodeNext / Bundler module resolution).

## Tooling

- Test: `npm test` in each package (vitest). To run a single test file: `npx vitest run tests/<file>.test.ts`.
- Build: `tsup` (or `tsc` for goldencheck-types). Build artifacts to `dist/`, gitignored.

## Known pre-existing failures

- `infermap` parity test references `packages/tests/fixtures/parity_cases.json` (path was valid pre-monorepo, now broken). Not introduced by handoff work.
