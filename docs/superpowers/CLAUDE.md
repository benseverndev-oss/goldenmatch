# superpowers workflow notes

## Path is gitignored

`docs/superpowers/specs/` and `docs/superpowers/plans/` are matched by `.gitignore`. Use `git add -f <file>` to commit them.

## Spec → plan → execute, in order

- **Spec** (`brainstorming` skill): clarifying questions one at a time, propose 2-3 options each, present design, get approval, write to `specs/YYYY-MM-DD-<topic>-design.md`, run `spec-document-reviewer`, fold in corrections, get user approval.
- **Plan** (`writing-plans` skill): TDD-shaped tasks (failing test → minimal impl → commit), exact file paths, exact commands. Reviewer pass.
- **Execute**: `subagent-driven-development` for parallel work; for sequential ops, just power through phase by phase, committing each.

## Don't skip the brainstorming gate

The HARD-GATE in `superpowers:brainstorming` blocks implementation skills until a design is written and approved. This is by design. Even "trivial" projects go through it — they just have shorter designs.

## Existing artifacts

- `2026-05-01-goldenmatch-monorepo-fold-in-design.md` + plan — how the monorepo was assembled.
- `2026-05-01-infermap-goldencheck-handoff-design.md` + plan — InferMap stage 0 + shared type registry.
