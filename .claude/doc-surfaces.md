# Doc surfaces — Golden Suite monorepo

Machine-and-human checklist of every documentation surface that must be swept
when a feature/rollout lands. Consumed by the `rollout-docs-sweep` skill (it looks
for this file before falling back to generic discovery). Keep it current when a
new surface is added.

Repo: `benseverndev-oss/goldenmatch` (polyglot monorepo). "Sweep" = for each
surface, check whether the rollout added/renamed/removed anything the surface
documents, and update it. A removed flag/symbol/endpoint is the highest-signal
thing to grep for.

## 1. Mintlify docs site (`docs-site/`)

- **`docs-site/docs.json`** — navigation. A NEW page is invisible until added to a
  group's `pages` array. Validate it still parses (`json.load`) after editing.
- **`docs-site/<package>/*.mdx`** — the per-package pages. Highest-churn:
  - `goldenmatch/tuning.mdx` — the canonical `GOLDENMATCH_*` env-var reference.
    Any added/removed/renamed flag MUST be reflected here. (Keep the separate
    `GOLDENMATCH_SAIL_*` flags distinct from the non-Sail ones.)
  - `goldenmatch/configuration.mdx` — config YAML fields.
  - `goldenmatch/identity-graph.mdx` — identity scheme/behavior.
  - `goldenmatch/migrating-to-v2.mdx`, `v1-to-v2.mdx`, `v1-vs-v2.mdx` — upgrade docs.
  - feature pages under each package group (auto-config, scoring, blocking, ...).
- The site auto-serves **`/llms.txt`** and **`/llms-full.txt`** at `docs.bensevern.dev`
  (Mintlify-generated). The repo-root `llms.txt` family is the GitHub/raw supplement.

## 2. READMEs

- **`README.md`** (root) and **`packages/python/goldenmatch/README.md`** — the
  homepage "what's new" callout block is **single-sourced from CHANGELOG markers**.
  Do NOT hand-edit the block between `<!-- README-callouts:start -->` and `:end -->`.
  Instead add a `<!-- README-callout ... -->` block under the version heading in
  `packages/python/goldenmatch/CHANGELOG.md`, then run:
  `python scripts/sync_readme_callouts.py` (regenerates both; `--check` is a CI gate).
- **`packages/python/<pkg>/README.md`** — each package's PyPI long description.
- **`packages/typescript/<pkg>/README.md`**, `packages/actions/<pkg>/README.md`.

## 3. CHANGELOGs

- **`packages/python/<pkg>/CHANGELOG.md`** (Keep-a-Changelog). The release section
  is the source of truth; the homepage callout + version-consistency gate read it.
- The `version_consistency` CI gate requires `pyproject.toml` == `<pkg>/__init__.py`
  == `server.json` (`.version` + `packages[].version`) in lockstep. Bump all three.

## 4. Context network (`context-network/`)

- **`context-network/decisions/NNNN-*.md`** — add an ADR for any load-bearing
  architectural decision (numbered sequentially; current max wins). House style:
  `# NNNN — Title`, a `**Status:** ... • **Shipped:** ...` line, then
  `## Context` / `## Decision` / `## Consequence`.
- **`context-network/discovery.md`** — the nav hub; add a one-line link to any new
  decision/architecture node.
- **`context-network/meta/updates.md`** — newest-first log; add a dated entry.
- **`context-network/architecture/*.md`**, **`planning/roadmap.md`** — update if the
  rollout changes an active technical surface or the roadmap.
- (There is ALSO an older `docs/adr/` set, 0000+. `context-network/decisions/` is the
  primary; only touch `docs/adr/` if you are extending that specific series.)

## 5. Examples (`examples/` and `packages/python/<pkg>/examples/`)

- Runnable scripts + `examples/{python,sql,typescript,airflow}/`. If a rollout
  changed a public API signature, an env var, or a CLI command an example uses,
  update the example AND its README. Removed flags/symbols are the thing to grep.

## 6. Agent / discovery surfaces

- **`llms.txt`** family (root + per-package + suite root) — keep capability/feature
  counts and links honest (MCP tool counts, A2A skill counts, perf claims).
- **`server.json`** per package (MCP registry manifest) — version + capabilities.
- Agent card `_SKILLS` / MCP tool registrations if tools were added/removed.

## 7. Top-level

- **Root `CLAUDE.md` + per-package `CLAUDE.md`** — durable dev gotchas (not feature
  docs, but the place a rollout's hard-won lesson belongs).
- **GitHub About / topics**, **`CITATION.cff`** — only on a notable capability change.

## Sweep mechanics for this repo

- Grep the whole repo (minus `_archive/`) for every symbol/flag/endpoint the rollout
  REMOVED or RENAMED — that single grep finds most stale docs at once.
- `scripts/sync_readme_callouts.py --check`, `version_consistency`, and `readme_callouts`
  are CI gates; run their local equivalents before pushing.
- Docs-only PRs run a fast CI subset (path-filtered). Auth dance: push as `benzsevern`,
  switch back to `benzsevern-mjh` (see root `CLAUDE.md`).
