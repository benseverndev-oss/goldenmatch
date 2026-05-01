# Goldenmatch Monorepo Fold-In Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fold 8 sibling GitHub repos into the existing `goldenmatch` repo as a polyglot monorepo with full git history preserved for every source.

**Architecture:** Use `git filter-repo` to rewrite each source repo's history under its target prefix in `packages/<lang>/<name>/`. For the four parity repos (python+TS), do two filter-repo passes producing two rewritten clones each. Merge every rewritten clone into a fresh `monorepo-staging` working tree using `git merge --allow-unrelated-histories`. Add monorepo-level workspace plumbing (uv, fake JS workspace, cargo, just, CI). Verify per-language builds, then tag-and-force-push to `benzsevern/goldenmatch`.

**Note on deliberate spec divergence:** The spec describes performing the goldenmatch rewrite "in place" inside the staging clone. This plan treats goldenmatch symmetrically with the other parity repos — staging starts as an empty repo with a placeholder root commit, and goldenmatch's two filter-repo passes are merged in just like every other source. End state matches the spec; the procedure is cleaner and avoids special-casing.

**Tech Stack:** `git filter-repo`, `uv`, `cargo`, `npm`, `just`, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-05-01-goldenmatch-monorepo-fold-in-design.md`

---

## Pre-flight requirements

- `git filter-repo` installed (`pip install git-filter-repo` or `winget install --id Git.git-filter-repo`).
- `just` installed (`winget install --id Casey.Just`).
- `uv` installed.
- `cargo` toolchain installed.
- `gh` CLI authenticated as `benzsevern`.
- ~5 GB free disk (rewritten clones × 9, plus staging).
- Path: scratch dir at `D:/mr/` (short path avoids Windows path-length issues during filter-repo on deep histories).

## Conventions

- All commands run from `D:/mr/` unless stated.
- "Verify" means run the command and confirm the expected line/count appears.
- Commit at the end of every task that mutates the staging repo.

---

## Phase 0 — Safety net

### Task 0.1: Tag and back up current `goldenmatch` remote `main`

**Files:** none (remote-only operation)

- [ ] **Step 1: Fetch fresh remote state**

```bash
mkdir -p D:/mr && cd D:/mr
git clone https://github.com/benzsevern/goldenmatch.git goldenmatch-source
cd goldenmatch-source
git fetch --all --tags
```

Expected: clone completes, `git log -1 --oneline` shows current `main` HEAD.

- [ ] **Step 2: Create the safety tag and push it to remote**

```bash
git tag -a main-pre-monorepo -m "State of main before monorepo fold-in (2026-05-01)"
git push origin main-pre-monorepo
```

Verify on GitHub: https://github.com/benzsevern/goldenmatch/releases/tag/main-pre-monorepo exists.

- [ ] **Step 3: Verify safety tag is reachable from a fresh clone**

```bash
cd D:/mr && git clone --depth 1 --branch main-pre-monorepo https://github.com/benzsevern/goldenmatch.git verify-tag
ls verify-tag
rm -rf verify-tag
```

Expected: clone succeeds with the pre-monorepo tree.

### Task 0.2: Mirror-clone every source repo

**Files:** none (creates working clones in `D:/mr/sources/`)

- [ ] **Step 1: Clone all 9 repos with `--no-local` (so filter-repo can rewrite freely)**

```bash
mkdir -p D:/mr/sources && cd D:/mr/sources
for r in goldenmatch goldencheck goldenflow goldenpipe infermap \
         goldencheck-types goldenmatch-extensions dbt-goldencheck goldencheck-action; do
  git clone --no-local "https://github.com/benzsevern/$r.git" "$r"
done
```

- [ ] **Step 2: Switch `goldenmatch-extensions` to its feature branch**

```bash
cd D:/mr/sources/goldenmatch-extensions
git checkout feature/ai-coder-docs
git rev-parse --abbrev-ref HEAD
```

Expected: `feature/ai-coder-docs`.

- [ ] **Step 3: Verify each clone has its full history**

```bash
cd D:/mr/sources
for r in */; do echo "=== $r ==="; git -C "$r" rev-list --count HEAD; done
```

Expected: each repo prints its commit count (>0).

---

## Phase 1 — Initialize the staging monorepo

### Task 1.1: Create empty staging repo with a root commit

**Files:**
- Create: `D:/mr/monorepo-staging/.gitignore`
- Create: `D:/mr/monorepo-staging/README.md`

- [ ] **Step 1: Initialize**

```bash
cd D:/mr && git init monorepo-staging
cd monorepo-staging
git checkout -b main
```

- [ ] **Step 2: Write a minimal placeholder root README**

Path: `D:/mr/monorepo-staging/README.md`

```markdown
# Golden Suite Monorepo

This repository hosts the Golden Suite. See `docs/superpowers/specs/2026-05-01-goldenmatch-monorepo-fold-in-design.md`.

Languages and packages live under `packages/<lang>/<name>/`.
```

- [ ] **Step 3: Write a placeholder `.gitignore`** (will be expanded in Phase 5)

Path: `D:/mr/monorepo-staging/.gitignore`

```
# Build artifacts
target/
dist/
build/
*.egg-info/
node_modules/
__pycache__/
.venv/
.uv-cache/

# Generated outputs
*_lineage.json
*_clusters.csv

# IDE
.vscode/
.idea/
```

- [ ] **Step 4: Make the root commit**

```bash
git add README.md .gitignore
git commit -m "chore: initialize monorepo staging"
```

Verify: `git log --oneline` shows one commit.

---

## Phase 2 — Rewrite parity repos (Python + TS, four repos × two passes)

For each of `goldenmatch`, `goldencheck`, `goldenflow`, `infermap`, do two filter-repo passes producing `<name>-py-rewritten` and `<name>-ts-rewritten`.

### Task 2.1: Rewrite `goldenmatch` (Python pass)

**Files:** clones in `D:/mr/sources/`, output to `D:/mr/rewritten/goldenmatch-py/`

- [ ] **Step 1: Make a fresh clone for the rewrite**

```bash
cd D:/mr && mkdir -p rewritten
git clone --no-local D:/mr/sources/goldenmatch rewritten/goldenmatch-py
cd rewritten/goldenmatch-py
```

- [ ] **Step 2: Drop the JS port and generated artifacts**

```bash
git filter-repo \
  --invert-paths \
  --path packages/goldenmatch-js/ \
  --path-glob '*_lineage.json' \
  --path-glob '*_clusters.csv' \
  --path dist/ \
  --path build/ \
  --path-glob '*.egg-info/'
```

Expected: filter-repo prints stats; HEAD changes; no errors.

- [ ] **Step 3: Re-root everything into `packages/python/goldenmatch/`**

```bash
git filter-repo --to-subdirectory-filter packages/python/goldenmatch
```

Verify:

```bash
git log --name-only --pretty=format: | head -20 | sort -u
```

Expected: every path begins with `packages/python/goldenmatch/`.

### Task 2.2: Rewrite `goldenmatch` (TypeScript pass)

**Files:** clones in `D:/mr/sources/`, output to `D:/mr/rewritten/goldenmatch-ts/`

- [ ] **Step 1: Fresh clone**

```bash
cd D:/mr && git clone --no-local sources/goldenmatch rewritten/goldenmatch-ts
cd rewritten/goldenmatch-ts
```

- [ ] **Step 2: Keep only the JS port**

```bash
git filter-repo --subdirectory-filter packages/goldenmatch-js
```

Expected: tree now contains the JS package's contents at root (no `packages/goldenmatch-js/` prefix).

- [ ] **Step 3: Re-root into `packages/typescript/goldenmatch/`**

```bash
git filter-repo --to-subdirectory-filter packages/typescript/goldenmatch
```

Verify: `git log --name-only --pretty=format: | head -20 | sort -u` — every path begins with `packages/typescript/goldenmatch/`.

### Task 2.3: Rewrite `goldencheck` (Python + TS passes)

Identical structure to 2.1 + 2.2, substituting `goldencheck` for `goldenmatch` and `goldencheck-js` for `goldenmatch-js`.

- [ ] **Step 1: Python pass**

```bash
cd D:/mr && git clone --no-local sources/goldencheck rewritten/goldencheck-py
cd rewritten/goldencheck-py
git filter-repo --invert-paths --path packages/goldencheck-js/ --path dist/ --path build/ --path-glob '*.egg-info/'
git filter-repo --to-subdirectory-filter packages/python/goldencheck
```

- [ ] **Step 2: TypeScript pass**

```bash
cd D:/mr && git clone --no-local sources/goldencheck rewritten/goldencheck-ts
cd rewritten/goldencheck-ts
git filter-repo --subdirectory-filter packages/goldencheck-js
git filter-repo --to-subdirectory-filter packages/typescript/goldencheck
```

- [ ] **Step 3: Verify both rewrites root correctly**

```bash
for d in D:/mr/rewritten/goldencheck-py D:/mr/rewritten/goldencheck-ts; do
  echo "=== $d ==="
  git -C "$d" log --name-only --pretty=format: | head -5 | sort -u
done
```

### Task 2.4: Rewrite `goldenflow` (Python + TS passes)

Same as 2.3 with `goldenflow` / `goldenflow-js`.

- [ ] **Step 1: Python pass**

```bash
cd D:/mr && git clone --no-local sources/goldenflow rewritten/goldenflow-py
cd rewritten/goldenflow-py
git filter-repo --invert-paths --path packages/goldenflow-js/ --path dist/ --path build/ --path-glob '*.egg-info/'
git filter-repo --to-subdirectory-filter packages/python/goldenflow
```

- [ ] **Step 2: TypeScript pass**

```bash
cd D:/mr && git clone --no-local sources/goldenflow rewritten/goldenflow-ts
cd rewritten/goldenflow-ts
git filter-repo --subdirectory-filter packages/goldenflow-js
git filter-repo --to-subdirectory-filter packages/typescript/goldenflow
```

- [ ] **Step 3: Verify**

```bash
for d in D:/mr/rewritten/goldenflow-py D:/mr/rewritten/goldenflow-ts; do
  git -C "$d" log --name-only --pretty=format: | head -5 | sort -u
done
```

### Task 2.5: Rewrite `infermap` (Python + TS passes)

Same as 2.3 with `infermap` / `infermap-js`.

- [ ] **Step 1: Python pass**

```bash
cd D:/mr && git clone --no-local sources/infermap rewritten/infermap-py
cd rewritten/infermap-py
git filter-repo --invert-paths --path packages/infermap-js/ --path dist/ --path build/ --path-glob '*.egg-info/'
git filter-repo --to-subdirectory-filter packages/python/infermap
```

- [ ] **Step 2: TypeScript pass**

```bash
cd D:/mr && git clone --no-local sources/infermap rewritten/infermap-ts
cd rewritten/infermap-ts
git filter-repo --subdirectory-filter packages/infermap-js
git filter-repo --to-subdirectory-filter packages/typescript/infermap
```

- [ ] **Step 3: Verify**

```bash
for d in D:/mr/rewritten/infermap-py D:/mr/rewritten/infermap-ts; do
  git -C "$d" log --name-only --pretty=format: | head -5 | sort -u
done
```

---

## Phase 3 — Rewrite single-language repos (one pass each)

### Task 3.1: Rewrite `goldenpipe` → `packages/python/goldenpipe/`

- [ ] **Step 1**

```bash
cd D:/mr && git clone --no-local sources/goldenpipe rewritten/goldenpipe-py
cd rewritten/goldenpipe-py
git filter-repo --invert-paths --path dist/ --path build/ --path-glob '*.egg-info/'
git filter-repo --to-subdirectory-filter packages/python/goldenpipe
```

Verify: `git log --name-only --pretty=format: | head -5 | sort -u` shows `packages/python/goldenpipe/` paths.

### Task 3.2: Rewrite `goldencheck-types` → `packages/typescript/goldencheck-types/`

- [ ] **Step 1**

```bash
cd D:/mr && git clone --no-local sources/goldencheck-types rewritten/goldencheck-types
cd rewritten/goldencheck-types
git filter-repo --invert-paths --path dist/ --path node_modules/
git filter-repo --to-subdirectory-filter packages/typescript/goldencheck-types
```

### Task 3.3: Rewrite `goldenmatch-extensions` → `packages/rust/extensions/` (from `feature/ai-coder-docs`)

- [ ] **Step 1: Clone the feature branch**

```bash
cd D:/mr && git clone --no-local --branch feature/ai-coder-docs sources/goldenmatch-extensions rewritten/extensions
cd rewritten/extensions
```

- [ ] **Step 2: Rewrite**

```bash
git filter-repo --invert-paths --path target/
git filter-repo --to-subdirectory-filter packages/rust/extensions
```

Verify: `git log --name-only --pretty=format: | head -5 | sort -u` shows `packages/rust/extensions/`.

### Task 3.4: Rewrite `dbt-goldencheck` → `packages/dbt/goldencheck/`

- [ ] **Step 1**

```bash
cd D:/mr && git clone --no-local sources/dbt-goldencheck rewritten/dbt-goldencheck
cd rewritten/dbt-goldencheck
git filter-repo --to-subdirectory-filter packages/dbt/goldencheck
```

### Task 3.5: Rewrite `goldencheck-action` → `packages/actions/goldencheck/`

- [ ] **Step 1**

```bash
cd D:/mr && git clone --no-local sources/goldencheck-action rewritten/goldencheck-action
cd rewritten/goldencheck-action
git filter-repo --to-subdirectory-filter packages/actions/goldencheck
```

---

## Phase 4 — Merge every rewrite into staging

### Task 4.1: Add each rewritten clone as a remote and merge

**Files:** `D:/mr/monorepo-staging/` (working tree mutated)

- [ ] **Step 1: Add remotes**

```bash
cd D:/mr/monorepo-staging
for r in goldenmatch-py goldenmatch-ts \
         goldencheck-py goldencheck-ts \
         goldenflow-py goldenflow-ts \
         infermap-py infermap-ts \
         goldenpipe-py goldencheck-types extensions \
         dbt-goldencheck goldencheck-action; do
  git remote add "$r" "D:/mr/rewritten/$r"
  git fetch "$r"
done
```

Verify: `git remote -v` lists 13 entries.

- [ ] **Step 2: Merge each, in deterministic order**

```bash
for r in goldenmatch-py goldenmatch-ts \
         goldencheck-py goldencheck-ts \
         goldenflow-py goldenflow-ts \
         infermap-py infermap-ts \
         goldenpipe-py goldencheck-types extensions \
         dbt-goldencheck goldencheck-action; do
  echo "=== Merging $r ==="
  git merge --allow-unrelated-histories --no-ff -m "merge: fold $r into monorepo" "$r/HEAD"
done
```

Expected: each merge succeeds with no conflicts (every rewrite touches a disjoint subdirectory).

If any merge conflicts: stop and surface — that means a re-root was incomplete.

- [ ] **Step 3: Sanity-check the result**

```bash
ls packages/python packages/typescript packages/rust packages/dbt packages/actions
git log --oneline | wc -l
```

Expected: every package directory exists; commit count ≈ sum of all rewritten histories + 14 merge commits + 1 root.

---

## Phase 5 — Add monorepo plumbing

### Task 5.1: Root `pyproject.toml` (uv workspace)

**Files:**
- Create: `D:/mr/monorepo-staging/pyproject.toml`

- [ ] **Step 1: Write the workspace root**

```toml
[project]
name = "goldenmatch-monorepo"
version = "0.0.0"
description = "Golden Suite monorepo"
requires-python = ">=3.11"

[tool.uv.workspace]
members = ["packages/python/*"]
```

- [ ] **Step 2: Resolve workspace**

```bash
cd D:/mr/monorepo-staging
uv sync
```

Expected: `uv sync` succeeds; `uv.lock` is created. If a member's `pyproject.toml` declares incompatible Python or missing deps, fix per package and re-run.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add uv workspace root"
```

### Task 5.2: Root `package.json` (fake JS workspace)

**Files:**
- Create: `D:/mr/monorepo-staging/package.json`

- [ ] **Step 1: Write the root**

```json
{
  "name": "goldenmatch-monorepo",
  "private": true,
  "description": "Not a real npm workspace — each package installs independently to avoid Windows symlink issues.",
  "scripts": {
    "install:all": "for d in packages/typescript/*; do npm --prefix \"$d\" install; done",
    "test:all": "for d in packages/typescript/*; do npm --prefix \"$d\" test; done",
    "build:all": "for d in packages/typescript/*; do npm --prefix \"$d\" run build; done",
    "lint:all": "for d in packages/typescript/*; do npm --prefix \"$d\" run lint; done"
  }
}
```

- [ ] **Step 2: Verify install:all succeeds**

```bash
npm run install:all
```

Expected: each TS package installs without error.

- [ ] **Step 3: Commit**

```bash
git add package.json package-lock.json packages/typescript/*/package-lock.json 2>/dev/null
git commit -m "chore: add JS root package.json with per-package install scripts"
```

### Task 5.3a: Pre-flight — inspect extensions Cargo layout

- [ ] **Step 1: Determine extensions structure**

```bash
cd D:/mr/monorepo-staging
test -f packages/rust/extensions/Cargo.toml && grep -c '^\[workspace\]' packages/rust/extensions/Cargo.toml
ls packages/rust/extensions/
```

Record the result for Task 5.3:
- If grep returned `1`: extensions has its own nested workspace → in Task 5.3 list explicit member crate paths.
- If grep returned `0`: extensions is a single package → in Task 5.3 reference `packages/rust/extensions` as the only member.

### Task 5.3: Root `Cargo.toml` (cargo workspace)

**Files:**
- Create: `D:/mr/monorepo-staging/Cargo.toml`

- [ ] **Step 1: Write the root using the Task 5.3a result**

For a single-package extensions:

```toml
[workspace]
members = ["packages/rust/extensions"]
resolver = "2"
```

For a nested-workspace extensions, replace `members` with the explicit list of crate paths discovered in 5.3a (e.g., `["packages/rust/extensions/duckdb", "packages/rust/extensions/postgres", "packages/rust/extensions/bridge"]`) — cargo does not allow nested workspaces, so the existing nested `[workspace]` declaration in `packages/rust/extensions/Cargo.toml` must be removed in this same commit.

- [ ] **Step 3: Verify build**

```bash
cargo check --workspace
```

Expected: clean check (warnings okay; errors must be fixed before commit).

- [ ] **Step 4: Commit**

```bash
git add Cargo.toml Cargo.lock
git commit -m "chore: add cargo workspace root"
```

### Task 5.4: Root `justfile`

**Files:**
- Create: `D:/mr/monorepo-staging/justfile`

- [ ] **Step 1: Write**

```
set shell := ["bash", "-cu"]

default:
    @just --list

install:
    uv sync
    for d in packages/typescript/*; do npm --prefix "$d" install; done
    cargo fetch

test:
    uv run pytest
    for d in packages/typescript/*; do npm --prefix "$d" test; done
    cargo test --workspace

lint:
    uv run ruff check .
    for d in packages/typescript/*; do npm --prefix "$d" run lint; done
    cargo clippy --workspace -- -D warnings

build:
    uv build
    for d in packages/typescript/*; do npm --prefix "$d" run build; done
    cargo build --workspace --release
```

- [ ] **Step 2: Verify recipe list**

```bash
just --list
```

Expected: lists `default`, `install`, `test`, `lint`, `build`.

- [ ] **Step 3: Commit**

```bash
git add justfile
git commit -m "chore: add top-level justfile"
```

### Task 5.5: GitHub Actions CI

**Files:**
- Create: `D:/mr/monorepo-staging/.github/workflows/ci.yml`

- [ ] **Step 1: Write**

```yaml
name: ci

on:
  push:
    branches: [main]
  pull_request:

jobs:
  python:
    runs-on: ubuntu-latest
    if: contains(github.event.head_commit.modified, 'packages/python/') || github.event_name == 'pull_request'
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync
      - run: uv run pytest packages/python
      - run: uv run ruff check packages/python

  typescript:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        pkg: [goldenmatch, goldencheck, goldencheck-types, goldenflow, infermap]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 20
      - run: npm --prefix packages/typescript/${{ matrix.pkg }} install
      - run: npm --prefix packages/typescript/${{ matrix.pkg }} test --if-present
      - run: npm --prefix packages/typescript/${{ matrix.pkg }} run build --if-present

  rust:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
      - run: cargo test --workspace
      - run: cargo clippy --workspace -- -D warnings

  dbt:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install dbt-core dbt-postgres
      - run: cd packages/dbt/goldencheck && dbt parse --no-version-check

  action:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: |
          test -f packages/actions/goldencheck/action.yml && echo "action.yml present"
```

Note: the `if:` filter on the python job above is approximate — for fine-grained path filtering, prefer `paths:` triggers per workflow file once the layout settles. Keeping it permissive on PRs is acceptable for v1.

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add per-language matrix workflow"
```

---

## Phase 6 — Verification

### Task 6.1: Run `just install` and `just test` end-to-end

- [ ] **Step 1: Clean state**

```bash
cd D:/mr/monorepo-staging
git status
```

Expected: clean working tree.

- [ ] **Step 2: Install everything**

```bash
just install
```

Expected: uv sync, all `npm --prefix` installs, `cargo fetch` all succeed.

- [ ] **Step 3: Test everything**

```bash
just test 2>&1 | tee D:/mr/test-output.log
```

Expected: every language's test suite at least *executes*. Some pre-existing test failures (e.g., torch segfault per `feedback_polars_torch.md`) are acceptable as long as they reproduce the same failure mode they had in the source repo. Any *new* failure caused by the fold-in must be investigated before push.

- [ ] **Step 4: Spot-check history preservation**

```bash
git log --follow --oneline packages/python/infermap/pyproject.toml | head -10
git log --follow --oneline packages/typescript/goldenmatch/package.json | head -10
git log --follow --oneline packages/rust/extensions/Cargo.toml | head -10
```

Expected: each file shows a multi-commit history reaching back into the original repo's commits (not just the merge commit).

### Task 6.2: User gate — manual review

- [ ] **Step 1: Surface to user for sign-off before push**

Print a summary:

```bash
echo "Staging at: D:/mr/monorepo-staging"
echo "Commits: $(git log --oneline | wc -l)"
echo "Packages:"
ls -la packages/python packages/typescript packages/rust packages/dbt packages/actions
```

Wait for explicit user approval before Phase 7.

---

## Phase 7 — Push to remote

### Task 7.1: Force-push staging to `benzsevern/goldenmatch` `main`

- [ ] **Step 1: Add the real remote**

```bash
cd D:/mr/monorepo-staging
git remote add origin https://github.com/benzsevern/goldenmatch.git
git fetch origin
```

- [ ] **Step 2: Confirm safety tag is on the remote**

```bash
git ls-remote --tags origin | grep main-pre-monorepo
```

Expected: one line showing the tag SHA. **If absent, STOP** — re-run Task 0.1 step 2 before any force-push.

- [ ] **Step 3: Force-push main**

```bash
git push --force origin main
```

Expected: `+ <oldsha>...<newsha> main -> main (forced update)`.

### Task 7.2: Verify remote state

- [ ] **Step 1: Clone fresh and run smoke test**

```bash
cd D:/mr && rm -rf verify-monorepo
git clone https://github.com/benzsevern/goldenmatch.git verify-monorepo
cd verify-monorepo
just install
```

Expected: clean install. If it fails, the remote has an inconsistency vs staging — investigate before continuing.

---

## Phase 8 — Archive source repos

### Task 8.1: Add a "moved" notice and archive each source

For each of: `goldencheck`, `goldenflow`, `goldenpipe`, `infermap`, `goldencheck-types`, `goldenmatch-extensions`, `dbt-goldencheck`, `goldencheck-action`.

- [ ] **Step 1: Edit each repo's README**

Per repo:

```bash
cd D:/mr/sources/<repo>
git checkout main  # or feature/ai-coder-docs for goldenmatch-extensions

# Prepend the notice using a Python one-liner to avoid Git Bash CRLF/LF churn from shell redirection:
python -c "
import pathlib
p = pathlib.Path('README.md')
notice = '> **Moved.** This repo has moved into the [\`benzsevern/goldenmatch\`](https://github.com/benzsevern/goldenmatch) monorepo at \`packages/<lang>/<name>/\`. This repo is archived; new development happens in the monorepo.\n\n'
existing = p.read_bytes() if p.exists() else b''
p.write_bytes(notice.encode('utf-8') + existing)
"

git add README.md
git commit -m "docs: notice of move to goldenmatch monorepo"
git push origin HEAD
```

- [ ] **Step 2: Archive via `gh`**

```bash
gh repo archive benzsevern/<repo> --yes
```

- [ ] **Step 3: Verify archived state on GitHub**

```bash
gh repo view benzsevern/<repo> --json isArchived
```

Expected: `{"isArchived": true}`.

### Task 8.2: Final sanity check

- [ ] **Step 1: Confirm all 8 are archived**

```bash
for r in goldencheck goldenflow goldenpipe infermap \
         goldencheck-types goldenmatch-extensions \
         dbt-goldencheck goldencheck-action; do
  printf "%-30s " "$r"
  gh repo view "benzsevern/$r" --json isArchived --jq .isArchived
done
```

Expected: all `true`.

- [ ] **Step 2: Confirm `goldenmatch` is NOT archived and shows the new layout**

```bash
gh repo view benzsevern/goldenmatch --json isArchived --jq .isArchived
```

Expected: `false`.

---

## Rollback procedure

If catastrophic failure between Phase 7 step 3 (force-push) and Phase 8:

```bash
cd /tmp && git clone --branch main-pre-monorepo https://github.com/benzsevern/goldenmatch.git rollback
cd rollback
git push --force origin main-pre-monorepo:main
```

This restores `main` from the safety tag created in Task 0.1.

If failure occurs *after* archiving (Phase 8): `gh repo unarchive benzsevern/<repo>` to restore each archived repo to active.

---

## Out of scope (follow-ups)

- Release/publish automation (PyPI, npm, crates.io) — separate plan.
- Cross-package version sync (changesets / release-please) — separate plan.
- README/docs rewrite to reflect monorepo navigation — separate task.
- Deleting the now-redundant per-package `package.json` "fake workspace" notices inside each TS package — cosmetic, separate PR.
