# Golden Suite monorepo — agent guide

Fast navigational map for any coding agent. This is the **entry point**; the
deep operational lore (CI internals, incident post-mortems, release/publish
mechanics, native-kernel gotchas) lives in `CLAUDE.md` and the per-package
files listed below. Read those before doing package-specific work.

## What this is

A polyglot monorepo for **entity resolution and data-quality tooling**. Six
core products, each shipped across multiple language surfaces (Python, a
TypeScript port, and pyo3-free Rust `-core` kernels with optional compiled
accelerators). North Star: be the tool a developer reaches for *by default* for
entity resolution — defaults are re-earned every release
(`context-network/foundation/project-definition.md`).

## Layout

```
packages/
  python/        uv workspace — members: packages/python/*
                 (excluded standalone: goldenmatch-kg, goldengraph, golden-suite)
  typescript/    pnpm + Turborepo workspace (TS ports + wasm runtime)
  rust/extensions/  cargo workspace — pyo3-free *-core kernels + *-native accelerators
  actions/       reusable GitHub Actions
  dbt/           dbt models
docs/            design docs, ADRs, specs (docs/superpowers/{specs,plans})
docs-site/       the published docs site (Mintlify)
context-network/ project foundation, decisions, discovery — read for "why"
scripts/         benches, gates, release tooling
```

Products: **goldenmatch** (ER core), **goldencheck** (data-quality scan),
**goldenflow** (transforms), **goldenpipe** (pipeline compiler), **infermap**
(schema/column mapping), **goldenanalysis** (cluster/quality analysis). Plus
**goldengraph** (KG engine, standalone) and **golden-suite** (meta-package).

## Build & test

Canonical recipes are in the `justfile`:

| Task    | Command      | Under the hood                                                    |
| ------- | ------------ | ---------------------------------------------------------------- |
| install | `just install` | `uv sync`; `npm install` per TS pkg; `cargo fetch`             |
| test    | `just test`    | `uv run pytest packages/python`; `npm test` per TS pkg; `cargo test --workspace` |
| lint    | `just lint`    | `ruff check`; TS lint; `cargo clippy --workspace -- -D warnings` |
| build   | `just build`   | `uv build`; TS build; `cargo build --workspace --release`       |

Scope work to one package where possible: `uv run pytest packages/python/<pkg>`.

## Landmines to know before you touch anything

- **Do NOT run the full pytest suite locally** — xdist OOMs a dev box. Run
  per-package, or let CI run the full matrix. (`feedback_avoid_full_suite_oom`.)
- **GitHub auth:** `benseverndev-oss/*` and `benzsevern/*` repos use the personal
  `benzsevern` account, not the work account. `gh auth switch --user benzsevern`
  before any push. See `CLAUDE.md` › "GitHub auth".
- **Rust is the reference implementation; pure-Python is the lossy fallback.**
  `GOLDENMATCH_NATIVE=auto` runs native wherever a kernel symbol exists. Set
  `=0` to force pure-Python (and `POLARS_SKIP_CPU_CHECK=1` on Windows). See
  `CLAUDE.md` › "goldenmatch-native".
- **Branch + squash-merge via PR;** `main` is a native FIFO merge queue —
  `gh pr merge <N> --auto --squash` then stop. Never commit direct to `main`.
- **Only `.github/workflows/` at the repo root runs.** Workflow files left under
  `packages/*/.github/` are orphaned and silently ignored.

## Look it up instead of grepping: the generated maps

Two committed, CI-gated JSON maps let you answer most "where / what" questions
without searching the tree — `docs/agent-manifest.json` (config & capability
surface) and `docs/agent-codemap.json` (source structure).

`docs/agent-manifest.json` is a generated, machine-readable index of every
package's **config schema, CLI commands, MCP tools, enumerated vocabularies
(with `best_for` decision hints), `<PREFIX>_*` env knobs, source-file locations
(where the config schema / CLI / MCP server live + pyproject entry points), and
the repo's Rust crate map** — for all six packages. Query it to answer "what
scorers exist and which suits names", "what MCP tools does goldenpipe expose",
"the type/default of `GoldenMatchConfig.threshold`", "which env vars tune
goldenmatch", "where does goldenpipe's MCP server live", or "which crate is
`goldenmatch-fs-core`" — without searching the tree.

It is generated from the same registry as the CI-gated config-matrix docs
(`scripts/config_matrix/`) and gated for drift by `scripts/test_config_matrix.py`,
so it can't silently fall out of sync with the code. Never hand-edit it;
regenerate with `python scripts/gen_config_matrix.py --manifest`.

**Via MCP:** the `goldensuite-mcp` server exposes a `suite_manifest` tool that
serves slices of this file (overview / one package / one section / keyword
search) so you don't pull the whole 300 KB for one lookup.

`docs/agent-codemap.json` is the structural companion: a static-AST map of the
six packages' Python source. Per module it lists the file, a one-line purpose
(from the module docstring), the top-level classes/functions it defines, and its
intra-repo imports — so you can answer "which module defines `score_buckets`",
"what does `goldenmatch.backends.score_buckets` connect to", or "what modules
make up goldencheck's engine" without grepping. Gated by
`scripts/test_agent_codemap.py`; regenerate with `python scripts/agent_codemap.py --write`.

## Where the deep context lives

- `CLAUDE.md` (repo root) — CI structure, path filters, merge queue, release &
  publish mechanics, native-kernel incident lore (#688 etc.). The source of
  truth for operational gotchas.
- `packages/python/<pkg>/CLAUDE.md` and `.../AGENTS.md` — package-specific test
  tuning, `--ignore` lists, and quirks.
- `context-network/` — decisions (ADRs), foundation, and the "why" behind the
  architecture. Search here before assuming something is undesigned.
- `_archive/goldenmatch-pre-fold/` — the standalone repo's history; old specs
  are sometimes the foundation for current work.
