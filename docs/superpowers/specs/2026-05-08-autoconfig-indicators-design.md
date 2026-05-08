# Auto-Config Indicators (v1.10)

**Status:** Design (approved by user 2026-05-08; spec-review pass pending)
**Author:** brainstorm session, Claude + bsevern
**Scope:** new module `core/indicators.py`; modifications to `core/autoconfig_controller.py`, `core/autoconfig_policy.py`, `core/autoconfig_rules.py`, `core/complexity_profile.py`; new + modified tests
**Related:**
- v1.9 spec: `2026-05-08-autoconfig-best-effort-commit-design.md` (especially the §Amendment section with the DQbench T1 root-cause analysis)
- v1.9 release: PR #118, shipped 2026-05-08
- v1.10 trigger: spec premise of v1.9 was wrong — committing best-effort RED on T1 was worse than v0 because the controller's complexity indicators couldn't tell "blocking is wrong" from "blocking is right but sample lacks visible matches"

## Problem

v1.9 wrapped with DBLP-ACM/Febrl3/NCVR at v1.8 parity (YELLOW commits), DQbench composite still at 62.87. The diagnostic from `.profile_tmp/v0_vs_red_t1_findings.txt` traced the gap:

- v0 picks `email` blocking + threshold 0.80 + exact matchkeys for DQbench T1 (the right answer).
- The 1000-row sample contains ~50 true duplicate pairs, most with corrupted emails (e.g. `brian.nelson@gmail.com` vs `BRIAN.NELSON@gmail.com`). Under exact email blocking, `mass_above_threshold = 0.0`.
- `rule_no_matches` fires (iter 0) → drops threshold to 0.50; `rule_blocking_key_swap` fires (iter 1) → swaps blocking to `first_token(first_name)`. By iter 2 the controller has abandoned email entirely for a coarse 17-records-per-block key.

The proximate cause: a single signal (`mass_above_threshold = 0.0`) ambiguously means either "blocking key is wrong" or "blocking key is right but sample has no visible matches." Rules can only see one signal and can't disambiguate. v1.9's virtual-v0 + precision floor prevents catastrophic regression but doesn't add information — the controller still can't tell whether to refit or to back off.

v1.10 adds five indicators that disambiguate these cases and let the controller make better refit decisions.

## Goals

1. **DQbench composite ≥ 70 no-LLM** (primary; v1.9 was 62.87; halfway between current and with-LLM ceiling 95.30).
2. **DQbench composite ≥ 65 no-LLM** (fallback contract; if (1) fails, branch can still ship at parity gain).
3. **DBLP-ACM, Febrl3, NCVR hold at v1.8/v1.9 baselines** (no benchmark regression).
4. **Wall-clock budget**: `auto_configure_df(df)` on a 50K-row dataset completes within **90s** typical-case, with the understanding that struggling-iteration paths (where multiple expensive indicators fire) may approach this ceiling. Today's baseline is ~30s; indicator overhead is ~10s eager + up to ~50s lazy when both expensive indicators fire serially. A `GOLDENMATCH_AUTOCONFIG_INDICATOR_BUDGET=fast` env var disables the two slowest indicators (cross-blocking, full-pop) for users who prefer the v1.9 wall-clock at the cost of v1.10 gains.

### Composite math (back-of-envelope)

DQbench's composite weighting is opaque without harness inspection, but we can bound the contribution. Current: T1=89.3% v0 / 2.0% unguarded, T2=58.7% / 0.7%, T3=53.8% (unchanged). v1.9 ships at parity (composite 62.87) using virtual-v0, so all three tiers are at the "v0" numbers. Targets:

- **For ≥65 (fallback)**: T1 recovery from 89.3% → 92%+ via the corruption-normalize rule alone (catches `Brian/BRIAN` corrupted-email pairs that v0 misses). T1 contributes the largest single weight. Achievable from indicator 4 (corruption-normalize) alone.
- **For ≥70 (primary)**: T1 ≥ 92% AND T2 ≥ 65% (from cross-blocking + sparse-expand). T2 is the harder tier — its 58.7% baseline indicates blocking is good but threshold is too tight on a sparse-match dataset.
- **T3 ceiling**: 53.8% is roughly the indicator-stack's ceiling for T3 because none of the 5 indicators target T3's failure mode (which appears to be insufficient discriminative scoring on similar-but-not-duplicate records, not blocking or threshold). Higher T3 needs LLM scorer or richer matchkey design — both v1.11+.

If the back-of-envelope is wrong (DQbench weights heavier on T3 than expected), the primary target may be unreachable even with all 5 indicators. The fallback contract preserves shipping value.

## Non-goals (this spec)

- **Adaptive budget tuning.** Per-indicator wall-clock budgets are fixed constants tuned for the v1.10 measurement workload. Tune later if needed.
- **Indicator-driven LLM scorer policy.** Indicators feed the heuristic policy only; LLM policy is unchanged in v1.10.
- **Cross-run learning of indicator thresholds.** Memory cache stores indicator values but doesn't tune from them.
- **New public API.** No new `auto_configure_df` kwargs, no new env vars. The change is observable via existing `controller_profile` and `controller_history` fields on `PostflightReport`, plus new fields on `DataProfile` and `ComplexityProfile`.

## Decision summary

| Decision | Choice | Why |
|---|---|---|
| Scope | All 5 indicators | Aggressive — match the ≥70 target. **T3 (currently 53.8%) is unaffected by these indicators**; gain comes from T1 + T2 recovery only. If T2 doesn't move enough, fallback contract ≥65 is met by T1 recovery alone |
| Strategy | Hybrid: column-level priors integrate into existing rules; dynamic measurements get new dedicated rules | Negative-signal indicators (don't abandon) belong on existing rules; new-action indicators need new rules |
| Data model | Hybrid: `DataProfile.column_priors` + new `IndicatorsProfile` sub-profile | Column properties belong with column metadata; controller-level measurements belong in their own bag |
| Compute strategy | Tiered: cheap eager, expensive lazy via `IndicatorContext` | DBLP-ACM-class easy datasets shouldn't pay for indicators that exist to rescue T1/T2-class struggles |
| Identity-prior + full-pop integration | Modified rules try alternatives internally before swapping (action-list logic kept inside rules; no public type added) | Vetoing abandonment alone is insufficient — need to introduce normalize-then-exact alternatives without changing the public rule/policy contract |
| Acceptance | DQbench ≥70 (primary) / ≥65 (fallback) | ≥70 sharpens design choices; fallback prevents v1.9-style "ship parity and hope" |

## Architecture

Three layers added to the controller. No existing layer's contract changes (only `PolicyDecision` extends in a backward-compatible way).

### 1. Indicator computation layer (`core/indicators.py` — new module)

Pure functions, each with a bounded wall-clock budget. Return `None` (or unit-typed sentinel) on budget exhaustion or exception. No controller state — easy to unit-test.

| Function | Purpose | Budget | Cost class |
|---|---|---|---|
| `compute_column_priors(df) -> dict[str, ColumnPrior]` | Column-type-driven identity score + edit-distance corruption score | 5s | Cheap (eager) |
| `estimate_sparse_match_signal(df, sample_size=1000) -> SparsityVerdict` | Counts exact-matchkey hits in sample; flags if `n_exact_hits < 50` | 2s | Cheap (eager) |
| `estimate_full_pop_hits(df, blocking_key, matchkey) -> int \| None` | Count exact matches on full-population scan | 15s | Expensive (lazy) |
| `compute_cross_blocking_overlap(df, key_a, key_b, threshold) -> float \| None` | Fraction of candidates from key A whose pair-mate is also in a key-B candidate, scoring above threshold | 20s | Expensive (lazy) |
| `compute_corruption_score(df, col) -> float` | Edit-distance variance within the column on a 1000-row sample | 3s | Cheap (eager, but called per-column on demand) |

### 2. `IndicatorContext` object (`core/autoconfig_controller.py`)

Holds: the dataframe, the controller's wall-clock start, a memoization cache keyed by `(function_name, args_tuple)`. Tracks already-fired flags for one-shot rules (e.g. `ExpandSample`). Records timeouts and exceptions for postmortem via `_LAST_CONTROLLER_RUN.errors`.

API exposed to rules:
```python
class IndicatorContext:
    def full_pop_matchkey_hits(self, blocking_col: str, matchkey: MatchkeyConfig) -> int | None: ...
    def cross_blocking_overlap(self, key_a: BlockingKeyConfig, key_b: BlockingKeyConfig, threshold: float) -> float | None: ...
    @property
    def column_priors(self) -> dict[str, ColumnPrior]: ...
    @property
    def sparsity_verdict(self) -> SparsityVerdict: ...
    def has_fired(self, rule_name: str) -> bool: ...
    def mark_fired(self, rule_name: str) -> None: ...
```

Passed as 4th positional arg to `RefitPolicy.propose(profile, config, history, ctx)`. Existing rules ignore it (kwarg `ctx=None` default for backward compat). New rules and modified existing rules read from it.

### 3. Rule signature changes (`core/autoconfig_policy.py`, `core/autoconfig_rules.py`)

**`PolicyDecision` is unchanged** — its current shape (`rule_name: str`, `rationale: str`, `config_diff: dict[str, Any]`) is preserved. The "action-list" pattern is implemented *inside* each new rule's body, not exposed as a public type. Each rule still returns `tuple[GoldenMatchConfig, PolicyDecision] | None` like today.

**`RefitPolicy.propose()` signature gains an optional `ctx` kwarg:**

```python
class RefitPolicy(Protocol):
    def propose(
        self,
        profile: ComplexityProfile,
        current: Any,
        history: RunHistory,
        ctx: IndicatorContext | None = None,   # NEW (kwarg, default None)
    ) -> Any | None: ...
```

`HeuristicRefitPolicy.propose` accepts and forwards `ctx` to each rule. Rules whose signature is widened to accept `ctx` receive it; rules unchanged ignore it. The `Rule` type alias becomes:

```python
Rule = Callable[
    [ComplexityProfile, Any, RunHistory, IndicatorContext | None],
    "tuple[Any, PolicyDecision] | None",
]
```

Existing rules' signatures are widened to add `ctx: IndicatorContext | None = None` as a final kwarg-defaulted parameter. Their bodies don't reference `ctx`. New + modified rules read from it.

**`LLMRefitPolicy.propose` is updated to accept and forward `ctx` to its wrapped base policy.** This is the only real signature change with backward-compat impact: any caller passing `LLMRefitPolicy.propose(profile, current, history)` positionally still works because `ctx` is kwarg-defaulted. Custom user implementations of `RefitPolicy` that don't accept `ctx` will receive a `TypeError` if called by the v1.10 controller. **Mitigation**: the controller calls `policy.propose(profile, current, history, ctx=ctx)` only if the policy's signature accepts the param (introspected via `inspect.signature`); else falls back to the 3-arg call. Custom policies see no breakage.

**Rule-internal action-list logic.** New rules implement multi-alternative logic internally: they construct a list of candidate `(new_config, rationale)` pairs in preference order, then return the first whose `new_config` differs from `current`. If none differs, return None (treated as rule-not-fired by the policy).

Pseudocode for `rule_no_matches` (modified):

```python
def rule_no_matches(profile, current, history, ctx=None):
    if profile.scoring.mass_above_threshold > 0:
        return None
    blocking_col = current.blocking.keys[0].fields[0]
    priors = ctx.column_priors if ctx else {}
    cp = priors.get(blocking_col, ColumnPrior(0.0, 0.0))

    candidates = []
    if cp.identity_score >= 0.7:
        # Identity column: try gentler alternatives before swap
        candidates.append(_with_lower_threshold(current, 0.05))
        candidates.append(_with_normalize_standardization(current, blocking_col))
        candidates.append(_with_multi_pass(current, _orthogonal_key(current)))
    elif ctx and ctx.sparsity_verdict.is_sparse:
        candidates.append(_with_expanded_sample(current, factor=2.0))
    else:
        candidates.append(_with_lower_threshold(current, 0.05))

    for new_config, rationale in candidates:
        if new_config != current:
            return (new_config, PolicyDecision(
                rule_name="rule_no_matches",
                rationale=rationale,
                config_diff=_diff(current, new_config),
            ))
    return None
```

This pattern keeps the rule contract simple (still returns one `(config, decision)`); the "action list" is purely internal scaffolding.

### Data flow

**Pre-iteration (one-time, eager):**
```
auto_configure_df(df)
  -> _legacy_auto_configure_v0(df) -> config_v0
  -> compute_column_priors(df) -> dict[col, ColumnPrior]
  -> estimate_sparse_match_signal(df, 1000) -> SparsityVerdict
  -> ctx = IndicatorContext(df, column_priors, sparsity_verdict)
  -> profile_v0_sample = _run_pipeline_sample(df, config_v0, ctx)
  -> controller.run(df, config_v0, ctx)
```

**Per-iteration (lazy, on rule demand):**

Each rule in the existing 10-rule + 3-new-rule ordered list now optionally consults `ctx` and returns an action-list. Existing rules without indicator integration return `[their_existing_action]` (a 1-element list). Modified existing rules and new rules return multi-action lists. The controller applies actions sequentially until one succeeds in changing the config; if all fail, treats as POLICY_NO_PROGRESS.

Detailed per-rule firing conditions are in §Components below.

## Components

| Component | Type | LOC | Description |
|---|---|---|---|
| `core/indicators.py` | New module | ~250 | 5 indicator functions + `ColumnPrior`, `IndicatorsProfile`, `SparsityVerdict` dataclasses |
| `IndicatorContext` | New class in `autoconfig_controller.py` | ~50 | df + memoization cache; `__call__`-style API for rules |
| Rule helpers in `autoconfig_rules.py` | New module-private functions | ~120 | `_with_lower_threshold`, `_with_normalize_standardization`, `_with_multi_pass`, `_with_expanded_sample`, `_orthogonal_key`, `_diff`. Used by modified + new rules to build candidate configs |
| `RefitPolicy.propose` signature widening | Protocol extension | ~30 | Optional `ctx: IndicatorContext \| None = None` kwarg; `HeuristicRefitPolicy` and `LLMRefitPolicy` both forward; controller introspects signature for custom-policy backward compat |
| `DataProfile.column_priors: dict[str, ColumnPrior] \| None` | Field add | ~5 | Default-None for cache compat |
| `ComplexityProfile.indicators: IndicatorsProfile \| None` | Field add | ~5 | Default-None |
| `rule_no_matches` modification | Existing rule | ~40 | Returns `[LowerThreshold, AddNormalize, AddMultiPass, SwapBlockingKey]` ordered by indicator-driven preference. Sparsity-driven `[ExpandSample]` short-circuit when sparse |
| `rule_blocking_key_swap` modification | Existing rule | ~30 | Reads identity_score + full_pop_hits; vetoes swap when both signal v0-key-good |
| `rule_cross_blocking_disagreement` | New rule | ~80 | Fires when iter-N RED + cross_blocking_overlap < 0.3 + mass_above < 0.1; proposes `[AddMultiPass(orthogonal_key)]` |
| `rule_corruption_normalize` | New rule | ~70 | Fires when blocking column corruption_score > 0.4 + identity_prior > 0.6; proposes `[AddNormalizeStandardization(col)]` |
| `rule_sparse_match_expand` | New rule | ~50 | Fires when sample_is_sparse + iteration <= 1 + not yet fired; proposes `[ExpandSample(2.0)]` |
| Controller wiring | Edits in `autoconfig_controller.py` | ~60 | Pre-loop eager indicator compute; pass `IndicatorContext` to policy; apply first applicable action from list |
| Tests | New + modifications | ~400 | Per-indicator unit tests; per-rule fire/no-fire tests; T1 integration test; backward-compat tests |

**Total: ~1150 LOC code + ~400 LOC tests = ~1550 LOC.** v1.10 is a real feature, not a refactor.

### Rule ordering (post-v1.10, 13 ordered rules)

The existing v1.8 rule order is preserved; new rules slot in at positions where their action specificity matches:

1. `rule_blocking_field_null_heavy`
2. `rule_blocking_singleton_trap`
3. `rule_blocking_key_swap` (modified — reads indicators, vetoes when v0-key-good)
4. `rule_blocking_too_coarse`
5. `rule_uniform_heavy_blocking`
6. **NEW: `rule_corruption_normalize`** (between blocking and scoring rules; fixes blocking by normalizing, not swapping)
7. `rule_unimodal_scoring`
8. `rule_low_reduction_ratio`
9. **NEW: `rule_cross_blocking_disagreement`** (after blocking diagnostics, before recall-gap)
10. `rule_low_transitivity`
11. `rule_no_matches` (modified — reads indicators, returns action-list)
12. `rule_recall_gap_suspected`
13. **NEW: `rule_sparse_match_expand`** (last — only fires if no other rule has, and only on early iterations)

Per-rule firing conditions (concrete):

**`rule_no_matches` (modified):**
- Today: fires when `mass_above_threshold == 0.0`; returns `LowerThreshold(0.05)`
- v1.10:
  - If `priors[blocking_col].identity_score >= 0.7`:
    return `[LowerThreshold(0.05), AddNormalizeStandardization(blocking_col), AddMultiPass(orthogonal_key)]`
  - Else if `ctx.sparsity_verdict.is_sparse`:
    return `[ExpandSample(2.0)]`
  - Else: return `[LowerThreshold(0.05)]` (today's behavior)

**`rule_blocking_key_swap` (modified):**
- Today: fires when `mass_above_threshold == 0.0` AND prior decision exists; swaps key
- v1.10:
  - If `priors[blocking_col].identity_score >= 0.8` AND `ctx.full_pop_matchkey_hits(blocking_col, matchkey) > 0`:
    rule does not fire (vetoed; falls through to next rule)
  - Else: today's swap behavior

**`rule_corruption_normalize` (NEW):**
- Fires when `priors[blocking_col].corruption_score > 0.4` AND `priors[blocking_col].identity_score > 0.6` AND profile is YELLOW or RED AND no normalization rule already exists for `blocking_col` in `current.standardization`
- Returns a new config with `AddNormalizeStandardization(blocking_col)` applied
- Standardization rule chosen by column type: email → `lowercase + strip + remove_invisible`; phone → `digits_only`; name → `casefold + strip`
- **One-shot guard**: the "already-exists" check on `current.standardization[blocking_col]` prevents this rule firing twice on the same column. If `rule_no_matches` (position 11) also wants to add normalize on the same column in the same iteration, it'll see the rule already added by `rule_corruption_normalize` (position 6) and try the next alternative in its action list (lower_threshold or multi_pass).

**`rule_cross_blocking_disagreement` (NEW):**
- Fires when iter ≥ 1, profile RED, `mass_above_threshold < 0.1`, and `ctx.cross_blocking_overlap(blocking_a, blocking_b) < 0.3` (where `blocking_b` is a heuristically-chosen orthogonal key from remaining columns)
- Returns `[AddMultiPass(orthogonal_key)]`

**`rule_sparse_match_expand` (NEW):**
- Fires when `ctx.sparsity_verdict.is_sparse` AND `iteration <= 1` AND `not ctx.has_fired("sparse_match_expand")`
- Returns `[ExpandSample(2.0)]`
- After applying, calls `ctx.mark_fired("sparse_match_expand")` to prevent re-firing

## Error handling

| Failure mode | Where | Behavior |
|---|---|---|
| Wall-clock budget exceeded | Any indicator function | Returns None; rule treats as "indicator unavailable"; falls back to today's behavior. INFO log per indicator-per-run on timeout |
| Exception inside indicator function | Any indicator function | Caught at `IndicatorContext` boundary; logged at WARNING with traceback hash; returns None. Accumulates in `_LAST_CONTROLLER_RUN.errors` |
| `column_priors[col]` lookup miss | New rules reading priors | Treat missing key as `ColumnPrior(identity_score=0.0, corruption_score=0.0)`. Defensive |
| Action application failure | `controller._apply_action(action, config) -> (config, applied: bool, error: str | None)` | Controller falls through to `actions[1]`; if all fail, treats as POLICY_NO_PROGRESS |
| Memory cache deserialization | Loading v1.9-saved entries | **Profiles are not persisted to memory cache.** `autoconfig_memory.py` stores only `GoldenMatchConfig` (Pydantic) + a profile signature hash + `succeeded` flag. Adding `column_priors` / `indicators` fields to `DataProfile` / `ComplexityProfile` (both `@dataclass`, not Pydantic) has zero serialization impact on the on-disk SQLite schema. Verification: a "load v1.9-vintage cache entry" test asserts the entry round-trips intact (no schema migration required). |
| `PolicyDecision.action` access on multi-action decision | Backward-compat property | Returns `actions[0]` + emits DeprecationWarning. Removed in v2.0 |

**Determinism:** indicators that sample (corruption-score) use a fixed seed derived from df hash, not random. Memoization cache uses tuple keys with deterministic ordering. Cross-version determinism not guaranteed (indicator functions may evolve).

**Three concrete bugs prevented:**
- Identity-column prior computed *after* user `column_types` overrides (priors respect user's intent).
- Cross-blocking overlap on same key (degenerate) returns 1.0 → rule short-circuits, won't infinitely propose the same multi-pass.
- ExpandSample fires once per `auto_configure_df` (guarded by `iteration <= 1` AND `ctx.has_fired` flag).

**Thread-safety:** unchanged from v1.8/v1.9. `IndicatorContext` is per-`auto_configure_df`-call (not shared across runs); the memoization dict is not concurrency-safe but is never accessed concurrently in the controller's single-threaded iteration loop.

## Testing

### Tier 1 — Unit tests per indicator function (`tests/test_indicators.py`, ~150 LOC, 20 tests)

5 functions × 3-4 cases each:
- `compute_column_priors`: identity_score on `email`/`ssn`/`phone` is high; 0.0 for booleans/dates; user-overridden `column_types` respected; missing column → empty dict
- `estimate_full_pop_hits`: 0 on disjoint blocking; >0 when v0 finds duplicate emails; budget timeout returns None
- `cross_blocking_overlap`: 1.0 on identical keys (degenerate); 0 on orthogonal keys with no shared candidates
- `compute_corruption_score`: high on `Brian/brian/B.` variants; low on perfect-match `email@host`
- `estimate_sparse_match_signal`: marks sparse when `n_exact_hits < 50`; not sparse when ≥ 50

### Tier 2 — Per-rule fire/no-fire tests (`tests/test_autoconfig_rules.py`, ~200 LOC, 10 tests)

- `rule_no_matches` × 3 (high-prior, sparse, baseline) — each returns the right action list
- `rule_blocking_key_swap` × 2 (vetoed when prior+hits both signal good; today's swap when not)
- `rule_cross_blocking_disagreement` × 2 (fires on low overlap; no-fire on high overlap)
- `rule_corruption_normalize` × 2 (fires when corruption + identity both pass; no-fire otherwise)
- `rule_sparse_match_expand` × 1 (fires once on iter ≤1; not on iter > 1)

### Tier 3 — Rule signature + ctx forwarding (`tests/test_autoconfig_policy.py`, ~80 LOC additions)

- `HeuristicRefitPolicy.propose(profile, current, history, ctx=ctx)` forwards `ctx` to each rule
- `LLMRefitPolicy.propose(profile, current, history, ctx=ctx)` forwards `ctx` to its wrapped base policy
- Custom-policy backward compat: a 3-arg-only mock policy (no `ctx` param) still works via the controller's signature introspection — verify with a deliberately-old-shape mock policy
- Existing rules called with `ctx=None` produce no warnings under `warnings.simplefilter("error", DeprecationWarning)` (no spurious deprecations)
- Rule-internal alternative-tries: `rule_no_matches` with high identity prior tries `lower_threshold` first; if that yields `new_config == current` (already at floor), falls through to `add_normalize`; if THAT yields `new_config == current` (already normalized), falls through to `add_multi_pass`. Verify each fall-through path produces a different config or returns None when truly stuck.

### Tier 4 — Integration on T1-style fixture (`tests/test_dqbench_t1_recovery.py`, ~120 LOC)

Synthetic 200-row "noisy email" fixture: 50 true duplicate pairs with corrupted emails (`Brian@gmail` vs `BRIAN@gmail`), 150 unique records, plus city/name fields.

Asserts:
- Committed config has `email` blocking (NOT `first_token(first_name)`)
- Committed config has `AddNormalizeStandardization` for email
- `mass_above_threshold > 0.1` (not collapsed to 0)
- Cluster count between 50 and 100 (not "everything in one cluster")

This is the in-CI guard the v1.9 review flagged as missing.

### Tier 5 — Cache backward compat (`tests/test_autoconfig_memory_v1_9_compat.py`, ~50 LOC)

- Snapshot a `ComplexityProfile` serialized by v1.9 (committed JSON fixture in `tests/fixtures/`)
- Load via v1.10's deserializer; assert `column_priors is None` and `indicators is None` defaults; assert `mem.lookup_best()` returns the entry intact
- Save a v1.10 entry; assert it round-trips with all new fields populated

### Tier 6 — Property tests (`tests/test_autoconfig_properties.py`, ~60 LOC additions)

- All 5 existing properties hold (no behavior change on YELLOW-reaching paths)
- New: indicator computation is deterministic given fixed df hash
- New: `IndicatorContext` memoization is hit-once-per-(fn, args) within a run

### Tier 7 — Performance budgets (`tests/test_indicators_budget.py`, ~50 LOC)

- Each indicator function on a 50K-row synthetic df completes within its budget
- `IndicatorContext` records timeout when a mocked-slow function is forced to exceed budget

## Acceptance criteria

1. **All 7 test tiers pass.** New test count ~+60 (1850 → ~1910).
2. **DBLP-ACM, Febrl3, NCVR hold.** F1 ≥ v1.8/v1.9 baselines (0.9641 / 0.9443 / 0.9719). Committed health YELLOW or GREEN.
3. **DQbench composite ≥ 70 (no LLM)** — primary target.
4. **DQbench composite ≥ 65 (no LLM)** — fallback contract; if (3) fails, branch can ship as v1.10 with the gap queued for v1.11.
5. **Wall-clock budget**: `auto_configure_df(df)` on a 50K-row dataset completes within 75s.
6. **Cache compat**: a v1.9-saved entry loads cleanly into v1.10. PR includes the v1.9 fixture snapshot.
7. **PR description includes per-tier DQbench breakdown.** Indicator-attribution sweep (one disabled-indicator run per indicator, ~10 min each = 1hr total) is **conditional**: required only if shipping at composite ≥ 70 (so we can document which indicators delivered the win); not required if shipping the ≥65 fallback (per-tier breakdown alone is sufficient).

## Risks

- **`RefitPolicy.propose` signature widening**: existing custom policy implementations (none in tree, but possible in user code) take 3 positional args. Mitigation: controller introspects via `inspect.signature(policy.propose)` and falls back to 3-arg call when `ctx` isn't a parameter — Tier 3 covers this with a deliberately-old-shape mock policy. No `__all__` exports `RefitPolicy` as a stable public type, so this is best-effort backward compat.
- **Cross-blocking probe wall-cost variance**: 20s budget covers 50K rows, but bigger datasets may consistently timeout, eliminating the indicator's signal. Mitigation: corruption-normalize rule offers an alternative path that doesn't depend on cross-blocking.
- **YAGNI risk on `ExpandSample`**: only fires on iter ≤ 1. If we never observe it firing on real benchmarks, drop the rule + indicator in v1.11.
- **Indicator-attribution costs N+1 DQbench runs**: each disabled-indicator run is ~10 minutes. Mitigation: only run the attribution sweep once per major spec change, not per-PR.

## Implementation sequence (informational — full TDD plan in `writing-plans` output)

1. Build `core/indicators.py` with 5 functions + dataclasses (~250 LOC) + Tier 1 unit tests
2. Add `ColumnPrior` and `IndicatorsProfile` to `complexity_profile.py`; default-None fields on `DataProfile` and `ComplexityProfile` + Tier 5 cache compat tests
3. Widen `RefitPolicy.propose` signature with optional `ctx` kwarg; update `HeuristicRefitPolicy` + `LLMRefitPolicy` to forward; add controller signature-introspection for old-shape custom policies + Tier 3 tests
4. Add `IndicatorContext` to controller + thread `ctx` through `policy.propose(...)` signature
5. Modify `rule_no_matches` and `rule_blocking_key_swap` (smallest delta first) + Tier 2 tests
6. Add `rule_corruption_normalize`, `rule_cross_blocking_disagreement`, `rule_sparse_match_expand` + Tier 2 tests
7. Tier 4 T1-recovery integration test + Tier 7 budget tests
8. Re-measure DBLP-ACM/Febrl3/NCVR; re-measure DQbench no-LLM; iterate on rule firing conditions until ≥70 (or accept ≥65 fallback)
9. Update CLAUDE.md, CHANGELOG, version bump to 1.10.0
10. Open PR; gh auth dance; release; PyPI publish
