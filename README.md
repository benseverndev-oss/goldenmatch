# Golden Suite Monorepo

Polyglot monorepo hosting the Golden Suite. Packages live under `packages/<lang>/<name>/`.

## Layout

- `packages/python/{goldenmatch,goldencheck,goldenflow,goldenpipe,infermap}/` — Python packages, managed via uv workspace at the root.
- `packages/typescript/{goldenmatch,goldencheck,goldencheck-types,goldenflow,infermap}/` — TypeScript packages, each installed independently (no real npm workspace, to avoid Windows symlink issues).
- `packages/rust/extensions/` — Rust workspace for DuckDB and Postgres extensions. Self-contained; cargo commands run from inside this directory.
- `packages/dbt/goldencheck/` — dbt package.
- `packages/actions/goldencheck/` — GitHub Action.

## Quickstart

```bash
just install   # uv sync + per-package npm install + cargo fetch
just test      # all languages
just lint
just build
```

## Why no root Cargo workspace?

`packages/rust/extensions/` is itself a Cargo workspace (with `postgres` excluded for pgrx-specific build requirements). Cargo does not allow nested workspaces sharing members. The root therefore does not declare a Cargo workspace; cargo commands run inside `packages/rust/extensions/`.

## History

This repository was formed on 2026-05-01 by folding 8 sibling repos into the existing `goldenmatch` repo using `git filter-repo`. Full commit history is preserved for every source. See `docs/superpowers/specs/2026-05-01-goldenmatch-monorepo-fold-in-design.md` for details.
