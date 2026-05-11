# TS Parity Wave 2 (v0.6.0) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port Python `goldenmatch` v1.9 + v1.10 (5 complexity indicators + indicator-aware refit rules + aggressive sparsity/collision tuning) into `packages/typescript/goldenmatch` and release as npm `goldenmatch@0.6.0`. Tighten controller parity to byte-equal committed config.

**Prerequisite:** Wave 1 (PR #138) merged.

**Architecture:** Add an `IndicatorContext` memoization layer feeding 5 pure indicator functions; wire indicators into 3 new refit rules; align the TS single-pass `autoConfigureRows` heuristic with Python's so committed configs match byte-for-byte. Edge-safe. Strict TS.

**Tech Stack:** Same as Wave 1.

**Source-of-truth Python files (READ ONLY):**
- `packages/python/goldenmatch/goldenmatch/core/indicators.py` (~366 LOC — all 5 indicator fns + `IndicatorContext`)
- `packages/python/goldenmatch/goldenmatch/core/autoconfig_controller.py` (the indicator-context wiring around the policy call)
- `packages/python/goldenmatch/goldenmatch/core/autoconfig_rules.py` (the 3 indicator-aware rules deferred in Wave 1: `rule_uniform_heavy_blocking`, `rule_recall_gap_suspected`, `rule_blocking_field_null_heavy`; plus `rule_collision_signal_too_high` / v1.10 sparsity tightening)
- `packages/python/goldenmatch/goldenmatch/core/complexity_profile.py` (the `IndicatorsProfile` sub-profile + `CollisionSignal` v1.10 threshold change)

---

### Task 1: Port `indicators.py` → `indicators.ts`

**Files:**
- Create: `packages/typescript/goldenmatch/src/core/indicators.ts`
- Test: `packages/typescript/goldenmatch/tests/unit/indicators.test.ts`

- [ ] **Step 1**: Port `IndicatorContext` class with memoization. Constructor takes `(rows, config, complexityProfile)`. Each indicator method caches its result.
- [ ] **Step 2**: Port `computeColumnPriors(rows, columns)` — returns `Map<string, ColumnPrior>` (prevalence, sparsity, uniqueness). Mirror Python's numpy-free path.
- [ ] **Step 3**: Port `estimateSparseMatchSignal(rows, config)` — returns `SparsityVerdict` (`SPARSE` / `BALANCED` / `DENSE` + supporting metrics).
- [ ] **Step 4**: Port `computeCorruptionScore(rows, config)` — float 0..1 measuring perceived typos/inconsistency.
- [ ] **Step 5**: Port `estimateFullPopHits(rows, blocking)` — projects block-collision rate.
- [ ] **Step 6**: Port `computeCrossBlockingOverlap(rows, blockingKeys)` — Jaccard-style overlap between blocking keys' candidate sets.
- [ ] **Step 7**: Unit tests for each indicator across 4 mini-datasets (clean, sparse, dirty, dense).
- [ ] **Step 8**: Commit: `feat(indicators): IndicatorContext + 5 complexity indicators TS port`.

### Task 2: Extend fixture generator + tighten parity expectation

**Files:**
- Modify: `packages/python/goldenmatch/scripts/emit_ts_parity_fixtures.py` (add indicator fields to emitted fixtures)
- Modify: `packages/typescript/goldenmatch/tests/parity/controller-stoppoint-fixtures.json` (regenerated)
- Create: `packages/typescript/goldenmatch/tests/parity/indicators-fixtures.json`

- [ ] **Step 1**: Add `expected_indicators_profile` to each dataset in the existing controller fixture.
- [ ] **Step 2**: Write a new indicator-only fixture JSON with 8 datasets stressing individual indicators (high-prevalence ID col, all-null col, heavy typos, low-overlap blocking keys, etc.).
- [ ] **Step 3**: Run generator, commit regenerated JSON.
- [ ] **Step 4**: Commit: `chore(ts-parity): wave-2 indicator fixtures`.

### Task 3: Wire indicators into 3 deferred refit rules

**Files:**
- Modify: `packages/typescript/goldenmatch/src/core/autoconfigRules.ts`
- Modify: `packages/typescript/goldenmatch/src/core/autoconfigController.ts` (instantiate `IndicatorContext` per iteration, pass into `RuleContext`)
- Modify: `packages/typescript/goldenmatch/src/core/autoconfigPolicy.ts` (consume indicator-aware rules)
- Test: `packages/typescript/goldenmatch/tests/unit/autoconfigRules.indicators.test.ts`

- [ ] **Step 1**: Port `rule_uniform_heavy_blocking`, `rule_recall_gap_suspected`, `rule_blocking_field_null_heavy`. Each consumes `IndicatorContext` via `RuleContext.indicators`.
- [ ] **Step 2**: Port `rule_collision_signal_too_high` (v1.10 introduced this; in Python at threshold 0.75 post-tightening).
- [ ] **Step 3**: Expand `DEFAULT_RULES` exported list to the v1.10 set (10 rules, Wave 3 adds the negative-evidence rule).
- [ ] **Step 4**: Add `IndicatorContext` instantiation to `AutoConfigController.run()` loop — one per iteration, fed into rule context.
- [ ] **Step 5**: Unit tests for each new rule.
- [ ] **Step 6**: Commit: `feat(autoconfig): indicator-aware refit rules (v1.10 set)`.

### Task 4: Align single-pass heuristic with Python (byte-equal config parity)

**Files:**
- Modify: `packages/typescript/goldenmatch/src/core/autoconfig.ts`

Wave 1 documented that TS emits `weighted_identity` + jaro_winkler where Python emits `fuzzy_match` ensembles. The fix:

- [ ] **Step 1**: Read `packages/python/goldenmatch/goldenmatch/core/autoconfig.py` end-to-end. Identify the column-classification → matchkey-emission decision tree. Python uses `fuzzy_match`/`exact`/`alias_match` scorer choice depending on `ColumnPrior`.
- [ ] **Step 2**: In `autoconfig.ts`, replace the current scorer-picking branch (`weighted_identity` default) with a port of Python's decision: `fuzzy_match` (Jaro-Winkler ensemble) for name-like columns, `exact` for ID-like, `alias_match` only when learning memory present.
- [ ] **Step 3**: Adjust matchkey weight/threshold defaults to match Python's `_legacy_auto_configure_v0` numbers.
- [ ] **Step 4**: Verify all 48 baseline parity tests still green AND existing scorer-ground-truth still hits the 4-decimal tolerance.
- [ ] **Step 5**: Commit: `feat(autoconfig): align scorer selection with Python (byte-equal MK shape)`.

### Task 5: Tighten controller parity to byte-equal committed config

**Files:**
- Modify: `packages/typescript/goldenmatch/tests/parity/controller-stoppoint.parity.test.ts`

- [ ] **Step 1**: Drop the structural-only assertions added in Wave 1. Replace with deep-equal on `committedConfig` (JSON-roundtrip both sides first).
- [ ] **Step 2**: Run parity. Iterate on `autoconfig.ts` until all 6 controller-fixture datasets emit byte-equal configs.
- [ ] **Step 3**: Commit: `test(autoconfig): byte-equal controller parity vs Python`.

### Task 6: Indicator parity test

**Files:**
- Create: `packages/typescript/goldenmatch/tests/parity/indicators.parity.test.ts`

- [ ] **Step 1**: Load `indicators-fixtures.json`. For each dataset: instantiate `IndicatorContext`, call each of the 5 indicators, deep-equal against fixture (numeric at 4dp, verdicts exact).
- [ ] **Step 2**: Commit: `test(indicators): parity vs Python v1.10`.

### Task 7: Release prep

- [ ] **Step 1**: Bump `packages/typescript/goldenmatch/package.json` version 0.5.0 → 0.6.0.
- [ ] **Step 2**: CHANGELOG entry citing Python v1.9 + v1.10.
- [ ] **Step 3**: Full suite: `pnpm --filter goldenmatch build typecheck test`. Expect green.
- [ ] **Step 4**: Commit: `chore(release): goldenmatch-js v0.6.0`.

### Task 8: PR + merge handoff

- [ ] **Step 1**: Wait for human to push (do not run `gh auth switch` automatically).

## Done check

- npm `goldenmatch@0.6.0` ready to publish.
- ≥97 parity tests passing (54 prior + 8 indicators + 6 byte-equal controller upgrades).
- Zero edits to `packages/python/goldenmatch/goldenmatch/**` (only `scripts/emit_ts_parity_fixtures.py` modified).
