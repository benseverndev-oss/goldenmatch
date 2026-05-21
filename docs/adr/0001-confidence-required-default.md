# ADR-0001: `confidence_required=True` as the default safety gate

**Status:** Accepted
**Date:** 2026-05-21 (decision originally landed 2026-05-16, PR #271-#276)

## Context

Auto-config can commit a RED-health profile when the controller exhausts its iteration budget on adversarial data. Pre-2026-05-16, the controller would silently commit that RED config and the downstream pipeline would run a 22-26 minute degenerate dedupe producing meaningless output. Users had no way to know auto-config had given up; they'd see a successful exit code and useless `gm_clusters` data.

PR #271-#276 added `ControllerNotConfidentError` — when the controller commits a RED config on a large input (`n_rows ≥ REFUSE_AT_N = 100_000`), it raises instead of running. The open question was the **default**: should auto-config raise loudly, or warn and run?

## Decision

`confidence_required=True` is the default on `auto_configure_df` / `dedupe_df` / `match_df`. RED config + `n_rows ≥ 100_000` + `confidence_required=True` → raise `ControllerNotConfidentError`. The caller has to opt into the silent low-precision path via explicit `confidence_required=False`.

Rejected alternatives:
- **`confidence_required=False` default + WARN log.** Users miss the warning and ship bad data. The asymmetry is wrong: a noisy raise costs the user 1 minute to find docs; a silent low-precision run costs them an unknown amount of trust in their downstream consumers.
- **Per-sub-profile flags** (`refuse_on_blocking_red=True`, `refuse_on_scoring_red=True`). More API surface for marginal expressiveness. The single boolean covers 95% of cases; the failing-sub-profile is surfaced on the exception for diagnosis.

## Consequences

Positive:
- Bad output never silently ships. Users see the error message + docs link immediately.
- Programs that integrate goldenmatch can catch the exception and route to an explicit-config branch.

Negative:
- Backward-incompatible for users who had pipelines depending on the old "warn and run" behavior. Mitigation: the env-var rollback path + explicit `confidence_required=False` kwarg on every entry point.
- Test fixtures < `REFUSE_AT_N` (100K rows) pass through without the gate, which means small-N test cases of degenerate behavior don't trip it. Documented as intentional; the gate is for production-scale calls.

Cross-references:
- Spec: `docs/superpowers/specs/2026-05-16-controller-budget-vs-blocking-discovery-design.md`
- PRs: #271, #272, #273, #274, #275, #276
- Related ADR-0003 (matchkey/blocking pool split) — that split's `BLOCKING_DEGENERATE` guard composes with this one.
