# Network Maintenance

## Principles
- **Point, don't copy.** Code-level facts live in `CLAUDE.md`; designs live in
  `docs/superpowers/`. Nodes here link to those, not duplicate them. When they conflict,
  the source-of-truth wins and the node is wrong — fix the node.
- **Small, focused nodes.** One concern per file. If a node grows past ~1 screen, split it.
- **Cross-link liberally.** Every node should be reachable from
  [../discovery.md](../discovery.md) and link to its neighbors.
- **Classification footer** on every node: `domain/stability` + last-updated date.

## When to update
- After a workstream milestone (PR merged that changes status/decisions): update the
  relevant architecture/decision node + add an [updates.md](updates.md) entry.
- When a decision is made that has no code home (trade-offs, "why we didn't"): add a
  numbered record under `../decisions/`.
- When the foundation changes (new package, structural move): update `../foundation/`.

## What does NOT belong here
- Secrets, tokens (those are gitignored elsewhere).
- Transient task state or one-session scratch notes (use TodoWrite / user memory).
- Duplicates of CLAUDE.md operational detail.

## Committing
The network is meant to be committed (shared brain), but commit only when the user asks.
It is not currently in `.gitignore`; confirm before `git add`.

---
**Classification:** meta/process • **Last updated:** 2026-06-03
