# ADR-0000: Adopt ADR practice

**Status:** Accepted
**Date:** 2026-05-21

## Context

`docs/superpowers/specs/` and `docs/superpowers/plans/` capture HOW features get built. But the WHY behind load-bearing decisions — why this default, why this algorithm, why this rejected — is scattered across PR descriptions, commit messages, CLAUDE.md notes, and inline comments. Future contributors keep asking "why isn't this X?" and re-proposing rejected alternatives because the rationale isn't surfaced in one place.

Recent examples surfaced in this session:
- "Why does auto-config refuse on RED config at 100K rows by default?" → answer scattered across PR #271-#276 + a controller-budget spec.
- "Why is the streaming-sync threshold 500K and not 5M?" → answer in PR #402's commit body.
- "Why doesn't the unified `exclude_columns` rescue work through env vars when the YAML field is set?" → answer in PR #406's body.

## Decision

Adopt the Michael Nygard ADR format. One small markdown file per load-bearing decision in `docs/adr/`, numbered sequentially. Index in `docs/adr/README.md`.

ADRs complement specs/plans:
- **Specs** = how to build (design + implementation).
- **Plans** = sequencing (steps + PR shape + tests).
- **ADRs** = why a load-bearing choice was made + what alternatives were rejected.

## Consequences

Positive:
- Future "why is this X?" questions resolve in one place.
- Rejected alternatives stay rejected — future contributors see the rationale before re-proposing.
- New contributors get the architectural shape from `docs/adr/` instead of archaeology.

Negative:
- Process overhead: one more thing to write when shipping a load-bearing change.
- Risk of ADR sprawl if every minor decision gets one. Mitigation: README's "when to write an ADR" criteria.

Neutral:
- ADRs are not gospel. Decisions get superseded. ADRs are append-only with status changes; old ADRs stay linked from their successors.
