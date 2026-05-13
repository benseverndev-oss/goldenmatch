# TS Parity Wave 1 (v0.5.0) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port Python `goldenmatch` v1.7 + v1.8 (introspective auto-config controller + best-effort commit + StopReason telemetry) into `packages/typescript/goldenmatch` and release as npm `goldenmatch@0.5.0`.

**Architecture:** Mirror the Python module layout one-to-one. Sync API surface. Edge-safe (no `node:*` imports under `src/core/`). Parity validated by golden-fixture JSON generated from the Python sibling.

**Tech Stack:** TypeScript 5.4 strict, vitest 4, tsup, pnpm. No new runtime deps.

**Source-of-truth Python files (READ ONLY — do not edit):**
- `packages/python/goldenmatch/goldenmatch/core/autoconfig_controller.py`
- `packages/python/goldenmatch/goldenmatch/core/complexity_profile.py`
- `packages/python/goldenmatch/goldenmatch/core/autoconfig_history.py`
- `packages/python/goldenmatch/goldenmatch/core/autoconfig_policy.py`
- `packages/python/goldenmatch/goldenmatch/core/autoconfig_rules.py` (only the 7 base v1.7/v1.8 rules — indicator-aware rules are Wave 2)

---

### Task 1: Bootstrap branch state and fixture script

**Files:**
- Create: `packages/python/goldenmatch/scripts/emit_ts_parity_fixtures.py`
- Create: `packages/typescript/goldenmatch/tests/parity/controller-stoppoint-fixtures.json` (output)

- [ ] **Step 1**: Verify branch `feature/ts-parity-v05-arc` checked out and `git status` clean of source edits.
- [ ] **Step 2**: Write `scripts/emit_ts_parity_fixtures.py` — runs `AutoConfigController` on 6 curated mini-datasets (clean-people, sparse-people, dirty-people, exact-id, mixed-blocking, two-cluster) with fixed seed, emits JSON `{datasetName: {input_rows, expected_committed_config, expected_run_history, expected_complexity_profile, expected_stop_reason}}`.
- [ ] **Step 3**: Run script: `python packages/python/goldenmatch/scripts/emit_ts_parity_fixtures.py --out packages/typescript/goldenmatch/tests/parity/controller-stoppoint-fixtures.json`. Confirm JSON file written.
- [ ] **Step 4**: Commit: `chore(ts-parity): wave-1 fixture generator + initial controller fixtures`.

### Task 2: Port `complexity_profile.py` → `complexityProfile.ts`

**Files:**
- Create: `packages/typescript/goldenmatch/src/core/complexityProfile.ts`
- Test: `packages/typescript/goldenmatch/tests/unit/complexityProfile.test.ts`

- [ ] **Step 1**: Port enums `HealthVerdict` (`GREEN`/`YELLOW`/`RED`), `StopReason` (8 variants — copy verbatim from `complexity_profile.py`). Use TS string literal unions; export const objects for enum-like access.
- [ ] **Step 2**: Port dataclasses: `ColumnPrior`, `SparsityVerdict`, `CollisionSignal`, `IndicatorsProfile` (Wave-1 fields only — leave indicator-specific fields optional), `DataProfile`, `ComplexityProfile`. Use `readonly` interfaces + factory functions matching naming style of `types.ts`.
- [ ] **Step 3**: Port `compute_complexity_profile(rows, config)` top-level builder. It currently chains data → domain → matchkey → blocking → scoring → cluster sub-profiles. Mirror exactly. Use existing TS `profileRows` + `detectDomain` outputs as inputs to the data and domain sub-profiles to avoid duplicating that logic.
- [ ] **Step 4**: Write unit tests covering the 6 fixture datasets' `expected_complexity_profile` values from Task 1.
- [ ] **Step 5**: Run `pnpm --filter goldenmatch test tests/unit/complexityProfile.test.ts`. Expect PASS.
- [ ] **Step 6**: Commit: `feat(autoconfig): ComplexityProfile + HealthVerdict + StopReason TS port`.

### Task 3: Port `autoconfig_history.py` → `autoconfigHistory.ts`

**Files:**
- Create: `packages/typescript/goldenmatch/src/core/autoconfigHistory.ts`
- Test: `packages/typescript/goldenmatch/tests/unit/autoconfigHistory.test.ts`

- [ ] **Step 1**: Port `HistoryEntry`, `PolicyDecision`, `ErrorRecord` interfaces.
- [ ] **Step 2**: Port `RunHistory` class with `append(entry)`, `appendError(err)`, `pickCommitted()` (lex key `(health_rank, -mass_separation, iteration)`), `stopReason` getter, `stopReason` setter. Mirror Python semantics: best-effort commit picks YELLOW over no-commit-with-RED.
- [ ] **Step 3**: Unit tests for `pickCommitted` ranking + stop-reason setter idempotency.
- [ ] **Step 4**: Commit: `feat(autoconfig): RunHistory + PolicyDecision TS port`.

### Task 4: Port `autoconfig_policy.py` → `autoconfigPolicy.ts`

**Files:**
- Create: `packages/typescript/goldenmatch/src/core/autoconfigPolicy.ts`
- Test: `packages/typescript/goldenmatch/tests/unit/autoconfigPolicy.test.ts`

- [ ] **Step 1**: Port `HeuristicRefitPolicy` — turns a `ComplexityProfile` + last `HistoryEntry` into a `PolicyDecision` describing which rules fire and which direction. Mirror branch order from Python exactly.
- [ ] **Step 2**: Unit tests for each verdict branch (GREEN→commit, YELLOW→refit-soft, RED→refit-hard, oscillation→stop).
- [ ] **Step 3**: Commit: `feat(autoconfig): HeuristicRefitPolicy TS port`.

### Task 5: Port the 7 base refit rules from `autoconfig_rules.py`

**Files:**
- Create: `packages/typescript/goldenmatch/src/core/autoconfigRules.ts`
- Test: `packages/typescript/goldenmatch/tests/unit/autoconfigRules.test.ts`

- [ ] **Step 1**: Identify the v1.7/v1.8 rule set in `autoconfig_rules.py`. Currently file has 14 rules total; Wave 1 ports only the first 7 (indicator-aware ones start mid-file — search for `compute_column_priors` import to find the split). Add a comment in the TS file listing which rules are intentionally omitted (Wave 2/3).
- [ ] **Step 2**: Port each rule as a pure function `(ctx: RuleContext) => RuleOutcome | null`. `RuleContext` carries `config`, `history`, `complexityProfile`, `iteration`.
- [ ] **Step 3**: Export `DEFAULT_RULES_V1_7_V1_8` ordered array. Order matches Python.
- [ ] **Step 4**: Unit tests: each rule independently with a minimal context.
- [ ] **Step 5**: Commit: `feat(autoconfig): 7 base refit rules (v1.7/v1.8 set) TS port`.

### Task 6: Port `autoconfig_controller.py` → `autoconfigController.ts`

**Files:**
- Create: `packages/typescript/goldenmatch/src/core/autoconfigController.ts`
- Test: `packages/typescript/goldenmatch/tests/unit/autoconfigController.test.ts`

- [ ] **Step 1**: Port `AutoConfigController` class. Constructor takes `(rows, baseConfig, options)`. Public method `run()` returns `{committedConfig, runHistory, complexityProfile}`.
- [ ] **Step 2**: Inside `run()`: compute initial `ComplexityProfile`, loop up to `maxIterations` (default 8): policy → rule → apply → score-sample → record → check stop. Mirror Python control flow including the best-effort commit fallback at loop end (v1.8).
- [ ] **Step 3**: Wire `StopReason` settings at every exit path (CONVERGED, MAX_ITERATIONS, OSCILLATION_DETECTED, PRECISION_FLOOR, RECALL_FLOOR, RULE_EXHAUSTION, ERROR, INITIAL_GREEN).
- [ ] **Step 4**: Module-level `_lastControllerRun` cache (private), exported `getLastControllerRun()` accessor — matches Python's `_LAST_CONTROLLER_RUN` debug surface.
- [ ] **Step 5**: Unit tests use the same 6 fixtures from Task 1 with their `expected_run_history` + `expected_stop_reason`.
- [ ] **Step 6**: Commit: `feat(autoconfig): AutoConfigController TS port with StopReason telemetry`.

### Task 7: Wire controller into public `autoconfig()` entry point

**Files:**
- Modify: `packages/typescript/goldenmatch/src/core/autoconfig.ts`
- Modify: `packages/typescript/goldenmatch/src/core/index.ts`
- Modify: `packages/typescript/goldenmatch/src/index.ts`

- [ ] **Step 1**: In `autoconfig.ts`, add an `iterate?: boolean` (default `false` to preserve v0.4.0 behavior) to `AutoconfigOptions`. When `true`, route through `AutoConfigController.run()` and return the committed config; otherwise keep the existing single-pass path.
- [ ] **Step 2**: Export `AutoConfigController`, `RunHistory`, `ComplexityProfile`, `HealthVerdict`, `StopReason` from `src/core/index.ts` and re-export from `src/index.ts`.
- [ ] **Step 3**: Verify the existing 48 parity tests still pass: `pnpm --filter goldenmatch test tests/parity/`.
- [ ] **Step 4**: Commit: `feat(autoconfig): expose iterate option + controller from public API`.

### Task 8: Parity test against Python fixtures

**Files:**
- Create: `packages/typescript/goldenmatch/tests/parity/controller-stoppoint.parity.test.ts`

- [ ] **Step 1**: Load `controller-stoppoint-fixtures.json`. For each dataset: run TS `AutoConfigController.run(rows, baseConfig)`, compare:
  - committed config (deep equal after JSON-roundtrip),
  - run history length + per-entry stop_reason / health_verdict / mass_separation (numeric @4dp),
  - final stop_reason exact match,
  - complexityProfile.dataProfile fields exact match.
- [ ] **Step 2**: Run `pnpm --filter goldenmatch test tests/parity/controller-stoppoint.parity.test.ts`. Expect all 6 dataset cases PASS.
- [ ] **Step 3**: Commit: `test(autoconfig): controller parity vs Python v1.8`.

### Task 9: Release prep

**Files:**
- Modify: `packages/typescript/goldenmatch/package.json` (version 0.4.0 → 0.5.0)
- Modify: `packages/typescript/goldenmatch/CHANGELOG.md`

- [ ] **Step 1**: Bump version. Add CHANGELOG entry naming Python v1.7 + v1.8 as the source-of-truth.
- [ ] **Step 2**: Run full test + build: `pnpm --filter goldenmatch build typecheck test`. Expect all green.
- [ ] **Step 3**: Commit: `chore(release): goldenmatch-js v0.5.0`.

### Task 10: PR + merge

- [ ] **Step 1**: `gh auth switch --user benzsevern`, push branch, open PR titled `feat(ts): goldenmatch v0.5.0 — auto-config controller parity (Python v1.8)`.
- [ ] **Step 2**: After CI green: squash-merge, delete branch.
- [ ] **Step 3**: `gh auth switch --user benzsevern-mjh` immediately.
- [ ] **Step 4**: Tag `goldenmatch-js-v0.5.0` at HEAD of main; release notes from CHANGELOG.

## Done check
- `goldenmatch-js@0.5.0` on npm.
- ≥73 parity tests passing locally (48 baseline + 6 controller datasets + ~19 unit tests).
- Zero edits to `packages/python/goldenmatch/goldenmatch/**` (only `scripts/emit_ts_parity_fixtures.py` added).
