# pnpm Workspaces + Turbo Migration

**Date:** 2026-05-02
**Status:** Design approved, awaiting implementation plan
**Parent:** `2026-05-02-performance-audit-checklist.md` (item #8)

## Goal

Convert `D:\show_case\goldenmatch` from its current "not a real workspace" TypeScript layout (5 independent packages, no lockfiles, bash-loop orchestration) to a pnpm workspace with Turborepo on top. Unblocks committed lockfiles → CI dependency caching → cross-package code sharing.

## Background

Current state (`packages/typescript/{goldenmatch,goldencheck,goldencheck-types,goldenflow,infermap}`):

- 4 of 5 packages have near-identical devDependencies (tsup, typescript, vitest, rimraf, @types/node, yaml). Heavy duplication on every install.
- 5th (`goldencheck-types`) is currently empty — no `package.json`.
- No package-lock.json files exist anywhere → blocks both `npm ci` and `setup-node`'s `cache: npm`.
- Root `package.json` uses bash for-loops (`for d in packages/typescript/*; do ...`) to fan-out install/test/build/lint. Sequential, no caching.
- Comment in root `package.json` claims this is to "avoid Windows symlink issues" — a stale concern. User has Windows Dev Mode enabled, which permits non-admin symlink creation.
- No package currently imports another by name, but the user has confirmed cross-package sharing is planned (B answer in brainstorm).

## Non-goals

- Turbo remote cache (Vercel-hosted) — deferred until local cache benefits are measured
- Actual cross-package code sharing (e.g., `goldencheck` consuming `goldencheck-types`) — set up the plumbing only; populate when real shared code exists
- ESLint setup — `lint` task will be `tsc --noEmit` until ESLint lands
- Dependency hoisting cleanup (deduplicating versions across packages) — separate cleanup PR

## Design

### 1. Workspace architecture

**New / changed root files:**

| File | Purpose |
|---|---|
| `package.json` | Add `"packageManager": "pnpm@9.15.0"` (exact semver — Corepack and `pnpm/action-setup` reject ranges like `9.x`), `"private": true`, `"engines": { "node": ">=20" }`; drop `*:all` bash-loop scripts; remove "Not a real npm workspace" description |
| `pnpm-workspace.yaml` | `packages: ['packages/typescript/*']` |
| `pnpm-lock.yaml` | Single root lockfile (committed) |
| `turbo.json` | Pipeline definition (Section 2) |
| `.gitignore` | Ensure `.turbo/` and `node_modules/` are present (likely already there — verify, don't duplicate) |
| `README.md` | One line on Dev Mode requirement |

**Per-package:** Existing `packages/typescript/*/package.json` files keep their devDeps. When packages start consuming each other, use `"workspace:*"` protocol.

**`goldencheck-types` initialization** (currently empty): create `package.json` + `src/index.ts` stub now, since it's the obvious shared-types target the name implies.

**Windows guardrail:** Add to root `README.md`:
> **Windows users:** pnpm requires symlink creation. Enable Developer Mode (Settings → For Developers → Developer Mode → On) before running `pnpm install`, or you'll see `EPERM` symlink errors.

### 2. Turbo pipeline

`turbo.json`:

```json
{
  "$schema": "https://turbo.build/schema.json",
  "ui": "stream",
  "tasks": {
    "build": {
      "dependsOn": ["^build"],
      "inputs": ["src/**", "tsconfig*.json", "package.json", "tsup.config.*"],
      "outputs": ["dist/**"]
    },
    "test": {
      "dependsOn": ["^build"],
      "inputs": ["src/**", "tests/**", "vitest.config.*", "package.json"],
      "outputs": []
    },
    "lint": {
      "inputs": ["src/**", "tests/**", ".eslintrc*", "eslint.config.*"],
      "outputs": []
    },
    "typecheck": {
      "dependsOn": ["^build"],
      "inputs": ["src/**", "tests/**", "tsconfig*.json"],
      "outputs": []
    },
    "clean": { "cache": false }
  }
}
```

**Rationale:**

- `dependsOn: ["^build"]` on build/test/typecheck builds workspace-deps first. No-op today, free dependency-graph parallelism the moment cross-deps appear.
- Explicit `inputs` (vs. turbo's default of "all git-tracked files in the package") prevents `README.md` edits from invalidating test cache.
- `outputs: []` for test/lint/typecheck caches the *fact that they passed* (exit code + stdout) without trying to cache binaries.
- No remote cache configuration. Local `.turbo/` only. Adding Vercel later is `turbo login` away.

**Per-package script alignment:** every `packages/typescript/*/package.json` needs the same script names so turbo can find them:

| Script | Today | Action |
|---|---|---|
| `build` | present (tsup) | keep |
| `test` | present (vitest) | keep |
| `clean` | absent | add `rimraf dist` |
| `typecheck` | absent | add `tsc --noEmit` |
| `lint` | absent | add `tsc --noEmit` (placeholder until ESLint) |

### 3. CI integration

**Replace the entire `typescript` matrix job** in `.github/workflows/ci.yml`:

```yaml
typescript:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - uses: pnpm/action-setup@v4
      # Reads version from root package.json `packageManager` field. That field
      # MUST be an exact semver (e.g. pnpm@9.15.0) — ranges like 9.x will error.
    - uses: actions/setup-node@v4
      with:
        node-version: 20
        cache: pnpm
      # cache: pnpm requires (a) pnpm on PATH (handled by step above) and
      # (b) pnpm-lock.yaml present at checkout. Both are satisfied because the
      # lockfile is committed in the same PR as this CI change (see migration step 4/7).
    - run: pnpm install --frozen-lockfile
    - uses: actions/cache@v4
      with:
        path: .turbo
        key: turbo-${{ github.sha }}
        restore-keys: turbo-
    - run: pnpm turbo run build test typecheck lint
```

**Wins:**

- Single install replaces 4 matrix entries. pnpm hoists shared deps once.
- `cache: pnpm` on `setup-node` handles pnpm-store caching on lockfile hash — no extra `actions/cache` needed for that.
- `actions/cache@v4` on `.turbo/` enables cross-run task caching: unchanged packages are skipped.
- Fewer runner-minutes, faster wall time.

**Cleanup in same PR:**

- Remove the now-stale "no lockfiles yet" deferral comment in `ci.yml`'s typescript job.
- Mark task #8 in the parent checklist as complete.

## Migration sequence

> **Critical:** steps 4 and 7 must ship in the **same PR**. Splitting them breaks CI — `pnpm install --frozen-lockfile` and `cache: pnpm` both require the committed lockfile to be present at checkout.

1. Initialize `goldencheck-types` (minimal `package.json` + `src/index.ts`).
2. Add `pnpm-workspace.yaml`, `turbo.json`. Update root `package.json` (`packageManager` pinned to exact semver, `private`, `engines.node`, drop bash-loop scripts, remove old description).
3. Add `clean` / `typecheck` / `lint` scripts to each package's `package.json`. Add matching `engines.node` if the spec wants per-package alignment.
4. Run `pnpm install` locally — generates `pnpm-lock.yaml`. Smoke test for Dev Mode / Windows symlink errors. **Commit the lockfile.**
5. Run `pnpm turbo run build test typecheck` locally — smoke test the pipeline end-to-end. If tsup/vitest fail with "cannot find module" peer-dep errors, see risk table for `shamefully-hoist=true` escape hatch.
6. Verify `.gitignore` covers `.turbo/` and `node_modules/`; add only what's missing.
7. Replace the typescript CI job per Section 3 (same PR as step 4's lockfile commit).
8. Add Dev Mode note to root `README.md`.
9. Mark task #8 complete in the parent checklist; remove the npm-cache deferral comment in `ci.yml`.

## Risks / known unknowns

| Risk | Likelihood | Mitigation |
|---|---|---|
| `pnpm install` surfaces dep resolution conflicts (e.g., two packages on different tsup minors) | Medium | Align to the highest minor in the implementation plan, not in execution. |
| Windows Dev Mode somehow off | Low (user confirmed) | Step 4 fails fast with EPERM — non-destructive. |
| **tsup / vitest fail under pnpm's strict node_modules layout** (peer or transitive deps not declared, worked under npm's flat hoisting) | **Medium-High** | Most common pnpm-migration failure mode. Prefer fixing missing peer declarations first. Escape hatch: add `.npmrc` with `shamefully-hoist=true` to mimic npm's flat layout — accept the loss of strictness in exchange for compatibility. |
| Vitest 4.1 + pnpm hoisting confuses test runner | Low | Add `hoist-pattern` in `.npmrc` if it surfaces. |
| Turbo invalidates more aggressively than expected on first runs | Low | Acceptable — first warm fill is one-time; subsequent runs benefit. |
| Future root-level `tsconfig.base.json` not in turbo `inputs`, so its edits don't bust per-package caches | Low (no root tsconfig today) | When a root tsconfig is introduced, add it to each task's `inputs` glob, or switch to `"$TURBO_DEFAULT$"`. |

## Acceptance criteria

- `pnpm install` succeeds on Windows with Dev Mode and on Ubuntu CI.
- `pnpm turbo run build test typecheck` passes locally and in CI.
- Second consecutive CI run with no source changes hits the turbo cache (visible in run logs as "FULL TURBO" or per-task `cache hit`).
- pnpm-store cache hit visible in the `setup-node` step on subsequent CI runs.
- No regression in any package's test count vs. pre-migration baseline, verified via `pnpm --filter <pkg> test --reporter=verbose`.

## Working agreement

- Implementation goes through the writing-plans skill next; this spec is the input.
- Before claiming the migration is done, capture before/after numbers for: TS install time (cold + warm), TS CI job wall time (cold + warm), local `pnpm turbo run build` wall time on a no-op rerun.
