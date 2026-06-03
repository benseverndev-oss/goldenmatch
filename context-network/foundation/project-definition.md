# Project Definition

## What it is
**Golden Suite** is a polyglot monorepo of data-quality / entity-resolution tooling.
**goldenmatch** is the flagship: an entity-resolution (ER / dedupe / record-linkage)
toolkit — zero-config auto-configuration, a fuzzy + probabilistic matching pipeline,
clustering, golden-record survivorship, and a durable identity graph. Sibling packages:
goldencheck (quality), goldenflow (transforms), goldenpipe (pipeline framework),
infermap (type inference).

- **GitHub:** `benseverndev-oss/goldenmatch` (auth as personal `benzsevern`, not work).
- **Distribution:** PyPI `goldenmatch`, npm `goldenmatch` (TS port), DuckDB/Postgres extensions.
- The pipeline: ingest → column_map → auto_fix → validate → standardize → matchkeys →
  block → score → cluster → golden → output.

## The governing arc (current)
**Arrow-native → engine portability.** The destination is making every pipeline stage a
frames-in/frames-out relational op a query engine can plan, spill, and distribute
(DataFusion single-box out-of-core; Sail distributed). One-box peak RSS was retired as
the gate — see [../decisions/0001-gate-reframe-engine-portability.md](../decisions/0001-gate-reframe-engine-portability.md).

The active concrete work is the **DataFusion spine** —
[../architecture/datafusion-spine.md](../architecture/datafusion-spine.md).

## Authoritative detail lives elsewhere
This node is deliberately thin. Code-level behavior, perf numbers, and gotchas are in
the `CLAUDE.md` files (see [structure.md](structure.md)); designs are in
`docs/superpowers/specs/`.

---
**Classification:** foundation/stable • **Last updated:** 2026-06-03
