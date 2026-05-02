# pnpm + Turbo Migration Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate `D:\show_case\goldenmatch` TypeScript packages from per-package npm with bash-loop orchestration to a single pnpm workspace with Turborepo, generate the first committed lockfile, and replace the typescript CI matrix with a single cached job.

**Architecture:** One root `pnpm-workspace.yaml` over `packages/typescript/*`. One `turbo.json` defining `build`/`test`/`typecheck`/`lint`/`clean` tasks with explicit inputs and `^build` dependencies. CI: pnpm install once, turbo runs everything with `.turbo/` cached across runs.

**Tech Stack:** pnpm 9.15.0 (exact pin required by Corepack/pnpm-action-setup), Turborepo (latest), Node 20, existing tsup + vitest per package.

**Spec:** `docs/superpowers/specs/2026-05-02-pnpm-turbo-migration.md`

**Pre-existing state confirmed:**
- `.gitignore` already covers `node_modules/`. Only `.turbo/` needs adding.
- `goldencheck-types` directory exists with `domains/*.yaml` schemas, `tests/`, `README.md`, `CONTRIBUTING.md` — but no `package.json`. Treat as a real content package, not an empty stub.
- 4 of 5 packages already have `build`/`test`/`typecheck`/`clean` scripts. Only `lint` is missing on `goldencheck`/`goldenflow`/`infermap`. `goldenmatch` has `lint` but no `clean`.
- `goldenmatch` declares heavy optional peerDependencies (ink, react, transformers, hnswlib) — verify they install cleanly under pnpm.

**Same-PR constraint:** All tasks below ship as a single PR. Do not split. The CI change (Task 8) requires the lockfile from Task 6.

---

## Task 1: Initialize `goldencheck-types` package

**Files:**
- Create: `packages/typescript/goldencheck-types/package.json`
- Create: `packages/typescript/goldencheck-types/src/index.ts`
- Create: `packages/typescript/goldencheck-types/tsconfig.json`
- Create: `packages/typescript/goldencheck-types/tsup.config.ts`

**Why:** It's the only TS package without a `package.json`. pnpm-workspace will fail to enumerate it otherwise. It already has real content (yaml schemas under `domains/`), so it's not throwaway.

- [ ] **Step 1: Create `package.json`**

```json
{
  "name": "goldencheck-types",
  "version": "0.1.0",
  "description": "Shared TypeScript types and domain schemas for the goldencheck data-quality toolkit",
  "type": "module",
  "main": "./dist/index.js",
  "types": "./dist/index.d.ts",
  "exports": {
    ".": { "types": "./dist/index.d.ts", "import": "./dist/index.js" }
  },
  "files": ["dist", "domains"],
  "engines": { "node": ">=20" },
  "scripts": {
    "build": "tsup",
    "test": "vitest run --passWithNoTests",
    "typecheck": "tsc --noEmit",
    "lint": "tsc --noEmit",
    "clean": "rimraf dist"
  },
  "devDependencies": {
    "@types/node": "^20.0.0",
    "rimraf": "^5.0.0",
    "tsup": "^8.5.1",
    "typescript": "^5.4.0",
    "vitest": "^4.1.0"
  }
}
```

- [ ] **Step 2: Create minimal `src/index.ts`**

```typescript
// Stub entry — real types will land as goldencheck starts consuming this package.
// Domain schemas live in ./domains/*.yaml and are loaded at runtime by consumers.
export const PACKAGE_NAME = "goldencheck-types";
```

- [ ] **Step 3: Create `tsconfig.json` (mirror sibling packages)**

Copy structure from `packages/typescript/goldencheck/tsconfig.json`. If it doesn't exist, use:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "declaration": true,
    "outDir": "dist"
  },
  "include": ["src/**/*"]
}
```

- [ ] **Step 4: Create `tsup.config.ts`**

```typescript
import { defineConfig } from "tsup";

export default defineConfig({
  entry: ["src/index.ts"],
  format: ["esm"],
  dts: true,
  clean: true,
});
```

- [ ] **Step 5: DO NOT `npm install` here**

Skipped intentionally. Running `npm install` in this package now would generate a stray `package-lock.json` + flat `node_modules/` that collides with pnpm in Task 6. Validation of this package happens via the workspace-wide `pnpm install` in Task 6.

- [ ] **Step 6: Commit**

```bash
git add packages/typescript/goldencheck-types/package.json packages/typescript/goldencheck-types/src/ packages/typescript/goldencheck-types/tsconfig.json packages/typescript/goldencheck-types/tsup.config.ts
git commit -m "feat(goldencheck-types): initialize package with build config

Required so pnpm-workspace can enumerate every packages/typescript/* directory."
```

---

## Task 2: Add missing per-package scripts

**Files:**
- Modify: `packages/typescript/goldenmatch/package.json` (add `clean`)
- Modify: `packages/typescript/goldencheck/package.json` (add `lint`)
- Modify: `packages/typescript/goldenflow/package.json` (add `lint`)
- Modify: `packages/typescript/infermap/package.json` (add `lint`)

**Why:** Turbo needs the same script names across all packages so a single `pnpm turbo run lint` works. `lint` is `tsc --noEmit` until ESLint lands (per spec).

**Note on script delta vs. spec:** the spec's Section 2 table shows `clean` as "absent" across all four packages. **That table is stale.** Live inspection (`jq '.scripts.clean' packages/typescript/*/package.json`) shows `clean: rimraf dist` already present on goldencheck, goldenflow, and infermap — only goldenmatch is missing it. This plan reflects the actual current state, not the outdated table.

- [ ] **Step 1: Add `clean` to goldenmatch**

In `packages/typescript/goldenmatch/package.json`, add to `scripts`:

```json
"clean": "rimraf dist"
```

(Verify `rimraf` is already in `devDependencies` — it is, from inspection.)

- [ ] **Step 2: Add `lint` to goldencheck, goldenflow, infermap**

In each, add to `scripts`:

```json
"lint": "tsc --noEmit"
```

- [ ] **Step 3: Verify scripts run**

Run: `cd packages/typescript/goldenmatch && npm run clean && npm run lint`
Expected: clean removes dist if present, lint exits 0 (or surfaces real existing typecheck errors — fix or note them, do not mask).

Repeat spot-check on one other package.

- [ ] **Step 4: Commit**

```bash
git add packages/typescript/*/package.json
git commit -m "chore(ts): align per-package scripts (clean, lint) for turbo"
```

---

## Task 3: Add `pnpm-workspace.yaml`

**Files:**
- Create: `pnpm-workspace.yaml`

- [ ] **Step 1: Create the file**

```yaml
packages:
  - "packages/typescript/*"
```

- [ ] **Step 2: Commit**

```bash
git add pnpm-workspace.yaml
git commit -m "feat: add pnpm-workspace.yaml covering packages/typescript/*"
```

---

## Task 4: Update root `package.json`

**Files:**
- Modify: `package.json`

**Why:** Pin the package manager (exact semver — Corepack rejects ranges), declare engines, drop the bash-loop orchestration that pnpm/turbo replace.

- [ ] **Step 1: Replace root `package.json` with:**

```json
{
  "name": "goldenmatch-monorepo",
  "private": true,
  "packageManager": "pnpm@9.15.0",
  "engines": { "node": ">=20" },
  "scripts": {
    "build": "turbo run build",
    "test": "turbo run test",
    "typecheck": "turbo run typecheck",
    "lint": "turbo run lint",
    "clean": "turbo run clean"
  },
  "devDependencies": {
    "turbo": "^2.0.0"
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add package.json
git commit -m "chore: switch root package.json to pnpm + turbo orchestration

- Pin pnpm@9.15.0 (exact semver required by Corepack/pnpm-action-setup)
- Add engines.node >=20
- Replace bash-loop *:all scripts with turbo run <task>
- Drop 'not a real npm workspace' description"
```

---

## Task 5: Add `turbo.json`

**Files:**
- Create: `turbo.json`

- [ ] **Step 1: Create the file (verbatim from spec Section 2)**

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

- [ ] **Step 2: Add `.turbo/` to `.gitignore`**

Append to `.gitignore` (do not duplicate `node_modules/` — already present):

```
# Turborepo
.turbo/
```

- [ ] **Step 3: Commit**

```bash
git add turbo.json .gitignore
git commit -m "feat: add turbo pipeline config

Tasks: build, test, lint, typecheck, clean.
build/test/typecheck depend on ^build (workspace deps build first).
Explicit inputs prevent README edits from busting test cache.
test/lint/typecheck cache pass/fail without binary outputs."
```

---

## Task 6: Generate the lockfile (smoke test moment)

**Files:**
- Create: `pnpm-lock.yaml`
- Possibly create: `.npmrc` (only if Step 3 surfaces peer-dep failures)

**Why:** This is the critical local validation. If Windows Dev Mode is off, pnpm strict mode breaks `tsup`/`vitest`, or any package has unresolvable peer deps, this is where it surfaces — before any CI change.

- [ ] **Step 0: Clear stale npm-managed `node_modules/` before pnpm runs**

Prior `npm install` runs (via the bash-loop `install:all` script) left flat-layout `node_modules/` directories in each TS package. pnpm reusing or hoisting against those produces broken / silently-incorrect installs.

Run from repo root:

```powershell
Remove-Item -Recurse -Force node_modules, packages/typescript/*/node_modules -ErrorAction SilentlyContinue
Get-ChildItem -Path packages/typescript -Filter package-lock.json -Recurse | Remove-Item -Force
```

Expected: no output, exit 0. (Bash equivalent: `rm -rf node_modules packages/typescript/*/node_modules packages/typescript/*/package-lock.json`.)

- [ ] **Step 1: Enable Corepack so pnpm@9.15.0 is auto-installed**

Run: `corepack enable`
Expected: no output, exit 0. (One-time per dev machine; CI handles this via `pnpm/action-setup`.)

**Fallback if `corepack enable` fails (often needs admin shell on Windows):** install pnpm globally instead — `npm i -g pnpm@9.15.0`. Functionally equivalent for local dev.

- [ ] **Step 2: Run `pnpm install` from repo root**

Run: `pnpm install`
Expected: pnpm downloads, generates `pnpm-lock.yaml`, hoists shared deps to root `node_modules/.pnpm/`, links per-package `node_modules`.

**If you see EPERM symlink errors:** Windows Dev Mode is off. Settings → For Developers → Developer Mode → On. Then re-run.

**If you see peer-dependency warnings for goldenmatch's optional peers (ink, react, transformers, hnswlib, etc.):** these are declared as optional via `peerDependenciesMeta`, so warnings are expected — not failures. Proceed.

**If install actually fails with missing peer deps for tsup/vitest themselves:** create `.npmrc` at repo root:

```
shamefully-hoist=true
```

Re-run `pnpm install`. Document in commit message that this was needed.

- [ ] **Step 3: Verify lockfile generated and packages enumerated**

Run: `pnpm list --depth -1 --recursive`
Expected: lists all 5 workspace packages (goldenmatch, goldencheck, goldencheck-types, goldenflow, infermap) with their direct deps.

- [ ] **Step 4a: Run the full pipeline locally (cold)**

Run: `pnpm turbo run build test typecheck lint`
Expected: all 5 packages build, tests run, typecheck/lint pass.

- [ ] **Step 4b: If any task fails, STOP and triage in a separate task before continuing**

Pre-existing failures masked by the old bash-loop setup may surface here. Do **not** mask them with `continue-on-error` or task-level skips. Either fix forward in this PR (if scope is small) or open a follow-up issue and revert this plan to a smaller subset (e.g., exclude the failing package from the workspace temporarily). The goal is a green baseline before the CI swap.

- [ ] **Step 5: Run the pipeline a second time to confirm caching**

Run: `pnpm turbo run build test typecheck lint`
Expected: turbo reports `>>> FULL TURBO` or `cache hit, replaying logs` for every task. If anything re-runs without source changes, the `inputs` glob in `turbo.json` is over-matching — investigate.

- [ ] **Step 6: Commit lockfile (and `.npmrc` if created)**

```bash
git add pnpm-lock.yaml
# Only if shamefully-hoist was required:
# git add .npmrc
git commit -m "chore: add pnpm-lock.yaml from initial workspace install"
```

---

## Task 7: Verify per-package test counts haven't regressed

**Why:** Acceptance criterion. Pre-migration, each package's tests ran via its own `npm test`. Confirm the test count under pnpm + turbo matches.

- [ ] **Step 1: Capture current test counts**

Run: `pnpm --filter goldenmatch test --reporter=verbose 2>&1 | tail -3`
Repeat for goldencheck, goldencheck-types, goldenflow, infermap.

Record counts in `docs/superpowers/plans/2026-05-02-pnpm-turbo-migration.md` as a comment under this task, or in the PR description.

- [ ] **Step 2: Compare to pre-migration baseline**

Pre-migration baseline: run `cd packages/typescript/<pkg> && npm test` against a `git stash`'d state, OR check existing CI logs from a recent run.

If counts match: ✅ proceed. If counts differ: investigate before continuing — the migration may be skipping a test file.

- [ ] **Step 3: No commit needed (verification only)**

---

## Task 8: Replace the typescript CI job

**Files:**
- Modify: `.github/workflows/ci.yml`

**Why:** Single job replaces 4-entry matrix. Adds pnpm-store cache + turbo cache. This MUST ship in the same PR as the lockfile (Task 6) — `pnpm install --frozen-lockfile` and `cache: pnpm` both require the committed lockfile at checkout.

- [ ] **Step 1: Replace the entire `typescript:` job in `.github/workflows/ci.yml`**

Find the existing `typescript:` job (currently lines 42-59 with matrix entries goldenmatch/goldencheck/goldenflow/infermap). Replace with:

```yaml
  typescript:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v4
        # Reads version from root package.json `packageManager` field.
        # That field MUST be exact semver (pnpm@9.15.0) — ranges error.
      - uses: actions/setup-node@v4
        with:
          node-version: 20
          cache: pnpm
        # cache: pnpm requires (a) pnpm on PATH (handled above) and
        # (b) pnpm-lock.yaml present at checkout. Both satisfied because
        # the lockfile is committed in this same PR.
      - run: pnpm install --frozen-lockfile
      - uses: actions/cache@v4
        with:
          path: .turbo
          key: turbo-${{ github.sha }}
          restore-keys: turbo-
      - run: pnpm turbo run build test typecheck lint
```

- [ ] **Step 2: Verify yaml validity**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"`
Expected: no output, exit 0.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: replace typescript matrix job with pnpm + turbo

- Single job replaces 4-entry per-package matrix
- pnpm-store cached via setup-node cache: pnpm
- .turbo/ cached across runs for task-level caching
- Requires pnpm-lock.yaml committed in same PR (it is)"
```

---

## Task 9: Add Dev Mode note to README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Find an appropriate section in `README.md`**

Search for an existing "Getting Started", "Setup", or "Development" heading. If none exists, add a new `## Development` section near the top.

- [ ] **Step 2: Append this block to that section**

```markdown
### Windows: enable Developer Mode for pnpm

`pnpm install` creates symlinks under `node_modules/`. On Windows this requires either Developer Mode or admin privileges.

**Settings → For Developers → Developer Mode → On**

If you see `EPERM: operation not permitted, symlink ...` during `pnpm install`, Dev Mode is off.
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: note Windows Dev Mode requirement for pnpm install"
```

---

## Task 10: Close out parent checklist task #8

**Files:**
- Modify: `docs/superpowers/specs/2026-05-02-performance-audit-checklist.md` (verify exists before editing — it should from the parent brainstorm; if missing, skip this task and note in PR description)

- [ ] **Step 1: Find item "Generate + commit TS lockfiles, then enable npm CI cache" under "Monorepo tooling overhead"**

Mark it complete by changing `- [ ]` to `- [x]`. Add a short note pointing to this plan:

```markdown
- [x] **Reassess npm workspaces / adopt Turborepo**
  - Done via `docs/superpowers/specs/2026-05-02-pnpm-turbo-migration.md` and corresponding plan. Adopted pnpm workspaces + Turborepo.
```

(Apply the same checkbox flip to the related "Generate + commit TS lockfiles" item — it's superseded.)

- [ ] **Step 2: Remove the now-stale "no lockfiles yet" comment from `ci.yml`**

The Task 8 replacement already does this implicitly (the entire job was rewritten). Verify no stray comments remain referencing the old npm-cache deferral.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-05-02-performance-audit-checklist.md
git commit -m "docs: mark TS workspace/turbo item complete in perf checklist"
```

---

---

## Task 11: Capture before/after performance numbers

**Why:** Spec acceptance section requires recording timing deltas before claiming the migration is done.

- [ ] **Step 1: Capture baseline (run BEFORE any of Tasks 1-10 if not already done — otherwise estimate from a recent CI run)**

Record:
- `npm --prefix packages/typescript/goldenmatch install` cold time (no `~/.npm` cache)
- Sum of all 4 typescript matrix entries' wall-clock time on a recent main-branch CI run

- [ ] **Step 2: Capture after-numbers**

Record:
- `pnpm install` cold time (clean `~/.local/share/pnpm/store/v3` and `node_modules/`)
- `pnpm install --frozen-lockfile` warm time
- `pnpm turbo run build test typecheck lint` cold time
- `pnpm turbo run build test typecheck lint` warm (no-op) time — should be near-zero with full cache hit
- The new single typescript CI job's wall-clock on its first run after merge

- [ ] **Step 3: Add the four-row table to the PR description**

| Metric | Before | After |
|---|---|---|
| Install cold | ... | ... |
| Install warm | n/a (no lock) | ... |
| Pipeline cold | ... | ... |
| Pipeline warm (no-op) | ... | ... |
| CI typescript job wall time | ... | ... |

No commit needed — lives in the PR description.

---

## Acceptance verification (run before opening the PR)

- [ ] **A1:** `pnpm install --frozen-lockfile` succeeds locally on Windows w/ Dev Mode.
- [ ] **A2:** `pnpm turbo run build test typecheck lint` passes locally.
- [ ] **A3:** Second consecutive `pnpm turbo run build test typecheck lint` reports `FULL TURBO` / cache hits across all tasks.
- [ ] **A4:** Per-package test counts unchanged from pre-migration baseline (Task 7).
- [ ] **A5:** `python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"` succeeds.
- [ ] **A6:** Push branch, observe CI: typescript job goes green, pnpm-store cache hit visible on second run.

If A6 fails on first push: that's why `continue-on-error` is NOT set on this job — failures surface immediately, fix forward in the same PR.

---

## Risk reminders during execution

- **tsup/vitest fail with module resolution errors** — escape hatch: `.npmrc` with `shamefully-hoist=true`. Document in commit if used. (Spec risk table, Medium-High.)
- **EPERM on Windows** — Dev Mode off. Single-toggle fix.
- **Turbo over-invalidating cache on second run** — the `inputs` glob is too wide; tighten before merging.

---

## Out of scope (do NOT do as part of this plan)

- Turbo remote cache (Vercel) — separate decision later.
- Cross-package `workspace:*` consumption (e.g., `goldencheck` importing `goldencheck-types`) — plumbing only here; populate when real shared types exist.
- ESLint setup — `lint` stays as `tsc --noEmit` until ESLint lands separately.
- Dependency version deduplication across packages — separate cleanup PR.
