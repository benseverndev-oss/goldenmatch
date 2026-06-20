# superpowers workflow notes

## Path is tracked (repo-root), gitignored (per-package)

The **repo-root** `docs/superpowers/specs/` and `docs/superpowers/plans/` are git-TRACKED — commit specs and plans here with a plain `git add` (no `-f`; the root `.gitignore` has no rule for them; `git check-ignore` is negative; 80+ specs/plans are committed). Only the per-package `packages/python/<pkg>/docs/superpowers/` dirs are gitignored (each package's own `.gitignore`) and stay local-only scratch.

## Spec → plan → execute, in order

- **Spec** (`brainstorming` skill): clarifying questions one at a time, propose 2-3 options each, present design, get approval, write to `specs/YYYY-MM-DD-<topic>-design.md`, run `spec-document-reviewer`, fold in corrections, get user approval.
- **Plan** (`writing-plans` skill): TDD-shaped tasks (failing test → minimal impl → commit), exact file paths, exact commands. Reviewer pass.
- **Execute**: `subagent-driven-development` for parallel work; for sequential ops, just power through phase by phase, committing each.

## Don't skip the brainstorming gate

The HARD-GATE in `superpowers:brainstorming` blocks implementation skills until a design is written and approved. This is by design. Even "trivial" projects go through it — they just have shorter designs.

## Existing artifacts

- `2026-05-01-goldenmatch-monorepo-fold-in-design.md` + plan — how the monorepo was assembled.
- `2026-05-01-infermap-goldencheck-handoff-design.md` + plan — InferMap stage 0 + shared type registry.
