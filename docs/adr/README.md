# Architecture Decision Records (ADRs)

Captures load-bearing architectural decisions in goldenmatch. Each ADR is a small markdown file in this directory, numbered sequentially.

## Why

The codebase has accumulated many decisions whose rationale is documented in PR descriptions, commit messages, or scattered through `docs/superpowers/specs/`. ADRs surface the **decisions themselves** — what choice was made, what alternatives were considered, what the consequences are — so future contributors can quickly understand "why is the system this way?" without archaeology.

ADRs complement specs:
- **Specs** describe HOW to build something (design + implementation plan).
- **ADRs** describe WHY a load-bearing choice was made (and what was rejected).

A spec is for the implementer. An ADR is for the next person who asks "why isn't this X instead?"

## Format

Standard [Michael Nygard format](https://github.com/joelparkerhenderson/architecture-decision-record/blob/main/locales/en/templates/decision-record-template-by-michael-nygard/index.md):

```markdown
# ADR-NNNN: Title

**Status:** Proposed | Accepted | Deprecated | Superseded by ADR-XXXX
**Date:** YYYY-MM-DD

## Context
What forced this decision? What's the situation?

## Decision
What was decided?

## Consequences
What follows from this — both positive and negative?
```

Keep each ADR short (≤ 1 page). Long debate goes in linked specs.

## Status conventions

- **Proposed** — under discussion; not yet implemented.
- **Accepted** — decided + implemented. Most ADRs.
- **Deprecated** — the decision is no longer in effect but kept for history.
- **Superseded by ADR-NNNN** — replaced by a later decision. Don't delete; link forward.

## When to write an ADR

Write one when:
- A load-bearing default changes (threshold, algorithm, API shape).
- A rejected alternative needs to stay rejected (or future contributors will keep proposing it).
- A non-obvious tradeoff was made (chose X over Y for non-obvious reason Z).
- A pattern is established that should be followed elsewhere.

Skip ADRs for:
- Bug fixes (those are commit messages).
- Routine refactors that preserve behavior.
- Decisions captured fully in a single spec or PR.

## Cross-references

- Specs: `docs/superpowers/specs/`
- Plans: `docs/superpowers/plans/`
- Roadmaps: see specs marked as `Status: roadmap` (e.g. `2026-05-21-v1-13-autoconfig-roadmap.md`).

## Index

| # | Title | Status |
|---|---|---|
| 0000 | Adopt ADR practice | Accepted |
| 0001 | `confidence_required=True` as the default safety gate | Accepted |
| 0002 | Unified `exclude_columns` API surface across the suite | Accepted |
| 0003 | Matchkey suitability vs blocking suitability as orthogonal axes | Accepted |
| 0004 | Chao1 sample-size correction for autoconfig cardinality | Accepted |
| 0005 | Streaming-block sync as the >500K-row path | Accepted |
| 0006 | Telemetry-gated rule deprecation | Accepted |
