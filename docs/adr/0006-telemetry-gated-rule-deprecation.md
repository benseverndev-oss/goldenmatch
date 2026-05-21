# ADR-0006: Telemetry-gated rule deprecation

**Status:** Accepted
**Date:** 2026-05-21 (pattern established for #124's deletion process)

## Context

`rule_demote_clustered_identity` (v1.11) was designed for one specific failure mode (T3 adversarial collision) that Path Y (v1.12) ended up solving differently. Phase 7 diagnostic showed the rule never fires in real workloads — earlier rules exhaust iteration budget first.

Synthetic tests for the rule pass because they invoke it directly. But "never fires in production" is hard to prove without observation.

The naive deletion path is: drop the rule + its 5-file collateral chain, run benchmarks, ship. The risk: a production workload somewhere DOES fire it, the deletion silently degrades that workload's matching, and the regression doesn't surface until much later.

## Decision

For autoconfig rules that are candidates for deletion, ship in two waves:

**Wave 1 — Telemetry.** Add an INFO log at the rule's fire path, gated by an opt-in env var (`GOLDENMATCH_TELEMETRY_<RULE_NAME>=1`). Production users opt in; we collect firing counts via grep over a 1-2 week observation window.

**Wave 2 — Deletion.** When telemetry confirms zero firings in production, delete the rule + every piece of collateral (helpers, indicators, dataclasses, test files, `DEFAULT_RULES` count assertions). Runs the full benchmark suite as the regression gate.

The deletion gate is:
- Zero firings in the telemetry window.
- DQbench composite ≥ v1.12 floor.
- No benchmark F1 regression.

Rejected alternatives:
- **Direct deletion.** Documented above as the risk path.
- **Feature flag the rule, default off.** Adds permanent code paths that aren't deleted. Worse outcome than the two-wave approach.
- **Mark deprecated + leave running.** Doesn't actually remove the maintenance surface or the iteration-budget cost. The point is to remove dead code, not annotate it.

## Consequences

Positive:
- Removes the "did anyone use this?" uncertainty before deletion.
- The pattern generalizes — any future rule deprecation follows the same two-wave shape.
- Forces honest scoping: if telemetry shows non-zero firings, the deletion case has to be re-argued.

Negative:
- 1-2 week delay between Wave 1 and Wave 2. Requires patience.
- The telemetry log is one more env var users have to know about. Documented in CLAUDE.md / spec.
- If the env var isn't set anywhere in production (no opt-in), the observation window produces no signal. Mitigation: include the env var in the "release notes / migration guide" that prompts users to opt in.

Cross-references:
- Spec: `docs/superpowers/specs/2026-05-21-demote-rule-deletion-design.md`
- Issue: #124
- Roadmap: `docs/superpowers/specs/2026-05-21-v1-13-autoconfig-roadmap.md` Wave B (telemetry) → Wave C (deletion)
