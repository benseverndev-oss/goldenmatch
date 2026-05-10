# Auto-Config Path Y — NE on Exact Matchkeys (v1.12)

**Status:** Design (approved by user 2026-05-09; spec-review pass pending)
**Author:** brainstorm session, Claude + bsevern
**Scope:** extend `_apply_negative_evidence` (`core/scorer.py`) to exact matchkeys; extend `promote_negative_evidence` (`core/autoconfig_negative_evidence.py`) to walk all matchkey types; reuse `MatchkeyConfig.threshold` field as the score-vs-threshold gate for NE-enabled exact matchkeys; tests + integration
**Related:**
- v1.11 spec: `2026-05-08-autoconfig-negative-evidence-and-clustered-identity-design.md`. v1.11 §Non-goals explicitly excluded "Negative evidence on `exact` matchkeys" with the rationale "subtracting from a binary 1.0 was considered (Path Y in brainstorm) and rejected as semantic confusion." **v1.12 amends this**: Path Y is now adopted on the strength of Phase 7 diagnostic evidence (`.profile_tmp/v111_t3_diagnostic.txt`) that proved Path X (clustered-guard demote → fuzzy + NE) cannot reach T3.
- v1.11 release: PR #121, 1986 tests passing, DQbench composite 66.99 (parity with v1.10).

## Problem

v1.11 shipped with infrastructure for negative-evidence scoring + clustered-identity-guard but DQbench T3 stayed at 53.8% F1 — the headline lever didn't move. Phase 7 diagnostic surfaced two structural issues:

1. **The collision-signal metric isn't discriminative.** T3's collision_rate is 0.59; T2's is 0.62. T3 — the adversarial dataset — is LESS collision-prone by this within-email-group address-divergence metric than T2. Any threshold that fires on T3 also fires on T2 (false positive: 186 added FNs). The metric measures something different from what we thought.
2. **Iteration budget exhausted before demote rule runs.** `rule_blocking_too_coarse` oscillates between `last_name` and `email` blocking, never converging. The committed config falls back to v0 (iter=-1), so `rule_demote_clustered_identity` at position 7 never executes inside the iteration loop on real T3.

But the diagnostic also surfaced the actual root cause of T3's FPs: **the `exact_email` matchkey directly emits FP pairs**. When the same email is shared across distinct entities (T3's adversarial pattern), `exact_email` returns 1.0 — a hard match — regardless of any NE on the weighted matchkey. v1.11's `_apply_negative_evidence` only fires on weighted matchkeys per spec §Non-goals.

The fix is **Path Y**: extend NE to exact matchkeys. When an exact matchkey has `negative_evidence` populated and a `threshold` set, score = `max(0, 1.0 - sum(disagreement_penalties))`; emit only if `score >= threshold`. T3's collision pairs (same email, divergent phone+address) get penalty 0.7 → final 0.3 → below 0.5 threshold → filtered. T3's true duplicates (same email + agreeing phone+address) get penalty 0.0 → final 1.0 → emitted.

## Goals

1. **DQbench composite ≥ 75 no-LLM** (primary; v1.11 was 66.99).
2. **DQbench composite ≥ 70 no-LLM** (fallback contract).
3. **DQbench T3 F1 ≥ 70%** (headline lever; diagnostic projects 85-90%).
4. **DBLP-ACM/Febrl3/NCVR/T1/T2 each F1 ≥ v1.11 baseline** (regression hard floor).
5. **Wall-clock budget**: `auto_configure_df(df)` on 50K-row dataset within 100s (unchanged from v1.11; Path Y adds ~1s on T3-class data, 0s elsewhere).

### Composite math (back-of-envelope)

DQbench weights are opaque without harness inspection; assume roughly equal thirds. v1.11 baseline: T1=88.9%, T2=69.0%, T3=53.8% → composite 66.99.

| T3 landing | Composite (equal-thirds estimate) | Verdict |
|---|---|---|
| 70% | (88.9 + 69.0 + 70.0) / 3 ≈ 75.97 | Just clears primary ≥75 |
| 80% | (88.9 + 69.0 + 80.0) / 3 ≈ 79.30 | **Primary met with margin** |
| 85% | (88.9 + 69.0 + 85.0) / 3 ≈ 80.97 | Diagnostic projects this |
| 90% | (88.9 + 69.0 + 90.0) / 3 ≈ 82.63 | Aggressive bound |

**Footnote:** DQbench's actual composite formula is opaque without harness inspection. The equal-thirds estimate above is back-of-envelope. v1.10 measured composite 66.91 with T1/T2/T3 = 88.9/69.0/53.8 → equal-thirds estimate would be 70.57; actual was 66.91 (~3.5pt lower than equal-thirds). DQbench likely weights tiers non-uniformly or uses a non-arithmetic mean. Real composite at T3=70% may be in the 73-76 range; at T3=80% may be 76-79. Both still clear the ≥70 fallback; primary ≥75 depends on T3 landing closer to 80%+.

Plus possible T1/T2 lift: same FP pattern (collision pairs with divergent witnesses) appears in T1 and T2 too. Path Y on those benchmarks' exact_email matchkeys may push T1 from 88.9% and T2 from 69.0% as well. Bound: T1+T2 lift is bonus; primary target depends only on T3.

## Non-goals (this spec)

- **Drop `rule_demote_clustered_identity`** — kept dormant in v1.12. Phase 7 diagnostic showed the rule **never executes on T3** under v1.11's committed config (oscillation in `rule_blocking_too_coarse` exhausts iteration budget at position ~3, well before the demote rule at position 7). The rule's synthetic test passes because the test directly invokes the rule, bypassing the iteration controller. So the rule is effectively dead code for the use case it was designed for — Path Y subsumes its purpose for T3, and the rule has no measured firing on real data. Remove in v1.13 after telemetry confirms it never fires in production. Kept in v1.12 only because the test is green and removing now would shrink coverage of the synthetic-data behavior path.
- **NE on probabilistic matchkeys** — Fellegi-Sunter has its own scoring framework; NE-as-subtraction doesn't fit. v1.13+ candidate.
- **Adaptive penalty/threshold tuning** — fixed defaults `(threshold=0.4, penalty=0.3)` per NE field; `matchkey.threshold=0.5` default for NE-enabled exact matchkeys. Adaptive candidate for v1.13.
- **Iteration oscillation fix** — Phase 7 surfaced this in `rule_blocking_too_coarse` but Path Y bypasses it (Path Y is eager via `promote_negative_evidence`, not iteration-driven). v1.13 candidate if other rules stall similarly.
- **TypeScript port parity** — Python-first; TS port catches up in a separate workstream.
- **New collision-signal metric** — the v1.11 metric's non-discrimination of T3 vs T2 is a known issue, but Path Y eliminates the need for the demote rule to fire on T3. The metric stays as-is for the dormant demote rule.
- **New schema fields on `MatchkeyConfig`** — Path Y reuses the existing `threshold` field (currently only honored on weighted matchkeys; v1.12 also honors it on NE-enabled exact matchkeys).

## Decision summary

| Decision | Choice | Why |
|---|---|---|
| Scope | Path Y only — extend NE to exact matchkeys via `_apply_negative_evidence` | Single-lever delivery worked in v1.10's T2 recovery; v1.11's hedging produced parity-only outcome |
| Mechanic | δ — reuse `MatchkeyConfig.threshold` field as score gate for NE-enabled exact matchkeys (default 0.5 when NE set + threshold None) | No schema change; backward-compat preserved (exact matchkey without NE ignores threshold per today's behavior) |
| Eager promotion | Extend `promote_negative_evidence` to populate NE on ALL matchkey types (not just weighted) | Without this, Path Y mechanism doesn't fire on the T3 path automatically |
| Acceptance | composite ≥75 primary / ≥70 fallback; T3 ≥ 70% | Conservative (v1.10+v1.11 missed primary); evidence-grounded fallback |
| `rule_demote_clustered_identity` disposition | Keep dormant | Test coverage; remove in v1.13 if telemetry shows it never fires |
| Spec amendment | Append §Amendment-2 to v1.11 spec; new file for v1.12 | v1.12 is its own release; spec doc per release for clean per-release reading |

## Architecture

Two surgical changes to existing v1.11 code; no new modules.

### 1. `_apply_negative_evidence` extends to exact matchkeys

Today (v1.11), the helper in `core/scorer.py` is called only from the weighted-matchkey scoring path per spec §Non-goals. v1.12: also call it from the exact-matchkey scoring path when NE is populated. The helper itself is unchanged — same per-pair penalty logic works on both matchkey types.

The exact-matchkey scoring path in `find_exact_matches` (or equivalent) becomes:

```python
for pair in candidate_pairs:
    if all_fields_equal(pair, matchkey.fields):
        if matchkey.negative_evidence:
            score_negative = _apply_negative_evidence(matchkey, pair_dict)
            final_score = max(0.0, 1.0 - score_negative)
            threshold = matchkey.threshold if matchkey.threshold is not None else 0.5
            if final_score >= threshold:
                emit (pair, score=final_score)
        else:
            emit (pair, score=1.0)    # today's binary behavior preserved
```

**Backward compat invariant**: an exact matchkey WITHOUT `negative_evidence` produces today's binary 1.0/0.0 output, threshold ignored. Behavior change ONLY when NE is present.

### 2. `promote_negative_evidence` extended to all matchkey types

v1.11's eager rule iterates only weighted matchkeys (`if mk.type != "weighted": continue`) AND applies a `_is_exact_matchkey_field` gate that blocks promotion when the candidate column is not present in any exact matchkey. v1.12 walks all matchkey types and selectively applies the v1.11 gates per matchkey type:

| Gate | v1.11 behavior | v1.12 weighted branch | v1.12 exact branch |
|---|---|---|---|
| `identity_score >= 0.75` | Applied | Applied (unchanged) | **Applied** |
| `cardinality_ratio >= 0.5` | Applied | Applied (unchanged) | **Applied** |
| `col not in matchkey.fields` (this matchkey) | Applied | Applied (unchanged) | **Applied** |
| `col not in blocking.keys` | Applied | Applied (unchanged) | **Applied** |
| `_is_exact_matchkey_field(col, all_matchkeys)` (col must be in some exact matchkey) | Applied | Applied (unchanged) — protects against NE-on-weighted-without-anchor regression | **SKIPPED** — the gate's rationale ("phone disagreement on a weighted match is ambiguous when there's no exact phone matchkey") doesn't apply when we're iterating an exact matchkey for itself. Phone disagreement on `exact_email` is unambiguously a collision signal. |

**Why the gate skip is critical for T3:** T3's v0 produces `exact_email` (not `exact_phone` or `exact_address`). With v1.11's gate active on the exact-matchkey iteration, phone and address would NOT pass — the gate looks for them in some exact matchkey, finds none. NE wouldn't be added; Path Y wouldn't deliver. Skipping the gate on the exact-matchkey branch is what makes T3 work.

Plus: when NE is added to an exact matchkey by this function and `matchkey.threshold` is None, set `threshold = 0.5`. Existing user-set thresholds are respected. Existing exact matchkeys without qualifying NE candidates are unchanged.

T3 application:
- v0 produces matchkeys: `exact_email` (type=exact, threshold=None, NE=None) and `fuzzy_match` (type=weighted, threshold=0.8, NE=None).
- v1.12 `promote_negative_evidence`:
  - Walks `exact_email`: identity-prior columns NOT in matchkey.fields = {phone, address, ...}. Adds NE for phone (token_sort, threshold=0.4, penalty=0.3) and address (token_sort, threshold=0.4, penalty=0.4). Since `exact_email.threshold` was None and NE was added, sets `exact_email.threshold = 0.5`.
  - Walks `fuzzy_match`: same NE candidates. Adds NE for phone+address (existing v1.11 behavior).
- Both matchkeys now have NE.

### Data flow

**Pre-iteration (one-time, eager):** v1.11's flow + the loop extension above.

**Per-pair scoring (hot path):**
- Exact matchkey without NE → today's binary path (zero new cost on benchmarks where NE doesn't fire).
- Exact matchkey with NE → score-and-threshold path; ~2 scorer calls per NE field per pair (~100K extra calls on T3's 50K candidate pairs); per rapidfuzz cdist ~1s additional wall-clock.

**T3 collision pair example** (same email, divergent phone+address):
- `all_fields_equal(email)` → True
- NE phone digits-only-exact → 0.0 → penalty 0.3
- NE address token_sort → 0.38 → penalty 0.4
- final = max(0, 1.0 - 0.7) = 0.3 → below 0.5 threshold → NOT emitted

**T3 true duplicate** (same email + agreeing phone+address):
- All NE fields agree → no penalty → final 1.0 → emitted

**Single-field-disagreement invariant**: at default penalties (0.3 for phone, 0.4 for address), single-field NE disagreement keeps the pair above threshold:
- phone alone disagrees → penalty 0.3 → final 0.7 ≥ 0.5 → match preserved
- address alone disagrees → penalty 0.4 → final 0.6 ≥ 0.5 → match preserved
- BOTH phone AND address disagree → penalty 0.7 → final 0.3 < 0.5 → filtered

So a true duplicate where ONE of phone/address disagrees (e.g. apartment number drift, phone reformat) is preserved. A pair where BOTH disagree (T3's adversarial pattern) is filtered. The bound is "single-field disagreement at default penalties (≤0.4) is safe; cumulative penalty must exceed `1.0 - threshold = 0.5` to filter."

Caveat: with two NE fields at default 0.3 penalty each (e.g. phone + zip), total penalty is 0.6 → final 0.4 → filtered. The invariant depends on the specific penalty values. T1 risk bound: TPs have phone agreement ≥ 0.95 (per Phase 7 diagnostic), so phone NE rarely fires; address NE alone is below the 0.5 cumulative threshold. Bounded.

### Memory cache

No schema change. v1.10 + v1.11 cache entries deserialize cleanly (NE field defaults to None on legacy entries; threshold field is already present on `MatchkeyConfig`). New v1.12 entries with NE on exact matchkeys serialize through the existing Pydantic field — no migration logic.

## Components

| Component | Type | LOC | Description |
|---|---|---|---|
| `_apply_negative_evidence` extension | Modify in `core/scorer.py` | ~30 | Same pure helper; now called from both weighted and exact paths. |
| Exact-matchkey scoring path in `find_exact_matches` | Modify in `core/scorer.py` | ~50 | Switch from binary-output to score-and-threshold output when NE is populated. Reuse `_apply_negative_evidence`. Defensive: if NE present but threshold None, log INFO + use 0.5 default. |
| `promote_negative_evidence` extension | Modify in `core/autoconfig_negative_evidence.py` | ~20 | Loop change: iterate all matchkey types. Add 5-line block: when adding NE to an exact matchkey, also set `matchkey.threshold = 0.5` if None. |
| `_pick_scorer_for_column` | Unchanged from v1.11 | 0 | Already handles all column types. |
| `_apply_negative_evidence` defensive guards | Modify | ~10 | Penalty exceeding 1.0 clamped to floor 0.0. INFO log on first NE-on-exact firing per matchkey-per-run. |
| Tests | New + extended | ~340 | 8 Tier 3 + 5 Tier 1 + 1 Tier 4 update + 2 Tier 6 + 3 Tier 5 = ~19 new tests |
| Spec amendment | This file | ~30 | Spec for v1.12 release. v1.11 spec §Non-goals "NE on exact matchkeys" is reversed via this v1.12 spec; the v1.11 spec doc itself stays unchanged for archival fidelity. |

**Total: ~280 LOC code + ~340 LOC tests = ~620 LOC.** Smaller than v1.11 (~850 LOC) since v1.12 extends existing functions rather than adding new modules.

## Error handling

| Failure mode | Where | Behavior |
|---|---|---|
| Exact matchkey has NE but threshold is None | `_apply_negative_evidence` boundary | Default to 0.5; INFO log once per matchkey-per-run |
| NE-list non-empty but all entries non-firing | `_apply_negative_evidence` | Returns 0.0 penalty; pair scored at 1.0; emitted as match (same as today's binary) |
| Penalty sum > 1.0 | Scorer | `max(0.0, 1.0 - penalty)` clamps to 0.0 floor; pair scored at 0.0 → not emitted |
| Pre-v1.12 user config with manually-added NE on exact matchkey (rare; pre-v1.12 ignored it) | Loaded into v1.12 | v1.12 honors NE — silent behavior change. Documented in CHANGELOG breaking-changes |
| `promote_negative_evidence` adds NE but the exact matchkey is later mutated to remove threshold | Scorer | Defensive: NE present + threshold None → use 0.5 default; logged at INFO |
| Cache entry with v1.10/v1.11 schema | Deserialization | Default-None NE preserved; binary path; no behavior change |

**Backward compat:**
- v1.10 + v1.11 cache entries load cleanly (no schema change).
- Custom user `RefitPolicy` implementations: no signature change; piggybacks on v1.11's `ctx` kwarg.
- TypeScript port parity: tracked separately at suite level. Not a v1.12 blocker.
- External callers reading `MatchkeyConfig.threshold` on exact matchkeys: today's behavior reads None (or user-set value); v1.12 same. Only the scorer's interpretation changes.

**Three concrete bugs prevented:**
1. Penalty exceeding 1.0 → `max(0, ...)` floor.
2. Recall regression on T1 from over-aggressive NE → bounded by multi-field-must-disagree invariant. Single NE disagreement (penalty 0.3 or 0.4) keeps final at 0.6 or 0.7 (above 0.5 default threshold).
3. `promote_negative_evidence` over-applying on benchmarks where exact matchkeys are fine → protected by v1.11's gates (identity_score >= 0.7, cardinality_ratio >= 0.5, col not in matchkey.fields, col not in blocking). NE only fires when both fields disagree, which is rare on clean benchmarks.

**Thread-safety:** unchanged from v1.11.

**Logging:**
- `_apply_negative_evidence` on exact-matchkey path logs INFO once per matchkey-per-run when NE+threshold are both set: `"auto-config: NE active on exact matchkey '%s' (N=%d NE fields, threshold=%.2f)"`.
- Existing v1.11 WARNING logs (unknown scorer, missing field) carry over unchanged.

## Testing

### Tier 3 — NE-on-exact scoring (8 tests, ~120 LOC)

`tests/test_negative_evidence_scoring.py` extension:
- `test_exact_matchkey_with_ne_and_threshold_filters_disagreeing_pair`
- `test_exact_matchkey_with_ne_keeps_agreeing_pair`
- `test_exact_matchkey_without_ne_preserves_binary_behavior`
- `test_exact_matchkey_with_ne_but_no_threshold_uses_default_0_5`
- `test_exact_matchkey_with_ne_minor_address_noise_preserves_match` (single-field-disagree at penalty 0.4 leaves 0.6 above 0.5)
- `test_exact_matchkey_with_ne_severe_single_field_disagree_still_matches`
- `test_exact_matchkey_penalty_exceeds_one_clamps_to_zero`
- `test_exact_matchkey_with_user_set_threshold_respected`

### Tier 1 — `promote_negative_evidence` extension (5 tests, ~60 LOC)

`tests/test_autoconfig_negative_evidence.py` extension:
- `test_promote_ne_populates_exact_matchkey_too`
- `test_promote_ne_sets_default_threshold_on_exact_when_none`
- `test_promote_ne_preserves_user_set_threshold_on_exact`
- `test_promote_ne_skips_exact_matchkey_when_no_candidates_qualify`
- `test_promote_ne_idempotent_on_exact_matchkey`

### Tier 4 — T3 recovery integration (1 new + 1 update, ~80 LOC)

`tests/test_dqbench_t3_recovery.py` extension:
- NEW `test_t3_synthetic_path_y_filters_collision_pairs`: asserts NE on exact_email + collision pairs filtered + true dups preserved (precision ≥ 0.85, recall ≥ 0.90)
- Update v1.11's `test_t3_synthetic_recovers_precision`: was xfailed; v1.12 should pass (Path Y delivers what Path X couldn't)

### Tier 6 — Properties (2 tests, ~30 LOC)

`tests/test_autoconfig_properties.py` extension:
- `test_ne_on_exact_monotonic_in_penalty`
- `test_promote_ne_extension_idempotent_property`

### Tier 5 — Cache backward-compat (3 tests, ~40 LOC)

`tests/test_autoconfig_memory_v111_compat.py` (NEW):
- `test_v1_11_cache_entry_loads_cleanly`
- `test_v1_10_chain_compat_through_v112`
- `test_v1_12_cache_entry_with_ne_on_exact_round_trips`

### Tier 7 — Performance budget (1 test, ~30 LOC)

`tests/test_indicators_budget.py` (extension):
- `test_exact_matchkey_ne_scoring_overhead_under_budget`: build a 50K-row synthetic df with one exact matchkey + 2 NE fields; run `_apply_negative_evidence` over 50K candidate pairs; assert elapsed < 2s. Spec target is "~1s additional wall-clock"; the 2s test margin allows for CI shared-runner load while still catching O(N²) blowups.

## Acceptance criteria

1. **All test tiers pass.** ~+19 new tests (1986 → ~2005).
2. **Hard floor**: DBLP-ACM/Febrl3/NCVR each F1 ≥ v1.11 baselines (0.9641/0.9443/0.9719). T1 F1 ≥ 88.9%. T2 F1 ≥ 69.0%. **If T1 or T2 regresses, do NOT ship without resolution.** Mitigations: tighten promotion gates (require multi-record collision evidence) OR drop default penalty values to 0.2/0.3.
3. **DQbench composite ≥ 75 (no LLM)** — primary target.
4. **DQbench composite ≥ 70 (no LLM)** — fallback contract.
5. **DQbench T3 F1 ≥ 70%** — headline lever target. If T3 lands below 65%, escalate (Path Y mechanism is failing for some unanticipated reason).
6. **Wall-clock budget**: `auto_configure_df(df)` on 50K-row dataset within 100s.
7. **Cache compat**: v1.10 + v1.11 fixtures load cleanly into v1.12.
8. **PR description**: per-tier DQbench breakdown + T3 before/after P/R/F1 explicit. Spec amendment §Path Y adoption cited with diagnostic evidence (`.profile_tmp/v111_t3_diagnostic.txt`).

## Risks

- **Path Y under-delivers** — diagnostic projected T3 85-90% based on field-similarity statistics. If v0's exact_email matchkey is being scored from a different code path than the modified `find_exact_matches`, NE won't fire. Mitigation: Tier 4 synthetic test catches this explicitly.
- **T1 or T2 recall regresses** — bounded by multi-field-must-disagree invariant + v1.11 gates on `promote_negative_evidence`. If still happens, Phase 7 measurement catches before merge; tighten penalty defaults or only promote on exact when collision_rate signal fires.
- **Spec amendment churn** — v1.11 spec rejected Path Y as "semantic confusion"; v1.12 amendment adopts it on diagnostic evidence. The amendment is short and evidence-grounded.
- **`MatchkeyConfig.threshold` semantic dual-purpose** — currently only used for weighted scores; v1.12 also uses it for NE-enabled exact matchkeys. Documented as "threshold applies to exact matchkeys when NE is set." Internally consistent (NE-enabled exact ≈ weighted-with-fixed-positive-1.0).
- **Multi-field NE accumulation breaks recall on legitimate-noise datasets** — example: a 3-NE-field dataset where all 3 minor disagreements stack to penalty 0.9. Mitigation deferred to v1.13: cap NE count per matchkey at 3 if measurement surfaces this.

## Implementation sequence

1. Spec doc commit (this file)
2. Extend `_apply_negative_evidence` to exact-matchkey scoring path — Tier 3 tests + scorer.py modification, ~80 LOC
3. Extend `promote_negative_evidence` loop + threshold default — Tier 1 tests + module modification, ~80 LOC
4. Tier 4 T3 integration test (synthetic + un-xfail v1.11's test) — ~80 LOC
5. Tier 5 cache compat + Tier 6 properties — ~70 LOC
6. Phase 7 measurement: re-measure DBLP-ACM/Febrl3/NCVR + DQbench. Expected: T3 jumps; composite hits ≥75 primary or ≥70 fallback
7. CLAUDE.md + CHANGELOG + version bump 1.11.0 → 1.12.0
8. PR + auth dance + release + PyPI publish + (deferred from v1.10) wiki/About/Topics/Discussion bundle for v1.10 + v1.11 + v1.12
