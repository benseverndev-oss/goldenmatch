# 0058 — One command to regenerate the derived-docs battery + a single drift gate

**Status:** accepted (2026-08-13, Ben) • **Shipped:** `scripts/regen_docs.py`, `make docs`, a `regen-docs` pre-commit hook, and a `docs_regen` CI gate (ci-required) • **Classification:** decision/accepted

## Context
The repo derives several docs from code and **commits** the output —
`config-matrix.mdx`, `agent-manifest.json`, `agent-codemap.json`,
`api-surface.mdx`, `suite-matrix.mdx`, `thesis-weaknesses.mdx`, `native.mdx` —
plus a few **hand-authored count figures** (`llms.txt` / `README` / `api-surface`
inline). Each has its own `--check` gate. Because the derivation output is
committed, drift is *possible* (the committed copy lags the code) and only
*caught*, not *prevented*.

Adding one MCP tool + CLI subgroup this session reddened the `config_matrix` gate
**three times in a row** — each failure a *different* stale artifact I regenerated
one at a time. The generators exist; the missing piece was a single command that
runs *all* of them, so nobody has to remember the list.

(The fully drift-*proof* alternative — stop committing the derived output, generate
it at docs-build / wheel-build, template every prose figure — was considered and
deferred: it trades PR-visible surface diffs and shipped "look-it-up" stores for a
load-bearing build step. This ADR keeps commit-and-gate and removes the *human
memory* failure instead.)

## Decision
1. **`scripts/regen_docs.py` — the single entry point.** Runs every writable doc
   generator (`gen_config_matrix --write` + `--manifest`, `agent_codemap`,
   `gen_api_surface`, `gen_suite_matrix`, `gen_thesis_weaknesses`,
   `gen_native_docs`) and then the two **non-regenerable** prose-count checks
   (`check_llms_counts`, `gen_api_surface --check` inline figures) — because no
   `--write` rewrites those, so it *reports* them for a manual bump rather than
   passing silently. `make docs` wraps it.
2. **`--check` = the CI gate.** Regenerate in place, then `git diff --quiet`
   (tracked files only; untracked scratch doesn't trip it) fails and prints the
   exact diff to commit if a committed doc drifted, plus runs the prose checks.
   Wired as the `docs_regen` job **in `ci-required`**, broadly path-filtered
   (`packages/python/**`, `parity/**`, the generators, the doc targets) — the
   regen is idempotent, so an over-trigger is a cheap no-op and an under-trigger
   can't leak a stale doc.
3. **A `regen-docs` pre-commit hook** runs it in write mode, scoped with `files:`
   to doc-affecting source so ordinary commits stay fast (the config's stated
   principle); a surface change regenerates on commit and, if anything changed,
   pre-commit fails so you `git add` + recommit.

## Consequences / honest flags
- **Additive, not a consolidation (yet).** The per-artifact `--check`s in the
  `config_matrix` job stay — they also assert non-drift invariants (determinism,
  markers, cross-refs, thesis conformance). `docs_regen` is now the authoritative
  drift gate; slimming `config_matrix` down to its non-drift tests is a low-risk
  follow-up, deliberately not bundled here.
- **`--check` assumes a committed tree** (as CI has). Run locally *after*
  committing, or use write mode (`make docs`) which just regenerates.
- **The pre-commit hook needs the workspace importable** (activate the dev venv /
  `uv sync`); it is `language: system` like the existing `no-ci-skip-directive`
  hook.
- **Prose figures are still hand-edited.** Truly eliminating them (template
  placeholders filled at render) is part of the deferred drift-proof design, not
  this change.

---
**Classification:** decision/accepted • **Last updated:** 2026-08-13
