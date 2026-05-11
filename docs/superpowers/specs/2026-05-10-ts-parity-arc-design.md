# TS Parity Arc — v0.5.0 / v0.6.0 / v0.7.0 Design

**Status:** approved (user directive 2026-05-10: "Do 2 and then execute. Go with your best judgement and don't ask me. Python behavior must stay the same.")

**Owner:** TypeScript port of `goldenmatch` (`packages/typescript/goldenmatch`).

**Constraint:** Python source code in `packages/python/goldenmatch/` is OFF LIMITS. This workstream may only:
- read Python sources as the authoritative reference,
- copy/adapt Python parity fixtures into `packages/typescript/goldenmatch/tests/parity/fixtures/`,
- modify `packages/typescript/goldenmatch/**` and the npm publish workflow.

## Why

TypeScript package `goldenmatch` is currently at v0.4.0, mirroring Python v1.6.0 (learning memory + scorer ground truth). Since then Python shipped:

| Python | Headline                                                  |
| ------ | --------------------------------------------------------- |
| v1.7   | Introspective auto-config controller (`AutoConfigController`, `RunHistory`, `HeuristicRefitPolicy`, refit rules, `ComplexityProfile`) |
| v1.8   | Best-effort commit + `StopReason` telemetry               |
| v1.9   | 5 complexity indicators wired into refit decisions        |
| v1.10  | Indicator aggression bump (collision signal, sparsity)    |
| v1.11  | Negative evidence on weighted matchkeys + clustered-identity guard |
| v1.12  | Negative evidence on exact matchkeys via Path Y post-filter |

TypeScript consumers should not be stuck two minor versions behind. We close the gap in three deliberate releases rather than one mega-PR.

## Arc shape

Three waves, each its own plan + PR + npm release. Each wave is self-contained: builds clean, tests green, parity fixtures expanded, no behavior drift in already-ported surface.

| Wave    | Target npm | Python source-of-truth | Surface added                                                                                                   |
| ------- | ---------- | ----------------------- | --------------------------------------------------------------------------------------------------------------- |
| Wave 1  | `0.5.0`    | Python v1.7 + v1.8      | `AutoConfigController` (single-step + iterate), `ComplexityProfile`, `RunHistory`, `HeuristicRefitPolicy`, 7 base refit rules, `StopReason` telemetry, best-effort commit |
| Wave 2  | `0.6.0`    | Python v1.9 + v1.10     | 5 indicators (`compute_column_priors`, `estimate_sparse_match_signal`, `compute_corruption_score`, `estimate_full_pop_hits`, `compute_cross_blocking_overlap`), `IndicatorContext` memoization, indicator-aware refit rules |
| Wave 3  | `0.7.0`    | Python v1.11 + v1.12    | `NegativeEvidenceField` config, `applyNegativeEvidence` for weighted MKs, `applyNegativeEvidenceToExactPairs` post-filter, `promoteNegativeEvidence` eager rule, dormant `demoteClusteredIdentity` rule |

Each wave ships only when its parity fixture suite is green.

## Non-goals

- Performance work. The TS port mirrors algorithm, not perf characteristics. Polars-less data plane is acceptable.
- LLM-driven scoring beyond what v0.4.0 already exposes.
- Real benchmark execution in CI (DQbench, NCVR). Parity here is via fixtures generated from the Python sibling at frozen inputs.
- Touching the existing Python implementation. Any drift discovered during porting is filed as a Python issue; the TS port matches whatever Python emits today.

## Parity strategy (applies to every wave)

1. **Fixture generation lives in Python land but is read-only there.** A small generator script under `packages/python/goldenmatch/scripts/emit_ts_parity_fixtures.py` (already present in pre-fold archive — re-introduce if missing) drives goldenmatch in-process across a curated set of inputs and dumps results as JSON into `packages/typescript/goldenmatch/tests/parity/fixtures/`. The generator is the only Python file the wave PRs may add. **No edits to existing Python files.**
2. **Tolerances**: numeric scores compare at 4 decimals (matches the v1.6 scorer parity tests). Health verdicts and stop reasons compare exact.
3. **Determinism**: every fixture pinned to a specific random seed; controller iteration is deterministic at the Python level (see `autoconfig_controller.py:_LAST_CONTROLLER_RUN`), so byte-equal JSON is the bar.
4. **Edge-safe rule still applies**: `packages/typescript/goldenmatch/src/core/**` MUST NOT import `node:*`. Memory/SQLite parity already followed this; new modules follow it.

## File map (TypeScript additions across the arc)

`src/core/` additions only. None of these files exist yet on `feature/ts-parity-v05-arc`:

```
src/core/
  autoconfigController.ts        — Wave 1
  complexityProfile.ts            — Wave 1
  autoconfigHistory.ts            — Wave 1
  autoconfigPolicy.ts             — Wave 1
  autoconfigRules.ts              — Wave 1 (7 base rules)
  indicators.ts                   — Wave 2
  autoconfigRules.indicators.ts   — Wave 2 (3 new indicator-aware rules merged into autoconfigRules.ts)
  autoconfigNegativeEvidence.ts   — Wave 3
```

Tests:

```
tests/parity/
  controller-stoppoint.parity.test.ts        — Wave 1
  controller-stoppoint-fixtures.json         — Wave 1 (generated)
  indicators.parity.test.ts                  — Wave 2
  indicators-fixtures.json                   — Wave 2 (generated)
  negative-evidence.parity.test.ts           — Wave 3
  negative-evidence-fixtures.json            — Wave 3 (generated)
```

## Risks / mitigations

- **Drift between Python implementation and Python spec docs.** Mitigation: when porting, the source of truth is the Python module's CURRENT behavior (read the .py file), not the spec doc. Spec docs are advisory; fixture diff is authoritative.
- **TS-side perf cliff.** The auto-config loop runs scorer + blocking many times. Without polars, large inputs can blow up. Mitigation: parity fixtures are <2k rows; CI uses these. Real-data ergonomics is a Wave-4 concern, not a parity concern.
- **Async surface choice.** Python is sync; the TS controller is sync too, mirroring the API one-for-one. No Promise<>-wrapping of pure compute.
- **Strict TS settings already enabled** (`exactOptionalPropertyTypes`, `noUncheckedIndexedAccess`). New code must satisfy them.

## Done definition (whole arc)

- Three npm releases on the registry: `goldenmatch@0.5.0`, `@0.6.0`, `@0.7.0`.
- Parity test suite: ≥48 (baseline) + ~25 (Wave 1) + ~20 (Wave 2) + ~15 (Wave 3) = ~108 tests, all passing.
- CHANGELOG entries on each wave referencing the matching Python version.
- No edits to `packages/python/goldenmatch/goldenmatch/**`.
