# Design: Fold 8 repos into `goldenmatch` monorepo

**Date:** 2026-05-01
**Author:** Ben Severn (with Claude)
**Status:** Draft

## Context

Nine separate GitHub repos under `benzsevern/` currently make up the Golden Suite (the existing `goldenmatch` repo plus eight to fold in):

| Repo | Stack | Notes |
|---|---|---|
| `goldenmatch` | Python + TypeScript parity | TS port lives at `packages/goldenmatch-js/` |
| `goldencheck` | Python + TypeScript parity | TS port at `packages/goldencheck-js/` |
| `goldenflow` | Python + TypeScript parity | TS port at `packages/goldenflow-js/` |
| `infermap` | Python + TypeScript parity | TS port at `packages/infermap-js/` |
| `goldenpipe` | Python only | |
| `goldencheck-types` | TypeScript | Standalone published types |
| `goldenmatch-extensions` | Rust | Currently on branch `feature/ai-coder-docs` |
| `dbt-goldencheck` | dbt package | |
| `goldencheck-action` | GitHub Action (YAML) | |

This spec consolidates all of them into a single monorepo rooted at `goldenmatch`, with full git history preserved for every source repo.

## Goals

- Single repository, single source of truth for the Golden Suite.
- Full commit history preserved for all 8 source repos (no snapshot collapses).
- Language-partitioned layout: tooling roots (uv, Cargo) live at the monorepo root and operate on their language's packages without walking through unrelated trees.
- Polyglot-friendly task runner so a contributor can run `just test` without learning every per-language toolchain.
- PyPI / npm published package names remain unchanged.

## Non-goals

- Release automation (publishing, version bumping, changelogs across packages) — follow-up work.
- Test consolidation, cross-package refactoring, or API unification — out of scope.
- Migration of issues/PRs from old repos — they stay on their archived origins.

## Final layout

```
goldenmatch/
├── packages/
│   ├── python/
│   │   ├── goldenmatch/              ← from goldenmatch (py paths only)
│   │   ├── goldencheck/              ← from goldencheck (py paths only)
│   │   ├── goldenflow/               ← from goldenflow (py paths only)
│   │   ├── goldenpipe/               ← from goldenpipe (whole repo)
│   │   └── infermap/                 ← from infermap (py paths only)
│   ├── typescript/
│   │   ├── goldenmatch/              ← from goldenmatch/packages/goldenmatch-js
│   │   ├── goldencheck/              ← from goldencheck/packages/goldencheck-js
│   │   ├── goldencheck-types/        ← from goldencheck-types (whole repo)
│   │   ├── goldenflow/               ← from goldenflow/packages/goldenflow-js
│   │   └── infermap/                 ← from infermap/packages/infermap-js
│   ├── rust/
│   │   └── extensions/               ← from goldenmatch-extensions, branch feature/ai-coder-docs
│   ├── dbt/
│   │   └── goldencheck/              ← from dbt-goldencheck (whole repo)
│   └── actions/
│       └── goldencheck/              ← from goldencheck-action (whole repo)
├── pyproject.toml                    (uv workspace root)
├── uv.lock
├── package.json                      ("fake workspace" with npm --prefix scripts)
├── Cargo.toml                        (cargo workspace root)
├── justfile
├── .github/workflows/ci.yml
├── .gitignore
└── README.md                         (rewritten as monorepo root)
```

## History-preserving fold-in procedure

The fold is performed in a fresh clone of `goldenmatch` (the new monorepo working copy). Each source repo is rewritten with `git filter-repo` to re-root its paths under the target prefix, then merged into the monorepo with `git merge --allow-unrelated-histories`. Order is deterministic so the result is reproducible.

### 1. Prep

1. `git clone https://github.com/benzsevern/goldenmatch.git monorepo-staging`
2. In the staging clone, perform an initial filter-repo pass on **the existing goldenmatch history itself**:
   - Re-root python paths to `packages/python/goldenmatch/`.
   - Re-root `packages/goldenmatch-js/**` to `packages/typescript/goldenmatch/`.
   - Strip `dist/`, `target/`, `node_modules/`, `*.egg-info/`, and the ~200 root-level `*_lineage.json` / `*_clusters.csv` artifacts from **all history** (not just current tree).
3. Result: existing goldenmatch history is now correctly placed; from this point forward it is the trunk we merge other repos into.

### 2. For each parity repo (`goldencheck`, `goldenflow`, `infermap`)

Two filter-repo passes per repo, each producing an independent rewritten clone:

- **Python pass:** keep python-side paths, drop `packages/<name>-js/`, re-root to `packages/python/<name>/`. Strip generated artifacts as in step 1.
- **TypeScript pass:** keep only `packages/<name>-js/**`, re-root to `packages/typescript/<name>/`.

Each rewritten clone is then merged into the staging monorepo:

```
git remote add <name>-py ../<name>-py-rewritten
git fetch <name>-py
git merge --allow-unrelated-histories <name>-py/main
# repeat for <name>-ts
```

### 3. For each single-language repo

One filter-repo pass each, re-rooting the entire tree to its target prefix:

| Repo | Source branch | Target prefix |
|---|---|---|
| `goldenpipe` | `main` | `packages/python/goldenpipe/` |
| `goldencheck-types` | `main` | `packages/typescript/goldencheck-types/` |
| `goldenmatch-extensions` | `feature/ai-coder-docs` | `packages/rust/extensions/` |
| `dbt-goldencheck` | `main` | `packages/dbt/goldencheck/` |
| `goldencheck-action` | `main` | `packages/actions/goldencheck/` |

Then merge each into staging via `--allow-unrelated-histories`.

### 4. Finalize

1. Add monorepo-level files (`pyproject.toml` workspace root, root `package.json`, `Cargo.toml`, `justfile`, `.gitignore`, root `README.md`).
2. Verify each package builds in isolation: `uv sync && uv run pytest`, `npm install && npm test` per TS package, `cargo build`, `cargo test`.
3. Tag the current remote `main` as `main-pre-monorepo` and **push that tag to the remote first** (so the pre-monorepo state survives any local disk loss). Then force-push staging to `benzsevern/goldenmatch` `main`, gated on user verification.

## Workspace tooling

### Python — uv workspace

Root `pyproject.toml`:
```toml
[tool.uv.workspace]
members = ["packages/python/*"]
```
Each package keeps its own `pyproject.toml` defining its package metadata and dependencies. Cross-package dev dependencies use `uv add --workspace`. A single `uv.lock` lives at the root.

### TypeScript — "fake workspace" (matches existing pattern)

Root `package.json` declares no `workspaces` field. Instead it provides scripts that delegate via `npm --prefix`:

```json
{
  "scripts": {
    "install:all": "for d in packages/typescript/*; do npm --prefix \"$d\" install; done",
    "test:all": "for d in packages/typescript/*; do npm --prefix \"$d\" test; done"
  }
}
```

This matches the explicit choice already made in each parity repo's `package.json`: *"Not a real npm workspace — each package installs independently to avoid Windows symlink issues."* No new symlink-related Windows pain introduced by this fold-in.

### Rust — cargo workspace

Root `Cargo.toml`:
```toml
[workspace]
members = ["packages/rust/extensions"]
resolver = "2"
```
Existing nested `Cargo.toml` files inside `goldenmatch-extensions` are preserved as workspace members.

### dbt + GitHub Action

Standalone, no workspace plumbing.

## `just` task runner

Top-level `justfile` provides language-agnostic commands that delegate to per-language tooling. The shebang pins `bash` so recipes work consistently on the Windows-primary dev environment (Git Bash) and Linux/macOS:

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

## CI

One workflow file at `.github/workflows/ci.yml` with a job-per-language design and `paths:` filters so unrelated PRs don't trigger every job:

- `python` job: triggers on `packages/python/**`, `pyproject.toml`, `uv.lock`. Runs `uv sync`, `uv run pytest`, `uv run ruff`.
- `typescript` job: triggers on `packages/typescript/**`. Runs `npm install` + `npm test` per package.
- `rust` job: triggers on `packages/rust/**`, `Cargo.toml`, `Cargo.lock`. Runs `cargo test --workspace`, `cargo clippy`.
- `dbt` job: triggers on `packages/dbt/**`. Runs `dbt parse` against the included integration tests.
- `action` job: triggers on `packages/actions/**`. Validates `action.yml` syntax.

No release / publish automation in this fold-in — that is follow-up work after the monorepo lands.

## Old GitHub repos

After staging is force-pushed and verified:

1. Archive all 8 source repos on GitHub (read-only, history preserved publicly).
2. Edit each archived repo's README to add a header: *"Moved to [`benzsevern/goldenmatch`](https://github.com/benzsevern/goldenmatch) monorepo at `packages/<lang>/<name>/`"*.
3. PyPI / npm publish targets unchanged — packages continue to publish under their original names (`goldenmatch`, `goldencheck`, `goldenflow`, `goldenpipe`, `infermap`, `@golden/goldencheck-types`, etc.) from their new monorepo locations.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Force-push to `goldenmatch/main` is destructive | Tag old main as `main-pre-monorepo` before push; staging is verified locally first; user gates the push. |
| `git filter-repo` rewrites SHAs — old refs / external links break | Source repos remain accessible (archived) with original SHAs intact. New monorepo SHAs are new. Acceptable. |
| Generated artifacts (lineage/clusters JSON) bloating monorepo history | filter-repo strips them from **all** history during the fold, not just the current tree. |
| Windows symlink/path-length issues during filter-repo | Run all rewrites in a short-path scratch directory (e.g., `D:/mr/`); avoid placing staging deep in `D:/show_case/`. |
| `goldenmatch-extensions` is on a feature branch, not `main` | Folded from `feature/ai-coder-docs` as-is per user decision; no merge of any `main` state from that repo. |
| Cross-package imports break after fold (e.g., goldenflow → goldenmatch) | uv workspace re-resolves intra-workspace deps from path. Verified in finalize step before push. |

## Open questions

None — all decisions locked during brainstorming:
- Q1: Preserve full history (option A).
- Q2: Language-first layout under `packages/<lang>/`.
- Q3: Four parity products: goldenmatch, goldencheck, goldenflow, infermap.
- Q4: filter-repo + dual-merge for parity repos (option A).
- Q5: uv workspace for Python.
- Q6: "Fake workspace" for JS (existing pattern, sidesteps Windows symlink pain).
- Q7: Archive old repos.
- Q8: Include `just` + minimal per-language CI matrix.
- Extensions branch: fold from `feature/ai-coder-docs`.
- History scrub: strip generated artifacts from all history.
- Package names: unchanged on PyPI / npm.
