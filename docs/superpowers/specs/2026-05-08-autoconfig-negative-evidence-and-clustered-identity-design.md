# Auto-Config Negative Evidence + Clustered-Identity Guard (v1.11)

**Status:** Design (approved by user 2026-05-08; spec-review pass pending)
**Author:** brainstorm session, Claude + bsevern
**Scope:** new `core/autoconfig_negative_evidence.py` module; new `compute_identity_collision_signal` indicator + `rule_demote_clustered_identity` rule; new `NegativeEvidenceField` schema on `MatchkeyConfig`; tests + integration
**Related:**
- v1.10 spec: `2026-05-08-autoconfig-indicators-design.md` (foundation: column priors, IndicatorContext, ctx-aware rules)
- v1.10 release: PR #119, shipped 2026-05-08 (DQbench composite 62.87 → 66.91)
- v1.11 trigger: T3 stayed at 53.8% F1 across v1.8/v1.9/v1.10 — diagnostic (`.profile_tmp/v111_t3_findings.txt`) traced root cause to a matchkey-shape gap, not a controller-decision gap

## Problem

DQbench tier 3 (`~/.dqbench/datasets/er_tier3/data.csv`, ~10K rows, adversarial-collision construction) sits at F1=53.8% (P=36.8%, R=100%) across three goldenmatch versions. The v1.10 controller (with 5 indicators + 13 rules) commits the same config as v1.8 and produces the same precision floor. The T3 diagnostic (2026-05-08) showed:

- T3 has identity-shaped columns (`email` cardinality 0.69, `phone` cardinality 0.85, `address` cardinality 0.83) that the controller correctly profiles.
- v1.10's committed config uses `exact_email` matchkey + a separate weighted `fuzzy_match` matchkey on `(first_name, last_name, address, company)`.
- T3's adversarial construction: same `firstname.lastname@gmail.com` is deliberately assigned to multiple distinct people in different cities with different phones and addresses.
- 60% of false positives are pairs where `exact_email` fires (binary 1.0) on collision-prone emails — `address` similarity averages 0.382 (vs 0.976 for true positives), but the matchkey has no mechanism to override `exact_email` based on `address` disagreement.
- Phone is the highest-cardinality field (0.85) but completely missing from the matchkey.

The controller has all the right *information* (column priors detect email/phone/address as identity); it lacks the *expressive power* in the matchkey shape to encode "address strongly disagrees → not a match even if email matches."

## Goals

1. **DQbench composite ≥ 75 no-LLM** (primary; v1.10 was 66.91).
2. **DQbench composite ≥ 70 no-LLM** (fallback contract; v1.10's missed primary target).
3. **DQbench T3 F1 ≥ 70%** (headline lever target; diagnostic estimated +35-40pp lift from 53.8%).
4. **DBLP-ACM/Febrl3/NCVR hold at v1.10 baselines** (no benchmark regression).
5. **Wall-clock budget**: `auto_configure_df(df)` on 50K-row dataset completes within 100s (v1.10's 90s + ~10s for collision-signal indicator).

### Composite math (back-of-envelope)

DQbench weights are opaque without harness inspection; assume roughly equal thirds across T1/T2/T3. v1.10 baseline T1=88.9%, T2=69.0%, T3=53.8% → composite 66.91.

| T3 landing | Composite (T1+T2 unchanged) | Verdict |
|---|---|---|
| 65% | ~70.6 | Fallback ≥70 met; primary ≥75 missed |
| 70% | ~72.3 | Fallback met; primary still short |
| 80% | ~75.7 | **Primary ≥75 met** |
| 90% | ~79.0 | Primary met with margin |

Diagnostic projected T3 88-93% (Lever 1 alone). Sanity bound: T3 ≥ 80% clears primary; T3 ≥ 65% clears fallback. If T3 lands 60-65%, we ship at fallback. If T3 lands < 60%, escalate (lever didn't work as expected).

**Caveat**: this assumes T1+T2 don't regress. If v1.11's clustered-identity demotion fires inappropriately on T1 (which has clean emails), T1 could regress. Tier 4 v1.10-compat fixture is the guardrail.

## Non-goals (this spec)

- **Fellegi-Sunter promotion (Lever 2 of original v1.11 brainstorm).** T3 diagnostic showed zero leverage — the discriminating fields aren't even in the matchkey, so FS m/u-training can't help. Queue for v1.12 if measurement on other datasets surfaces a use case.
- **Compound logical matchkeys (Lever 3).** Diagnostic showed equivalent power to negative evidence on T3 specifically but harder to generalize. v1.12 candidate.
- **Block-conditional thresholds (Lever 4).** Diagnostic showed zero impact on T3 — block size distribution is uniform-small (median 1, max 9). No evidence v1.11 needs it.
- **Negative evidence on `exact` matchkeys.** Only weighted matchkeys can host `negative_evidence` in v1.11. The clustered-identity guard's job is to demote `exact → weighted` before NE applies. Subtracting from a binary 1.0 was considered (Path Y in brainstorm) and rejected as semantic confusion.
- **Adaptive penalty/threshold tuning.** Fixed defaults `(threshold=0.4, penalty=0.3)` for v1.11. Adaptive tuning is a v1.12 candidate if the defaults under-perform.
- **TypeScript port parity.** Python-first; TS port catches up in a separate workstream tracked at the suite level.

## Decision summary

| Decision | Choice | Why |
|---|---|---|
| Acceptance target | composite ≥75 primary / ≥70 fallback | Matches v1.10's missed primary; Lever 1 alone projected to clear ≥75 with margin per diagnostic |
| Schema strategy | Hybrid: extend `MatchkeyConfig` with optional `negative_evidence` list; clustered-guard logic stays in rule + indicator (no new matchkey shape) | Surgical change; v1.10 cache compat preserved via default-None field |
| Auto-config promotion | All opt-in via rules + 1 eager pre-iteration step | Matches v1.9/v1.10 pattern; observable; opt-in is the recovery path from v1.9's behavior-change regression |
| Negative-evidence schema | Separate `negative_evidence: list[NegativeEvidenceField]` parallel to `fields` (positive) | Cleaner conceptual separation than per-field overload; T3's case is "phone+address as pure negative evidence" not currently in `fields` |
| Promotion mechanic | Identity-prior driven (eager): scan unused identity-prior columns at config-build time | Reuses v1.10's `column_priors`; deterministic; covers T3 deterministically |
| Clustered-identity detection | Full-data within-group divergence (lazy, 8s budget) | T3 collision_rate ≈ 0.6; clean datasets ≈ 0.0 — clear separation; lazy keeps green-path benchmarks fast |
| In-CI regression coverage | Two synthetic fixtures (collision + clean) + Tier 4 integration tests | v1.10's lesson: test both directions of regression; collision fixture verifies fix; clean fixture verifies no over-apply |

## Architecture

Two new layers and one new schema field. No existing layer's contract changes (additive only).

### 1. `MatchkeyConfig.negative_evidence: list[NegativeEvidenceField] | None`

New optional Pydantic field on existing weighted `MatchkeyConfig`. List of `NegativeEvidenceField(field, scorer, threshold, penalty)` entries. Scoring code applies these AFTER the positive `fields` weighted sum:

```python
score_positive = sum(f.weight * scorer(pair, f.field, f.scorer) for f in matchkey.fields)
score_negative = sum(
    ne.penalty for ne in (matchkey.negative_evidence or [])
    if scorer(pair, ne.field, ne.scorer) < ne.threshold
)
final_score = max(0.0, score_positive - score_negative)
```

`negative_evidence` defaults to None for v1.10-saved cache entries; backward compat preserved.

### 2. New rule `rule_promote_negative_evidence` (eager)

Module-level rule in `core/autoconfig_negative_evidence.py`. Fires once at config-build time (BEFORE iteration loop), called from `auto_configure_df`. For each weighted matchkey in `config_v0`: scan all df columns where `column_priors[col].identity_score >= 0.7` AND `cardinality_ratio >= 0.5` AND col NOT in matchkey's positive `fields` list AND col NOT in blocking keys. Promote each as `NegativeEvidenceField(field=col, transforms=transforms, scorer=scorer, threshold=0.4, penalty=0.3)` where `(transforms, scorer) = _pick_scorer_for_column(col, col_type)`. Idempotent: skip if already in `negative_evidence`.

This is an *eager* rule — it modifies `config_v0` directly, not via the iteration loop's `propose()` chain. Different shape from v1.10's post-iteration rules (which fire on profile signals). Logged at INFO with structured message per promoted column.

T3 application: matchkey `fuzzy_match` gains `negative_evidence=[NE(phone, transforms=["digits_only"], scorer="exact", threshold=0.5, penalty=0.3), NE(address, transforms=[], scorer="token_sort", threshold=0.4, penalty=0.4)]`.

### 3. New indicator + rule for clustered-identity guard

**Indicator** (`core/indicators.py`, lazy via `IndicatorContext`):
```python
def compute_identity_collision_signal(
    df: pl.DataFrame, identity_col: str, witness_cols: list[str],
) -> CollisionSignal:
```
1. Group rows by `identity_col` value.
2. For each multi-record group (size ≥ 2), compute max pairwise divergence on `witness_cols` (max(`1 - jaro_winkler(a, b)`) across pairs across witness_cols).
3. Returns `CollisionSignal(rate, witness_used)` where `rate` = fraction of multi-record groups with max-divergence > 0.5; `witness_used` = name of the witness col that drove the highest divergences.

**Divergence threshold rationale (0.5)**: T3's adversarial collision pairs have within-group witness divergence > 0.7 (different addresses, different phones). Legitimate-duplicate cases (e.g., apartment moves with slightly-different addresses) have divergence 0.2-0.4. The 0.5 cutoff cleanly separates them with margin on both sides.

Budget: 8s. On exhaustion, returns `CollisionSignal(rate=0.0, witness_used="")` sentinel.

**Memo key**: `("identity_collision_signal", identity_col, tuple(sorted(witness_cols)))`. Canonicalizing witness_cols ordering ensures the same call from different rules (with witness_cols in different order) hits the same cache entry.

**Rule** (`core/autoconfig_rules.py`, post-iteration):
```python
def rule_demote_clustered_identity(profile, current, history, ctx=None):
```
Fires when:
- `ctx is not None`
- For some col currently used in an `exact` matchkey: `column_priors[col].identity_score >= 0.85`
- `cardinality_ratio in [0.5, 0.95]` (near-unique but not unique)
- `ctx.identity_collision_signal(col, [other identity cols]).rate > 0.2`

Action: `_demote_exact_to_weighted_fuzzy(current, col, witness_used)` — surgically:
1. Remove the standalone exact matchkey on `col` from `matchkeys`.
2. Add `col` as a low-weight (`weight=0.3`) participant in the existing weighted matchkey.
3. Add `col` to blocking keys if not already present.
Returns `(new_config, PolicyDecision)` per the rule contract.

Slots at position 14 in `DEFAULT_RULES` (after v1.10's 13). Total `DEFAULT_RULES` becomes 14.

### Data flow

**Pre-iteration (one-time, eager):**
```
auto_configure_df(df)
  -> config_v0_raw = _legacy_auto_configure_v0(df)
  -> column_priors = compute_column_priors(df)              # v1.10
  -> sparsity = estimate_sparse_match_signal(df, ...)       # v1.10
  -> config_v0 = promote_negative_evidence(config_v0_raw, df, column_priors)  # NEW v1.11
  -> ctx = IndicatorContext(df, column_priors, sparsity)
  -> profile_v0_sample = _run_pipeline_sample(df, config_v0, ctx)
  -> controller.run(df, config_v0, ctx)
```

**Memory cache lookup ordering**: `auto_configure_df` may short-circuit on `autoconfig_memory.lookup_best(signature)` cache hit. v1.11 invariant: **`promote_negative_evidence` runs before any cache check** so the eager promotion always applies. The cache stores the *committed* config (post-iteration, post-finalize), which already has `negative_evidence` populated from a prior v1.11 run. v1.10-saved cache entries (no `negative_evidence` field) deserialize cleanly with `negative_evidence=None`; the next run on the same data shape *will* re-promote on the v0 raw config and overwrite the cache hit's behavior. Trade-off: a v1.10-saved cache hit doesn't get NE for the cached run, but the iteration that follows builds it correctly. Acceptable: cache hits are an optimization, not a correctness contract.

**Per-iteration (lazy, on rule demand):** v1.10's 13 rules unchanged + new `rule_demote_clustered_identity` at position 14:
```
iteration N:
  policy.propose(profile_n, config_n, history, ctx)
    -> ... v1.10's 13 rules ...
    -> rule_demote_clustered_identity.check(...)
        -> for each exact matchkey col where identity_score >= 0.85
                                       and cardinality_ratio in [0.5, 0.95]:
             signal = ctx.identity_collision_signal(col, witness_cols)
             if signal.rate > 0.2:
                 return _demote_exact_to_weighted_fuzzy(...)
```

**Scoring path (per-pair):** existing flow + negative-evidence subtraction in `_apply_negative_evidence`.

## Components

| Component | Type | LOC | Description |
|---|---|---|---|
| `NegativeEvidenceField` | New Pydantic model in `config/schemas.py` | ~30 | `field: str`, `transforms: list[str] = []`, `scorer: str` (validated against `VALID_SCORERS`), `threshold: float`, `penalty: float` (validators 0≤val≤1). Mirrors `MatchkeyField`'s shape so transforms can normalize before scoring (e.g., `digits_only` transform + `exact` scorer for phone). |
| `MatchkeyConfig.negative_evidence` | Field add | ~5 | `list[NegativeEvidenceField] \| None = None` |
| `_apply_negative_evidence(matchkey, pair) -> float` | New helper in `core/scorer.py` | ~50 | Computes negative penalty; called from existing scoring loop |
| `core/autoconfig_negative_evidence.py` | NEW module | ~120 | `promote_negative_evidence(config, df, column_priors) -> GoldenMatchConfig` (pure function) + `_pick_scorer_for_column` helper |
| `compute_identity_collision_signal` | New in `core/indicators.py` | ~80 | groupby + within-group divergence + budget enforcement |
| `IndicatorContext.identity_collision_signal` method | Extension | ~20 | Memoized lazy access (mirrors v1.10's `full_pop_matchkey_hits`) |
| `CollisionSignal` dataclass | New in `core/complexity_profile.py` | ~15 | `rate: float`, `witness_used: str` |
| `rule_demote_clustered_identity` | New in `core/autoconfig_rules.py` | ~80 | Post-iteration rule; calls `_demote_exact_to_weighted_fuzzy` |
| `_demote_exact_to_weighted_fuzzy` | New helper in `autoconfig_rules.py` | ~50 | Surgical config rewrite: remove exact matchkey, add as fuzzy participant, add to blocking |
| `auto_configure_df` integration | Edit in `core/autoconfig.py` | ~15 | Call `promote_negative_evidence` post-v0-build |
| `DEFAULT_RULES` ordering | Edit in `core/autoconfig_rules.py` | ~5 | Append `rule_demote_clustered_identity` at position 14 |
| Tests | New + modified | ~350 | 7 tiers below |

**Total: ~500 LOC code + ~350 LOC tests = ~850 LOC.**

## Error handling

| Failure mode | Where | Behavior |
|---|---|---|
| Scorer not registered for `NegativeEvidenceField.scorer` | `_apply_negative_evidence` | Skip NE entry with WARNING; scoring continues without crash |
| `NegativeEvidenceField.field` not in df columns | `_apply_negative_evidence` | Skip with WARNING; defensive |
| Scorer call raises | `_apply_negative_evidence` | Caught at boundary; WARNING + traceback hash; skip; continue |
| Pydantic validators reject (threshold > 1, etc.) | Config build time | Raises `ValidationError`; visible to user; defaults always within range |
| `column_priors` empty (eager indicator timed out) | `promote_negative_evidence` | Return config unchanged; INFO log |
| `df` is empty | `promote_negative_evidence` | Short-circuit: return config unchanged |
| Multiple weighted matchkeys | `promote_negative_evidence` | Apply to ALL; idempotent dedup by field name within each |
| Scorer-pick returns None for unknown col_type | `_pick_scorer_for_column` | Default to `"ensemble"`; never returns None |
| Collision-signal budget exceeded (8s) | `compute_identity_collision_signal` | Returns `CollisionSignal(rate=0.0, witness_used="")` sentinel; rule does not fire; INFO log |
| All witness_cols have identity_score < 0.7 | Same | Returns `CollisionSignal(rate=0.0, witness_used="<no_witness>")` sentinel; rule does not fire |
| Demotion would empty matchkey list | `_demote_exact_to_weighted_fuzzy` | Skip demotion; return `(cfg, "")`; rule's `if new_cfg != current` check sees no change |
| Demotion adds to blocking when col already present | `_demote_exact_to_weighted_fuzzy` | Skip add (mirrors v1.10's `_with_multi_pass` dedup) |

**Backward compat:**
- v1.10 cache entries deserialize cleanly: `negative_evidence` defaults to None
- TypeScript port parity tracked separately (Python-first ship)
- Custom `RefitPolicy` implementations: no signature change beyond v1.10's `ctx` kwarg
- Existing YAML configs: extra `negative_evidence:` key is opt-in; absence = no behavior change

**Three concrete bugs prevented:**
- Promotion on green-path benchmarks (DBLP-ACM): gates `col not in matchkey.fields` and `col not in blocking` filter columns already participating positively.
- Collision-signal false positive on legitimate duplicates (e.g., apartment moves with slightly-different addresses): rule's witness check requires divergence > 0.5 (very strong disagreement); legitimate-move case has divergence 0.2-0.4, doesn't trip.
- Negative-evidence on a blocking field: would waste compute (candidates always agree on blocking field). `promote_negative_evidence` skips columns currently in blocking.

**Thread-safety:** unchanged from v1.10. New components are per-`auto_configure_df`-call.

## Testing

### Tier 1 — Indicator + helper unit tests (~100 LOC)

`tests/test_indicators.py` extension:
- `compute_identity_collision_signal` returns rate ≈ 0 on clean fixture
- Returns rate > 0.5 on adversarial collision fixture
- Budget timeout returns sentinel
- Returns sentinel when no witness cols pass identity_score gate

`tests/test_autoconfig_negative_evidence.py` (NEW, ~70 LOC):
- `_pick_scorer_for_column` returns `(transforms, scorer)` tuple: email → `([], "token_sort")`; phone → `(["digits_only"], "exact")`; address → `([], "token_sort")`; unknown → `([], "ensemble")`. Returned scorer is always in `VALID_SCORERS`.
- `promote_negative_evidence`: enriches matchkey with phone+address on T3-shaped df
- Idempotent on second call
- Skips columns already in matchkey.fields
- Skips columns in blocking
- Returns unchanged config on empty df

### Tier 2 — Rule fire/no-fire (~100 LOC)

`tests/test_autoconfig_rules.py` extension:
- `rule_demote_clustered_identity` fires on synthetic collision fixture (cardinality 0.69, identity_score 0.95, collision_rate 0.6)
- Doesn't fire when cardinality_ratio > 0.95 (genuinely unique)
- Doesn't fire when identity_score < 0.85
- Doesn't fire when collision_rate ≤ 0.2
- Doesn't fire when ctx is None
- Doesn't fire when no exact matchkey on the candidate col
- `_demote_exact_to_weighted_fuzzy`: preserves matchkey list ordering; adds field as weight=0.3 to weighted matchkey; adds to blocking; defensive on edge cases

### Tier 3 — Schema + scoring integration (~80 LOC)

`tests/test_negative_evidence_scoring.py` (NEW):
- `NegativeEvidenceField` validators reject out-of-range
- `MatchkeyConfig.model_validate` accepts `negative_evidence=None`
- `_apply_negative_evidence` returns 0.0 when None or empty
- Pair scoring: positive=0.9, NE field disagrees (sim < threshold) → final = 0.6 (below 0.8 threshold = no match)
- Pair scoring: positive=0.9, NE field agrees → final = 0.9 (match)
- Scorer-not-registered: skips NE entry with WARNING
- Field not in df: defensive skip

### Tier 4 — Synthetic T3-style + v1.10-compat integration (~150 LOC)

Two committed fixtures:

`tests/fixtures/autoconfig/t3_synthetic.csv` — 200 rows: 50 true duplicate pairs + 50 collision pairs (different people with shared name+email but divergent phone+address+city) + 100 unique singletons.

`tests/fixtures/autoconfig/t3_clean_compat.csv` — 200 rows, no collision pattern (each email used at most once); mirrors DBLP-ACM-class workload.

`tests/test_dqbench_t3_recovery.py` (NEW):
- `test_t3_synthetic_recovers_precision`: runs `auto_configure_df` + `dedupe_df`. Asserts: NE was promoted, exact_email was demoted, cluster count ∈ [125, 175], precision ≥ 0.80, recall ≥ 0.90
- `test_t3_clean_compat_no_lever_overapply`: same pipeline on clean fixture. Asserts: clustered-identity rule does NOT fire; precision unchanged from v1.10 baseline within 1pp

### Tier 5 — Cache backward-compat (~50 LOC)

`tests/test_autoconfig_memory_v110_compat.py` (NEW). Commits a NEW fixture `tests/fixtures/autoconfig/v1_10_memory_snapshot.json` — a `MatchkeyConfig` JSON serialized using v1.10's schema (i.e., no `negative_evidence` key). Generated by a `_gen_v1_10_snapshot.py` script (mirrors v1.9's pattern in `tests/fixtures/autoconfig/_gen_v1_9_snapshot.py`).

Tests:
- v1.10-vintage `v1_10_memory_snapshot.json` (no `negative_evidence` key) loads cleanly into v1.11's `MatchkeyConfig.model_validate`
- A v1.11 entry with `negative_evidence` populated round-trips through Pydantic
- `MatchkeyConfig` constructed without `negative_evidence` arg defaults to None
- The pre-existing `v1_9_memory_snapshot.json` (committed in v1.10) still loads cleanly into v1.11 — chain compat: v1.9 → v1.10 → v1.11 deserialization works through both schema additions

### Tier 6 — Property tests (~40 LOC)

`tests/test_autoconfig_properties.py` extension:
- All v1.10 properties hold (no regression on YELLOW-reaching paths)
- `_apply_negative_evidence` is monotonic in penalty (higher penalty → ≤ score)
- `compute_identity_collision_signal` is deterministic given fixed df hash
- `promote_negative_evidence` is idempotent

### Tier 7 — Performance budget (~30 LOC)

`tests/test_indicators_budget.py` extension:
- `compute_identity_collision_signal` on 50K-row synthetic df completes within 8s
- Negative-evidence scoring overhead on 50K candidate pairs: < 2s

## Acceptance criteria

1. **All 7 test tiers pass.** New test count ~+50 (1907 → ~1957).
2. **DBLP-ACM/Febrl3/NCVR hold.** F1 ≥ v1.10 baselines (0.9641 / 0.9443 / 0.9719); committed health unchanged.
3. **DQbench composite ≥ 75 (no LLM)** — primary target.
4. **DQbench composite ≥ 70 (no LLM)** — fallback contract.
5. **DQbench T3 F1 ≥ 70%** — headline lever target. If T3 lands below 60%, escalate (lever didn't work as expected).
6. **Wall-clock budget**: `auto_configure_df(df)` on 50K-row dataset within 100s.
7. **Cache compat**: v1.10-saved entry loads cleanly into v1.11.
8. **PR description**: per-tier DQbench breakdown + T3 before/after P/R/F1 explicit. Indicator-attribution sweep optional, required only if shipping at composite ≥ 75.

## Risks

- **Estimate uncertainty**: diagnostic projected +35-40pp T3 lift from a 30-pair sample. If TP/FP distributions in the full T3 dataset differ, lift could be smaller. Mitigation: Tier 4 synthetic guard catches mechanism; Phase 7 measurement on real T3 catches sample-vs-population drift.
- **Promotion over-applies on unmeasured datasets**: high-cardinality non-identity columns (e.g., timestamps with identity_score < 0.7) shouldn't be promoted, but edge cases exist. Mitigation: identity_score gate (≥ 0.7) + col-not-in-blocking guard + Tier 4 v1.10-compat fixture in CI.
- **Synthetic T3 fixture passes while real T3 fails**: same risk as v1.10. Phase 7 real-DQbench measurement is the only complete check.
- **Collision-signal false positive on legitimate duplicates**: addressed by divergence > 0.5 threshold (very strong disagreement only).
- **Idempotency edge case**: `promote_negative_evidence` runs eagerly each call. Cached configs from `autoconfig_memory` may be re-loaded. Tier 1 idempotency test guards.

## Implementation sequence (informational — full TDD plan in `writing-plans` output)

1. Schema: `NegativeEvidenceField` dataclass + `MatchkeyConfig.negative_evidence` field + Tier 5 cache compat tests
2. Scoring: `_apply_negative_evidence` helper + Tier 3 tests
3. Eager rule: `core/autoconfig_negative_evidence.py` + `_pick_scorer_for_column` + Tier 1 unit tests + `auto_configure_df` integration
4. Indicator: `compute_identity_collision_signal` in `core/indicators.py` + `IndicatorContext.identity_collision_signal` method + Tier 1 unit tests
5. Demote rule: `rule_demote_clustered_identity` + `_demote_exact_to_weighted_fuzzy` helper + Tier 2 tests + `DEFAULT_RULES` update
6. Tier 4 synthetic + clean fixtures + integration tests
7. Tier 6 property tests + Tier 7 performance budget tests
8. Re-measure DBLP-ACM/Febrl3/NCVR; re-measure DQbench no-LLM; iterate on rule firing conditions until ≥75 or accept ≥70 fallback
9. Update CLAUDE.md, CHANGELOG, version bump to 1.11.0
10. Open PR; gh auth dance; release; PyPI publish
