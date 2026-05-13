# TS Parity Wave 3 (v0.7.0) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port Python `goldenmatch` v1.11 + v1.12 (negative evidence on weighted matchkeys + Path Y on exact matchkeys + `promote_negative_evidence` eager rule + dormant `demote_clustered_identity` rule) into `packages/typescript/goldenmatch` and release as npm `goldenmatch@0.7.0`.

**Prerequisite:** Waves 1 + 2 (PRs #138, #139) merged.

**Architecture:** Add `NegativeEvidenceField` to `MatchkeyConfig`; implement the scoring-side helper (weighted MKs) and the post-filter helper (exact MKs, "Path Y"); add the eager promote rule + (dormant) demote rule.

**Tech Stack:** Same as Waves 1+2.

**Source-of-truth Python files (READ ONLY):**
- `packages/python/goldenmatch/goldenmatch/core/autoconfig_negative_evidence.py` (~200 LOC — the centerpiece)
- `packages/python/goldenmatch/goldenmatch/core/scorer.py` (the Path Y post-filter call site; `find_exact_matches` signature stays intact, helper sits beside it)
- `packages/python/goldenmatch/goldenmatch/core/autoconfig_rules.py` (the `promote_negative_evidence` eager rule + `demote_clustered_identity` rule)
- `packages/python/goldenmatch/goldenmatch/config/matchkey.py` (the `NegativeEvidenceField` Pydantic model — port to TS interface + factory)

---

### Task 1: Add `NegativeEvidenceField` to TS types

**Files:**
- Modify: `packages/typescript/goldenmatch/src/core/types.ts`
- Test: `packages/typescript/goldenmatch/tests/unit/types.negativeEvidence.test.ts`

- [ ] **Step 1**: Add `NegativeEvidenceField` interface mirroring Python's Pydantic model: `field`, `transforms[]`, `scorer`, `threshold` (default 0.5), `penalty` (default 0.5).
- [ ] **Step 2**: Add factory `makeNegativeEvidenceField`. Extend `MatchkeyConfig` with optional `negativeEvidence?: readonly NegativeEvidenceField[]`.
- [ ] **Step 3**: Unit tests for the factory + that `makeMatchkeyConfig` accepts/round-trips NE fields.
- [ ] **Step 4**: Commit: `feat(types): NegativeEvidenceField + MatchkeyConfig.negativeEvidence`.

### Task 2: Port `autoconfig_negative_evidence.py` → `autoconfigNegativeEvidence.ts`

**Files:**
- Create: `packages/typescript/goldenmatch/src/core/autoconfigNegativeEvidence.ts`
- Test: `packages/typescript/goldenmatch/tests/unit/autoconfigNegativeEvidence.test.ts`

- [ ] **Step 1**: Port `_apply_negative_evidence(pair_score, pair_a, pair_b, matchkey)` → `applyNegativeEvidence(pairScore, recordA, recordB, matchkey): number`. Subtracts penalty when an NE field disagrees below threshold.
- [ ] **Step 2**: Port `_apply_negative_evidence_to_exact_pairs(pairs, matchkey, fullDf)` → `applyNegativeEvidenceToExactPairs(pairs, matchkey, allRows)`. Builds row lookup, applies NE per pair, filters pairs whose adjusted score falls below threshold. This is "Path Y" from v1.12.
- [ ] **Step 3**: Port `promote_negative_evidence(config, profile)` eager rule. Walks both weighted AND exact matchkeys, finds candidate NE fields (using `_pick_scorer_for_column`), attaches them with `threshold=0.5` default. **Critical:** v1.12 skips the `_is_exact_matchkey_field` gate on the exact branch.
- [ ] **Step 4**: Unit tests for all three: known agreeing-pair stays, disagreeing-pair gets penalized, exact-MK pair gets filtered when below threshold.
- [ ] **Step 5**: Commit: `feat(autoconfig): negative evidence (apply + Path Y + promote rule) TS port`.

### Task 3: Wire `applyNegativeEvidence` into weighted matchkey scoring

**Files:**
- Modify: `packages/typescript/goldenmatch/src/core/scorer.ts` (the weighted-MK pair-scoring loop)
- Test: `packages/typescript/goldenmatch/tests/unit/scorer.negativeEvidence.test.ts`

- [ ] **Step 1**: Find the weighted-MK scoring loop in `scorer.ts` (the equivalent of `find_weighted_matches` in Python). Inject `applyNegativeEvidence` AFTER weighted-sum computation, BEFORE threshold compare.
- [ ] **Step 2**: When `matchkey.negativeEvidence` is empty/undefined, no behavior change (existing tests stay green).
- [ ] **Step 3**: Unit test: weighted MK with NE field; agreeing pair survives, disagreeing pair drops out.
- [ ] **Step 4**: Commit: `feat(scorer): apply negative evidence to weighted matchkey scoring`.

### Task 4: Wire `applyNegativeEvidenceToExactPairs` (Path Y) into exact MK scoring

**Files:**
- Modify: `packages/typescript/goldenmatch/src/core/scorer.ts` (the exact-MK pair-collection path)
- Test: `packages/typescript/goldenmatch/tests/unit/scorer.pathY.test.ts`

- [ ] **Step 1**: Identify `findExactMatches` in `scorer.ts`. **Do NOT change its signature.** Instead, after it returns, post-filter through `applyNegativeEvidenceToExactPairs` if the matchkey has NE configured. This mirrors the Python v1.12 design.
- [ ] **Step 2**: Unit test: exact MK on `email`, NE on `last_name`. Pair sharing email but different surname gets filtered (Path Y).
- [ ] **Step 3**: Commit: `feat(scorer): Path Y — negative evidence on exact matchkeys`.

### Task 5: Wire `promoteNegativeEvidence` into rule set

**Files:**
- Modify: `packages/typescript/goldenmatch/src/core/autoconfigRules.ts`
- Modify: `packages/typescript/goldenmatch/src/core/autoconfigController.ts` (pre-iteration eager pass)
- Test: `packages/typescript/goldenmatch/tests/unit/autoconfigRules.negativeEvidence.test.ts`

- [ ] **Step 1**: Add `promoteNegativeEvidence` to `DEFAULT_RULES` (find its position in Python's rules list — currently position 7 or thereabouts).
- [ ] **Step 2**: Wire as an eager rule fired BEFORE the main refit loop in `AutoConfigController.run()` (matches Python's `auto_configure_df` pre-iteration pass).
- [ ] **Step 3**: Port `rule_demote_clustered_identity` — currently dormant in Python v1.12 but lives in the rules list. Position 7 in Python's `DEFAULT_RULES`.
- [ ] **Step 4**: Unit tests for eager-promote behavior + dormant demote-clustered.
- [ ] **Step 5**: Commit: `feat(autoconfig): wire promoteNegativeEvidence (eager) + demoteClusteredIdentity (dormant)`.

### Task 6: Extend fixture generator + parity test

**Files:**
- Modify: `packages/python/goldenmatch/scripts/emit_ts_parity_fixtures.py`
- Create: `packages/typescript/goldenmatch/tests/parity/negative-evidence-fixtures.json`
- Create: `packages/typescript/goldenmatch/tests/parity/negativeEvidence.parity.test.ts`

- [ ] **Step 1**: Add a fresh fixture set: 6 datasets that exercise NE — clustered-email-different-surname, clustered-phone-different-name, dense-population-needing-NE, sparse-pop-no-NE, etc.
- [ ] **Step 2**: Generator emits `{input, expected_promoted_matchkeys, expected_filtered_pairs_count, expected_committed_threshold_adjustments}`.
- [ ] **Step 3**: Parity test loads fixtures, runs TS NE flow end-to-end, asserts deep-equal on promoted MKs + filtered pair counts.
- [ ] **Step 4**: Run parity. Aim for all 6 green. If 1–2 diverge for the same Python-import-error reason as Wave 2, document and proceed.
- [ ] **Step 5**: Commit: `test(autoconfig): negative-evidence parity vs Python v1.12`.

### Task 7: Release prep

- [ ] **Step 1**: Bump `packages/typescript/goldenmatch/package.json` version 0.6.0 → 0.7.0.
- [ ] **Step 2**: CHANGELOG entry citing Python v1.11 + v1.12 + the DQbench T3 53.8% → 85.5% delta as motivation.
- [ ] **Step 3**: Full suite: `pnpm --filter goldenmatch build typecheck test`. Expect green.
- [ ] **Step 4**: Commit: `chore(release): goldenmatch-js v0.7.0`.

### Task 8: PR + merge handoff

- [ ] **Step 1**: Stop. Human will review locally before push.

## Done check

- npm `goldenmatch@0.7.0` ready to publish.
- ≥122 parity tests passing (97 prior + 6 NE parity + ~19 NE unit tests).
- Zero edits to `packages/python/goldenmatch/goldenmatch/**` (only `scripts/emit_ts_parity_fixtures.py` modified).
- DQbench-style smoke: a known clustered-email-different-surname pair gets filtered by Path Y in TS just like in Python.
