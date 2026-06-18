# Development Workflow

## The discipline (used for the spine work, paid off every stage)
**spec → spec-review → plan → plan-review → execute → code-review → CI → merge.**
- Specs land in `docs/superpowers/specs/`, plans in `docs/superpowers/plans/`.
- Reviewers (plan-document-reviewer, code-reviewer) catch real blockers — e.g. a stale
  branch premise, a wrong test invariant. Use them; don't skip.
- Execute via subagent-driven-development where parallelizable; otherwise power through
  task-by-task, committing each.

## Hard environment constraints (non-negotiable here)
- **The dev box HANGS on `import goldenmatch` / `polars` / `datafusion`**, and large
  benches OOM it. **Do NOT run pytest or the bench locally.** Validate Python with
  `ruff check` + `python -m py_compile` ONLY. **CI is the only test verifier.**
- `ruff check packages/python/goldenmatch` must exit 0 before EVERY commit (I001 import
  order). Never pipe through `tail` (masks exit code).
- pyright slice (`pyrightconfig.json`) covers only core/ + config/ + _api.py + utils —
  NOT backends/, scripts/, or tests/. Diagnostics there don't gate CI.
- Zombie python processes accumulate from import/uv attempts and starve the box; kill via
  `Get-Process python | Stop-Process -Force` (PowerShell).

## GitHub / CI
- Auth dance: `GH_TOKEN=$(gh auth token --user benzsevern)` for push/PR/merge/`gh run`.
  NEVER `benzsevern-mjh`. Switch back after.
- Branch off `origin/main` (local `main` goes stale fast). PRs: squash-merge.
- Merge cosmetic failures to ignore: `cannot delete branch ... used by worktree` and the
  502 "already merged" — the remote merge lands; only local cleanup failed.
- `UNSTABLE` mergeStateStatus is usually a `continue-on-error` lane or pending non-gating
  check (`claude-review`, CodeQL); `ci-required` is the gate.
- **`gh workflow run <file> --ref <branch>` CANNOT dispatch a workflow that isn't on the
  default branch yet** — GitHub only registers `workflow_dispatch` from the default
  branch. Merge the workflow first, then dispatch from `main`.
- Benches are `workflow_dispatch` on `large-new-64GB` (16c/64GB) — billable; smoke-green
  the harness in CI before dispatching the heavy run.
- **Cut a goldenmatch release by pushing a bare `v*` tag** (`git push origin vX.Y.Z`) —
  bump version in `pyproject.toml` + `__init__.py` + `server.json` and finalize the
  CHANGELOG on `main` first. `publish-goldenmatch.yml` owns the GitHub Release (build →
  PyPI → sign+attest → draft release with assets → publish); do NOT `gh release create`
  by hand (immutable releases reject the post-publish asset attach). See
  [../decisions/0019-immutable-releases-publish-flow.md](../decisions/0019-immutable-releases-publish-flow.md).

## Memory & this network
- Cross-session agent facts → user-level memory. Committed shared knowledge → this network.
- After a workstream milestone, update [../meta/updates.md](../meta/updates.md) and the
  relevant architecture/decision node.

---
**Classification:** process/stable • **Last updated:** 2026-06-18
