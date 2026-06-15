# Project Definition

## North Star
**Make GoldenMatch the tool any developer reaches for *by default* for entity
resolution — to the point where reaching for anything else is the harder choice.**

This is a pull, not a victory lap. Defaults are re-earned every release against a
moving field, so it is never "done." Its job is to **color every decision**: when two
changes compete, the one that advances the North Star wins. Past wins (beating
hand-tuned Splink, MCP/A2A surfaces, the 100M run) are not achievements to list —
they are *evidence the vector is real*. The question is always: what is still the
harder choice for our users, and does this change fix it?

Five commitments turn the North Star into a test every change must pass:

| Commitment (forward) | The decision test every change must pass |
|---|---|
| **Zero-config should embarrass the alternatives.** First run needs nothing; the no-tuning path keeps getting *more* correct (first run zero, next run reuse). | Does this raise the floor for the user who configures nothing — or does it only help experts? |
| **Correctness must be scale-invariant.** The same input gives the same answer from a laptop CSV to 100M+ rows. | Does this preserve answer-parity across scales — or did we buy speed with accuracy? |
| **Every capability must reach every surface.** Power shows up in CLI, library, SQL, MCP, and A2A — not stranded on one. | Is this reachable from where the user actually works — or did we strand it on one surface? |
| **Out-of-the-box should approach the hand-tuned expert.** The gap to a specialist's best manual result keeps shrinking. | Does this close the gap to expert-tuned — or widen it? |
| **Advanced, never black-box.** As the engine gets cleverer, every decision stays traceable and auditable. | Can the user see **why** — even for the newest, smartest path? |

The honest gap-assessment against these five commitments, and the sequenced plan to close
it, is [../planning/north-star-roadmap.md](../planning/north-star-roadmap.md).

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
**Arrow-native → engine portability — in service of the *scale-invariant correctness*
commitment.** The engine work is a *means*, not the North Star: making every pipeline
stage a frames-in/frames-out relational op a query engine can plan, spill, and
distribute (DataFusion single-box out-of-core; Sail distributed) is how we keep the
same answer correct from a laptop CSV to 100M+ rows. One-box peak RSS was retired as
the gate — see [../decisions/0001-gate-reframe-engine-portability.md](../decisions/0001-gate-reframe-engine-portability.md).

Status (as of 2026-06-15): the **100M scale outcome is achieved today via the Ray
distributed-WCC path** (validated 9.2 min e2e, default-on within the distributed path,
#867) — that is what the README's "100M on a Ray cluster" headline means. The
**engine-portability *destination* is not complete**: the **DataFusion spine**
([../architecture/datafusion-spine.md](../architecture/datafusion-spine.md)) is merged
Stages A–E but stays opt-in behind `mode="scale"` — Stage E came back HONEST-NULL
because the Union-Find clustering break collects pairs to the driver (an in-memory
island the spill pool does not cover, [../decisions/0003-stage-e-spill-honest-null.md](../decisions/0003-stage-e-spill-honest-null.md)).
The fix that would let the default flip — distributing connected-components — is the
**Sail tier** ([../architecture/sail-tier.md](../architecture/sail-tier.md)): buildable
(S1–S4 green) but its binding 100M multi-node run is unrun, pending a real cluster
(`SAIL_REMOTE`). Until then Ray serves real scale and the arc stays open — exactly what
a North Star commitment that is "never done" looks like.

## Authoritative detail lives elsewhere
This node is deliberately thin. Code-level behavior, perf numbers, and gotchas are in
the `CLAUDE.md` files (see [structure.md](structure.md)); designs are in
`docs/superpowers/specs/`.

---
**Classification:** foundation/stable • **Last updated:** 2026-06-15
