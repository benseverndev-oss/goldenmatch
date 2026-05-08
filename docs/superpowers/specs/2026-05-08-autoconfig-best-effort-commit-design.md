# Auto-Config Best-Effort Commit

**Status:** Design (approved by user 2026-05-08; spec-review pass 1 incorporated 2026-05-08)
**Author:** brainstorm session, Claude + bsevern
**Scope:** `core/autoconfig_history.py`, `core/autoconfig_controller.py`, `core/complexity_profile.py` + their tests
**Related:**
- v1.8.0 spec: `2026-05-06-autoconfig-introspective-controller-design.md`
- v1.8.0 release: 14 PRs (#102–#115), shipped 2026-05-07
- DQbench T1/T2 diagnostic: `.profile_tmp/diagnose_dqbench_blocking.txt`

## Problem

After the v1.8.0 release, three benchmarks measure at hand-tuned-or-better with zero-config (DBLP-ACM 0.964, Febrl3 0.944, NCVR 0.972). DQbench's three tiers measure at 62.87 / 100 — about 16 points above the hand-tuned-without-LLM score (46.24) but well below the published with-LLM ceiling (95.30).

The diagnostic from PR #114 / #115 traces the gap to a single architectural fact: **on DQbench T1/T2, every controller iteration produces a RED profile**, and `RunHistory.cheapest_healthy()` filters RED entries — returning `None`. The controller's `run()` then falls back to `(config_v0, _RED_PROFILE_SENTINEL, history)`. All the post-iteration plumbing (LLM-scorer auto-decoration, standardization auto-detection, memory persistence, drift detection) is gated on `best_entry is not None`, so none of it runs. The controller's improvements never get to apply on these tiers.

The same fact will bite any future dataset where the controller iterates but doesn't reach a healthy profile. v0 fallback was never specified — it's an emergent consequence of `cheapest_healthy()` returning `None`. v0 is *also* a config that's RED on the same data (often the same RED, since the rules just refined v0). So the current behavior commits an untested config that has the same health issues as the iterations the controller spent compute on, instead of committing the most-refined of those.

## Goals

1. **Commit best-effort whenever any iteration produced a profile.** When the controller has done useful work — even if it didn't reach a healthy verdict — return that work, not v0.
2. **Make the verdict observable.** The user-visible signal (`postflight_report.controller_profile.health()`) tells them what they got. Logs name the failing sub-profile.
3. **Surface why iteration stopped.** `RunHistory.stop_reason` finally gets populated (closes a known design-doc gap from the v1.8.0 spec review).
4. **Backward-compatible deprecation.** `RunHistory.cheapest_healthy()` keeps working for one release with a `DeprecationWarning`; behavior delegates to the new method.

## Non-goals (this spec)

- **Stronger T1/T2-class rules.** A separate followup. Without best-effort commit, even great new rules can't deliver — they'd just produce more RED entries that get discarded. With best-effort commit, today's rules become more effective; we'll re-measure DQbench and decide which new rules are actually needed based on that evidence.
- **Health rule re-tuning.** Don't loosen RED → YELLOW thresholds to "win" on more profiles. Keep health verdicts honest; let commit semantics deal with the unhealthy case.
- **Public API additions.** No new `auto_configure_df` kwargs, no new env vars. The change is observable via the existing `controller_profile` and `controller_history` fields on `PostflightReport`.

## Decision summary

| Decision | Choice | Why |
|---|---|---|
| Scope | A only — defer "stronger T1/T2 rules" (B) | Smaller change, leveraged: rules become effective once commit-RED works |
| Opt-in/out | Always commit best-effort | Default v0 fallback was emergent, not designed; v0 is RED on the same data |
| RED ranking | Same lex key as today (`health_rank`, `-mass_separation`, `iteration`) extended to `rank=2` for RED, with `error is None` filter | Single ordering rule across tiers; errored iterations excluded |
| All-errored handling | `pick_committed()` returns `None`, controller falls back to v0 + ERROR log | Preserves today's safety on "controller couldn't run at all" |
| Memory caching | Store all commits via `mem.remember(...)`. `succeeded = (profile_full.health() != RED)` — uses the **full-data** finalize profile, not the sample iteration profile, because that's what the user actually saw run. `lookup_best()` filters `succeeded=1` only. | Diagnostics via `mem.all_for(signature)` surface RED commits without polluting lookup |
| `StopReason` recording | Add `RunHistory.stop_reason: StopReason \| None` and set at each break | Observable telemetry; closes existing design-doc gap |
| Naming | Rename `cheapest_healthy()` → `pick_committed()`, deprecation alias for one release | Old name actively misleads once RED commits are valid |
| Implementation shape | Direct refactor (Approach 1) — single function in single place | Approach 2 (parallel method) and Approach 3 (strategy pattern) are over-engineered for present need |

## Architecture

Three small surgical changes to existing files. No new modules.

### Renamed method

`RunHistory.cheapest_healthy()` → `RunHistory.pick_committed()`. Logic delta:
- **Filter**: `e.error is None and e.profile is not None` (was: `health() != RED`). The `profile is not None` half guards the sentinel-mismatch case where an entry was appended with neither error nor profile.
- **Lex key**: `health_rank ∈ {0, 1, 2}` for `GREEN/YELLOW/RED` (was: `{0, 1}`).
- **Returns None**: only when every entry in `history.entries` errored or has no profile (was: when no entry has `health() != RED`).

**`HistoryEntry` invariant** (added to the dataclass docstring): exactly one of `error` and `profile` is non-None, never both, never neither. The controller's iteration-loop append site enforces this — every code path either records a real profile (success) or records an `ErrorRecord` with the `_RED_PROFILE_SENTINEL` (failure). `pick_committed()`'s filter relies on this invariant; it is documented but not defensively re-checked.

`cheapest_healthy()` stays as a thin alias that emits `DeprecationWarning` once and delegates to `pick_committed()`. Removed in v2.0.

**Cross-version compatibility**: `RunHistory` is not part of any persisted shape — it lives only on the in-process `PostflightReport`, which is not serialized to disk or wire by goldenmatch and has no documented external persistence contract. Cross-version load of a v1.8-serialized `RunHistory` under v1.9 is **explicitly out of scope** for this spec. The deprecation alias's behavior change (returns RED entries that v1.8 callers expected to be None) is tolerable because the alias is only an in-process compatibility shim for source-level callers, not a persistence contract.

### New `RunHistory.stop_reason` field

```python
@dataclass
class RunHistory:
    # ... existing fields ...
    stop_reason: StopReason | None = None
```

Default `None`. Controller sets it at each break point in `run()`.

### `StopReason` moves from controller to `complexity_profile.py`

Avoids a circular import shape: today `RunHistory` imports `ComplexityProfile`; if we add a `stop_reason` field that references `StopReason` from the controller, history would need to import controller, which already imports history. Move the enum next to `HealthVerdict` in `complexity_profile.py`. Controller updates its imports.

### Controller commit logic

- All `history.cheapest_healthy()` call sites become `history.pick_committed()`.
- Each `break` in `run()`'s iteration loop sets `history.stop_reason` to the matching enum value before breaking.
- The fallback-to-v0 branch only fires when `pick_committed()` returns `None` (every iteration errored). Logged at ERROR.
- Successful commit (any committed entry, regardless of health):
  - committed_config = `pick_committed().config` (decorated with LLM scorer per existing `_maybe_decorate_with_llm_scorer` logic — gates haven't changed)
  - profile_full = `_finalize(committed_config, df, reference)` (full pipeline run)
  - `mem.remember(signature, committed_config, succeeded=(profile_full.health() != RED), ...)`
  - Log:
    - `health=RED`: `logger.warning("auto-config committed best-effort RED config (iter=%d, stop_reason=%s, failing_subprofile=%s)", iter, reason.name, subprofile_name)` — see "failing sub-profile selection" below
    - `health=YELLOW`: `logger.info("auto-config committed YELLOW config (iter=%d, stop_reason=%s)", ...)`
    - `health=GREEN`: no log (today's silent success)

**Failing sub-profile selection for the WARNING log:** the named sub-profile is the *first* RED sub-profile in canonical declaration order (`data, domain, matchkey, blocking, scoring, cluster`). If no sub-profile is RED but the rollup is RED (impossible by current health rules but defensive), log `failing_subprofile="rollup"`. Implementation: iterate the six sub-profiles in that fixed order; emit the first whose `health()` returns RED; pass `n_rows` to `BlockingProfile.health` and `ClusterProfile.health` as those depend on it. The log format string uses keyword names (`iter=`, `stop_reason=`, `failing_subprofile=`) so test assertions can grep without sensitivity to argument order.

### Memory unchanged

`AutoConfigMemory.remember()` continues to write all commits. `lookup_best()` continues to filter `succeeded=1`. RED commits are persisted but invisible to lookup; visible via `mem.all_for(signature)` for diagnostics.

### Public API surface

`auto_configure_df` signature unchanged. `dedupe_df`/`match_df` zero-config paths unchanged. New observable: `result.postflight_report.controller_history.stop_reason`.

## Components

| File | Delta | Notes |
|---|---|---|
| `core/autoconfig_history.py` | ~30 LOC | Rename + new logic + deprecation alias + `stop_reason` field |
| `core/autoconfig_controller.py` | ~20 LOC | Call-site updates + `stop_reason` writes + log message change |
| `core/complexity_profile.py` | ~10 LOC | `StopReason` enum moved here from controller |
| `tests/test_autoconfig_history.py` | ~80 LOC | New tests for `pick_committed`; deprecation alias test |
| `tests/test_autoconfig_controller.py` | ~60 LOC | `stop_reason` recording tests; commit-RED end-to-end tests; log message tests |
| `tests/test_autoconfig_facade.py` | ~5 LOC | Update assertions that hardcoded "RED → v0" expectation |
| `tests/test_autoconfig_no_double_run.py`, `test_autoconfig_integration.py` | minimal | Most tests pass unchanged; spot-check for hardcoded RED-fallback expectations |
| **Total** | **~205 LOC + ~150 LOC tests** | Bounded change |

## Data flow

A single `auto_configure_df(df)` call that ends in commit-RED:

```
caller
  │
  ▼
auto_configure_df(df)
  │
  ▼
AutoConfigController.run(df)
  │
  ├──▶ _initial_config(df) ────────▶ GoldenMatchConfig (v0)
  ├──▶ _take_sample(df) ──────────▶ sample_df
  │
  ├──▶ ┌─── ITERATION LOOP ─────────────────────────────────────────┐
  │    │  iter 0..N: each produces a profile; rules fire / no fire   │
  │    │  break path → set history.stop_reason = StopReason.<x>      │
  │    └────────────────────────────────────────────────────────────┘
  │
  ├──▶ best_entry = history.pick_committed()
  │       │
  │       ├── filter: survivors = [e for e in entries
  │       │                         if e.error is None and e.profile is not None]
  │       ├── lex key:
  │       │     rank = {GREEN: 0, YELLOW: 1, RED: 2}[e.profile.health()]
  │       │     sep  = mass_above_threshold - mass_in_borderline
  │       │     key  = (rank, -sep, iteration)
  │       └── return min(survivors, key=key)  OR None if no survivors
  │
  ├──▶ if best_entry is None:    # everything errored
  │       logger.error(...)
  │       return (v0, _RED_PROFILE_SENTINEL, history)
  │
  ├──▶ # log committed entry's health and stop_reason
  │     match best_entry.profile.health():
  │       RED    → logger.warning("committed best-effort RED, stop=%s, failing=%s", ...)
  │       YELLOW → logger.info(...)
  │       GREEN  → no log
  │
  ├──▶ committed_config = _maybe_decorate_with_llm_scorer(
  │       best_entry.config, best_entry.profile)
  │
  ├──▶ profile_full = _finalize(committed_config, df, reference)
  │
  ├──▶ mem.remember(
  │       signature, committed_config,
  │       succeeded=(profile_full.health() != HealthVerdict.RED),
  │       ...,
  │     )
  │     # RED commits persist with succeeded=False; lookup_best filters them
  │
  └──▶ return (committed_config, profile_full, history)
              │
              ▼
       PostflightReport carries:
         controller_profile = profile_full
         controller_history.entries = [...]                (audit trail)
         controller_history.stop_reason = StopReason.X     (NEW)
         controller_history.full_vs_sample_drift = ...
```

### Boundary table

| Site | Returns | Shape change |
|---|---|---|
| `pick_committed()` | `HistoryEntry \| None` | None ⟺ every entry errored |
| `RunHistory.stop_reason` | `StopReason \| None` | NEW field, set at each `break` in `run()` |
| Controller log line | warning/info text | Names committed health + stop_reason + failing sub-profile |
| `mem.remember()` | (no return) | Still called when `best_entry is not None`, regardless of health |
| `mem.lookup_best()` | `GoldenMatchConfig \| None` | Unchanged |

### Path-by-path summary

- **All iterations errored.** `pick_committed()` returns `None`. Controller logs at ERROR, returns v0 + sentinel. Same observable as today.
- **Some errored, some produced profiles.** Errored entries filtered out. Best survivor committed. No special-case logic.
- **All iterations RED.** Best RED entry committed (most `mass_separation`, earliest iteration). Logged at WARNING.
- **Mix of YELLOW/RED.** YELLOW wins on `health_rank` tiebreak. Existing v1.8 behavior preserved.
- **GREEN reached.** Lowest `health_rank=0` wins. Existing v1.8 behavior preserved.

## Error handling

The change reduces error-path complexity rather than adding to it. Five named modes:

1. **All iterations errored** — covered above. Falls through to v0 + sentinel + ERROR log + populated `stop_reason`.
2. **Some errored, some produced profiles** — `pick_committed()` filters via `error is None`. No special handling needed beyond the filter.
3. **`KeyboardInterrupt` mid-iteration** — existing code re-raises after recording elapsed time. Add: also set `history.stop_reason = StopReason.CANCELLED` before re-raise. Caller catching the interrupt and inspecting history sees what happened.
4. **Logging failure on commit-RED warning** — wrap the new log statement in `try/except Exception` so a logger configuration error never escapes the controller. The log is informational; failing to log shouldn't fail the run.
5. **`_finalize` crash on a RED committed config** — propagates today via `FinalizationError(config, profile, original)`. Unchanged. The RED config may legitimately fail to run end-to-end; that's a real bug for the user to see, not something to swallow.

### One new failure mode worth calling out

If `_finalize` crashes on a committed RED config that's *also* the only entry pick_committed could return, today's behavior would have logged "couldn't produce healthy" + returned v0 (which then runs through the pipeline and either works or also crashes). Tomorrow's behavior commits the RED config and runs `_finalize` on it, which crashes. The user gets the crash from the bad RED config rather than the same crash from v0 — but it's a *more honest* crash because the postflight report carries the full controller history showing why the RED was committed.

This is a behavior change. It's defensible (failing visibly is better than failing silently), but it's a behavior change. Tests should explicitly cover both stack origins.

### Thread safety

This change does not alter goldenmatch's thread-safety contract. `auto_configure_df` was never documented as thread-safe and isn't made so by this work; concurrent calls share the global `_DEFAULT_MEMORY` SQLite store (which is itself thread-safe per the lock added in PR #111) but otherwise produce undefined interleaving of `_LAST_CONTROLLER_RUN.set(...)` writes. The new `pick_committed()` operates on a single-thread-owned `RunHistory` instance after the iteration loop completes; no concurrent access path is introduced.

## Testing

Five tiers, mostly delta to existing.

### 1. `pick_committed` unit tests (~5 new in `test_autoconfig_history.py`)

- `test_pick_committed_returns_red_when_no_green_or_yellow`
- `test_pick_committed_excludes_errored_entries`
- `test_pick_committed_returns_none_when_all_errored`
- `test_pick_committed_lex_key_orders_red_by_mass_separation`
- `test_pick_committed_empty_history_returns_none`

### 2. Deprecation alias test

```python
def test_cheapest_healthy_emits_deprecation_warning():
    h = RunHistory()
    h.entries.append(_red_entry())
    with pytest.warns(DeprecationWarning, match="pick_committed"):
        result = h.cheapest_healthy()
    # Behavior change: now returns the RED entry instead of None.
    # The deprecation message must call this out so callers update their
    # code; assertion below verifies the new behavior is in effect.
    assert result is not None
```

### 3. `stop_reason` recording (~6 new in `test_autoconfig_controller.py`)

One per `StopReason` value reachable from `run()`:
- `GREEN` (rules converge to healthy)
- `BUDGET_ITERATIONS` (max_iterations hit, all RED)
- `BUDGET_TIME` (max_seconds hit)
- `OSCILLATING` (`history.is_oscillating()` fires)
- `POLICY_SATISFIED` (policy returns None on YELLOW)
- `CANCELLED` (`KeyboardInterrupt` re-raised; caller catches and inspects history)

Each asserts `history.stop_reason == <expected>` after the run.

### 4. End-to-end commit-RED test (~2 new in `test_autoconfig_controller.py`, real pipeline)

```python
def test_controller_commits_red_when_data_provokes_red():
    """Real pipeline on a synthetic fixture where every iteration produces
    a RED profile. Commits the best RED entry, surfaces stop_reason, runs
    _finalize successfully on the committed RED config (output may be
    low-precision but exists)."""
    df = _build_red_provoking_fixture()       # ~50 rows, T2-shape
    controller = AutoConfigController(...)
    config, profile, history = controller.run(df)
    assert profile.health() == HealthVerdict.RED
    assert history.stop_reason in {
        StopReason.BUDGET_ITERATIONS,
        StopReason.OSCILLATING,
    }
    assert history.entries
    assert all(e.error is None for e in history.entries)
```

The fixture is small (~50 rows) and shaped like DQbench T2 (uniform-large blocks producing high `mass_above` + high `mass_in_borderline` simultaneously). Lives in `tests/fixtures/autoconfig/red_provoking.csv` — committed to the repo, not gitignored.

### 5. Logging tests (~2 new with `caplog`)

- WARNING logged when committed entry is RED — message contains `stop_reason` and the name of a failing sub-profile.
- INFO logged when committed entry is YELLOW; no log when GREEN.

### Backward-compat sweep (no new tests, just updates)

- `tests/test_autoconfig_controller.py::test_run_returns_v0_red_when_all_iterations_crash`: the "all iterations CRASHED" case stays as v0 + sentinel. Update the test name to be more specific (`test_run_returns_v0_when_all_iterations_errored`) and add a new test for "all iterations RED but produced profiles → commit best RED."
- Property tests in `test_autoconfig_properties.py`: unaffected. Determinism still holds (same input → same `pick_committed` result). Sample stability unaffected. Profile non-collapse unaffected (its assertion was about drift, not the RED commit path). No silent crashes still holds (the controller never raises uncaught exceptions inside `run()` except for `KeyboardInterrupt`). History audit unaffected.
- Benchmark regression tests in `test_autoconfig_benchmarks.py`: F1 thresholds for DBLP-ACM/Febrl3/NCVR are unaffected — those datasets all reach GREEN/YELLOW and the existing pick_committed picks them. DQbench tests aren't in the regression suite.

### Performance

No measurable impact. `pick_committed()` does the same `min()` over the same list with one extra rank tier. `stop_reason` is a single field write per break point.

## Acceptance criteria

v1.9 ships when:

1. All five test tiers above pass (~13 new tests, `pytest -q --timeout=180` from `packages/python/goldenmatch`).
2. Existing test suite passes (≥1825 from v1.8.0 baseline + new tests, no regressions).
3. Re-measure DBLP-ACM, Febrl3, NCVR — all hold at v1.8.0 levels (zero-config F1 ≥ 0.94 each). Specifically: each measurement uses `.profile_tmp/measure_dblp_acm_controller.py`, `.profile_tmp/baseline_febrl3_ncvr.py`, `.profile_tmp/measure_ncvr_controller.py` invoked with `GOLDENMATCH_AUTOCONFIG_MEMORY=0` (memory disabled) and a fresh `~/.goldenmatch/autoconfig_memory.db` removed before each run.
4. **RED-commit rate on existing benchmarks is zero** (each of DBLP-ACM, Febrl3, NCVR commits a non-RED entry). Verified by inspecting `result.postflight_report.controller_profile.health()` after each measurement run. If any of the three regress to RED commit, that's a real regression — investigate before merging.
5. Re-measure DQbench. The harness:
   - Custom adapter: `.profile_tmp/goldenmatch_zeroconfig_adapter.py` (committed to `.profile_tmp/`).
   - "no LLM" run: `unset OPENAI_API_KEY ANTHROPIC_API_KEY GOLDENMATCH_AUTOCONFIG_LLM` then `dqbench run goldenmatch-zeroconfig --adapter .profile_tmp/goldenmatch_zeroconfig_adapter.py`. Composite score is the single number printed at the end; per-tier F1 is reported in the per-tier table.
   - "with LLM" run: `set -a && source /d/show_case/goldencheck/.testing/.env && set +a` to load `OPENAI_API_KEY`, then the same `dqbench run` command. The LLM scorer auto-engages via `_maybe_decorate_with_llm_scorer` when borderline mass triggers it; no other env vars set.
   - Both runs preceded by `rm -f ~/.goldenmatch/autoconfig_memory.db` + `GOLDENMATCH_AUTOCONFIG_MEMORY=0` to ensure no cache hit.
   - **Conservative target: composite score ≥ 65 (no LLM)**. The DQbench gain is the visible payoff for the change; no specific number is mandated, but a fall would mean the change broke something. Measured baseline before this work: 62.87 (no LLM and with LLM both, per PR #115).
6. PR description includes a before/after table on the four benchmarks plus a sample stop_reason trace from a DQbench run (showing which tier landed which `StopReason`).
7. CLAUDE.md updated to reflect that `cheapest_healthy()` is deprecated and that `RunHistory.stop_reason` is a new observable.
8. CHANGELOG `[1.9.0]` section drafted with the four benchmark numbers.

## Open questions / things to validate during implementation

1. **The fixture for the end-to-end RED test.** Need to design 50 rows that reliably produce RED profiles across iterations on the current rule set. May need to iterate on the fixture during implementation.
2. **`_finalize` crashing on a committed RED config**: the spec says this propagates as `FinalizationError`, but tests should confirm the propagation path stays the same (no regression in error wrapping).
3. **The `cheapest_healthy()` deprecation alias's behavior change**: documented as a behavior change in the deprecation warning text. Verify no internal callers relied on the old behavior.
4. ~~Memory recording of RED commits~~ — **resolved in §Decision summary**: use full-data profile for `succeeded`. The drift case (sample GREEN, full RED) is honest: the cache should reflect what the user actually ran, not what the controller hoped for.

## Implementation sequence (formalized in implementation plan)

1. Move `StopReason` from controller to `complexity_profile.py`. Update controller imports.
2. Add `RunHistory.stop_reason: StopReason | None = None` field.
3. Add `pick_committed()` method on `RunHistory` with the new filter + lex-key logic. Document the `HistoryEntry` invariant ("exactly one of `error`, `profile` is non-None") in the dataclass docstring.
4. Convert `cheapest_healthy()` to a deprecation shim that delegates to `pick_committed()`. **Adopts new semantics** — the alias returns whatever `pick_committed()` returns, including RED entries that v1.8 callers expected to be None. The DeprecationWarning message must call out this behavior change explicitly so callers update their code (not just the function name).
5. Wire `stop_reason` writes at each `break` in controller's `run()`.
6. Update controller's commit logic — replace `cheapest_healthy()` calls with `pick_committed()`, replace v0-fallback warning with new health-aware logging (per §Architecture, "failing sub-profile selection" rule).
7. Update existing tests, add new tests in tier order (unit → controller integration → end-to-end → logging).
8. Re-measure benchmarks per §Acceptance criteria (specific commands listed there). Document numbers in CHANGELOG.
9. Update CLAUDE.md.

## Out of scope (followups)

- **Stronger T1/T2-class rules.** Re-evaluated based on what DQbench measurements show after this change lands. The rule that actually plugs the gap may be different than what we'd speculate today.
- **A `StrictCommitPolicy` that raises on all-RED.** Discussed but explicitly deferred — no current user demand. The strategy-pattern abstraction is YAGNI until we have a second commit strategy.
- **Health rule re-tuning.** Specifically out of scope; preserve current GREEN/YELLOW/RED thresholds.

## Revision history

### Pass 1 review (2026-05-08)

| ID | Issue | Resolution |
|---|---|---|
| S1-1 | Sentinel-mismatch entries (`error=None AND profile=None`) would crash `pick_committed`'s lex key | §Architecture filter tightened to `error is None and profile is not None`; `HistoryEntry` invariant documented |
| S1-2 | `succeeded` flag for memory caching contradicted between §Architecture (full profile) and §Open questions (still open) | §Decision summary now pins "full-data profile"; §Open questions item resolved |
| S1-3 | Cross-version persistence interaction with the deprecation alias | §Architecture explicitly declares cross-version load out of scope; `RunHistory` is in-process only |
| S2-1 | "failing sub-profile" in WARNING log was unspecified for multi-RED cases | §Architecture pins canonical declaration order (`data, domain, matchkey, blocking, scoring, cluster`) and first-RED selection rule |
| S2-2 | Thread-safety statement absent | §Error handling adds a thread-safety affirmation: unchanged from v1.8 |
| S2-3 | DQbench acceptance criterion not reproducible | §Acceptance #5 now pins exact commands, env-var setup, harness file paths, and the "no LLM" definition |
| S2-4 | Possible scope creep — committing RED through `_finalize` may regress existing benchmarks | §Acceptance #4 (new) requires zero RED-commits on existing benchmarks before merge |
| S3-1, S3-2, S3-3 | Style/clarity polish | Folded into §Components total LOC and §Implementation sequence step 4

---

## Amendment: Phase 3.5 — virtual v0 fallback + precision-collapse floor (2026-05-08, post-implementation)

### What we found

Phase 5.1 (DBLP-ACM 0.9641 YELLOW, Febrl3 0.9443 YELLOW, NCVR 0.9719 YELLOW) confirmed the spec's premise on the published benchmarks. Phase 5.2 surfaced a **catastrophic regression on DQbench**: composite collapsed 62.87 → 22.19 (T1 89.3% → 2.0%, T2 58.7% → 0.7%). Diagnosis (`v0_vs_red_t1_findings.txt`):

- **v0 picks `email` blocking + threshold 0.80 + exact matchkeys** for DQbench T1 (correct for person ER).
- The 1000-row sample contains ~50 true duplicate pairs, most with corrupted emails (e.g. `…@gmail.com` vs `…@GMAIL.COM` post-typo), so under exact email blocking `mass_above_threshold = 0.0` on the sample.
- `rule_no_matches` fires (iter 0) → drops threshold to 0.50; `rule_blocking_key_swap` fires (iter 1) → swaps blocking to `first_token(first_name)`. By iter 2 the controller has abandoned email entirely for a coarse 17-records-per-block key. At threshold 0.50 every pair in those blocks scores above threshold → 218k FPs on T2.
- v1.9's `pick_committed()` selects the iter-3 RED config over discarded v0 — committing a config that's *demonstrably worse* than v0.

The spec's framing was wrong on one premise: **v0 is not always RED on the same data the controller iterates on.** When it isn't, v0 should be in the candidate pool. And RED configs with `mass_above_threshold` near 1.0 are precision disasters that lex-key sort can't catch (high mass_above looks like "good" to the lex key, but means "matches everything").

### Amendment

Add a **virtual v0 entry** to the controller's `pick_committed()` candidate pool plus a **precision-collapse floor** for RED commits.

**Architecture changes:**

1. After the iteration loop and before calling `pick_committed()`, the controller runs `config_v0` through `_run_pipeline_sample` + `_assemble_profile` if it hasn't already been measured (it usually has — iter 0 typically uses v0 or a near-clone). Append the v0 result as a synthetic `HistoryEntry` with `iteration=-1` and `decision=None`. The lex key naturally orders entries by health then mass_separation; a YELLOW v0 beats a RED iter-3, a near-tied RED v0 beats a degraded RED iter-3 if v0 has higher mass_separation.

2. Inside `pick_committed()` (or in the controller's commit-decision branch immediately after), apply a **precision-collapse floor**: if the chosen entry has `health == RED` AND `profile.scoring.mass_above_threshold > 0.9`, prefer the v0 virtual entry instead. If v0 is also `mass_above > 0.9` RED, fall back to v0 + `_RED_PROFILE` (today's all-errored path). This guards the "everything matches" pathology where over-coarse blocking + low threshold makes lex key reward exactly the wrong config.

**Components added:**

- `_assemble_v0_history_entry(controller, sample, ref_dict) -> HistoryEntry` — module-level helper, ~25 LOC. Idempotent: if iter 0's config equals v0, returns iter 0 verbatim with iteration=-1.
- Modified `pick_committed()` accepts an optional `precision_collapse_floor: float = 0.9` kwarg. When the chosen entry trips the floor, returns the v0-virtual entry if present, else None.
- Controller's commit-decision branch: appends v0 virtual entry, calls `pick_committed()`, logs which entry was selected (iteration number or `v0`).

**Tests added:**

- `test_pick_committed_prefers_v0_virtual_when_iterations_red` (history test)
- `test_pick_committed_precision_floor_falls_back_to_v0` (history test, new entry has `mass_above=0.95` RED + v0-virtual is `mass_above=0.4` RED → v0 wins on floor)
- `test_controller_logs_when_v0_virtual_is_committed` (controller test, INFO log includes `iteration=v0` or similar)
- `test_dqbench_t1_does_not_regress_below_v18` (integration test guarded by dataset presence) — F1 ≥ 0.85 on T1 (recovers v1.8 89% with margin)

### Acceptance update

**§Acceptance #5 amended:** "DQbench composite ≥ 65 (no LLM)" stands. v1.8 was 62.87; v1.9 with this amendment must clear 65. If amendment under-delivers, branch is closed and v1.9 is shipped without DQbench gains (best-effort commit on benchmarks where v0 isn't already best, plus stop_reason telemetry, plus deprecation rename).

**§Non-goals:** "stronger T1/T2 rules" still deferred. The amendment doesn't add rules — it changes commit-decision logic only.

### Out of scope for this amendment

- **Re-tuning `rule_no_matches`** to distinguish "blocking is wrong" from "sample has no visible matches". That's the deeper issue but requires adding a positive signal (e.g., "did v0's exact matchkeys match anything full-population?"), which is a separate workstream. The amendment lets the controller back off to v0 instead of fixing the rule.
- **Adaptive `precision_collapse_floor`**. 0.9 is a conservative magic number that catches the DQbench T1 case (mass_above=1.0) without affecting DBLP-ACM/Febrl3/NCVR (mass_above ≤ 0.6). If users hit edge cases, we can adjust later.
