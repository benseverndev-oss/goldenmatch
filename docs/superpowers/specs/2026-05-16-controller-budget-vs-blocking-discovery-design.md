# Controller budget vs blocking-discovery pathology — adaptive budget + structured refusal

**Status:** Design (drafted 2026-05-16)
**Author:** Claude + bsevern
**Scope:** `packages/python/goldenmatch/goldenmatch/core/autoconfig_controller.py`.
**Related:**
- Controller v3 planner spec: [`2026-05-15-controller-v3-planner-design.md`](2026-05-15-controller-v3-planner-design.md). v3 picks the right *backend* at scale; this spec fixes what gets committed when the controller's sample iteration can't find blocking signal in time. The two specs are independent — controller v3 lands a separate axis (backend selection), this spec lands the iteration-time-budget axis.
- Pre-fold introspective controller spec: `2026-05-06-autoconfig-introspective-controller-design.md` — defines `ControllerBudget`, `RunHistory.pick_committed()`, the iteration loop, the `_assemble_v0_history_entry` fallback. The budget defaults this spec changes were set there.

## The pathology

`AutoConfigController.run` builds a config by iterating on a stratified sample of the input: build blocking keys, score sample pairs, cluster, measure a `ComplexityProfile` (data / matchkey / blocking / scoring / cluster sub-profiles), let the policy propose a refit, loop until GREEN / converged / out of budget. Today `ControllerBudget` defaults are fixed regardless of `df.height`:

```python
@dataclass
class ControllerBudget:
    max_iterations: int = 3
    max_seconds: float = 30.0
    sample_size_default: int = 2000
    sample_skip_below: int = 5000
```

At very small N (<5K) sampling is bypassed and the loop runs on the full df. At ~10K-100K those defaults work fine — measured 100K bench is 21.14s median. **At >=500K on real-ish data the defaults break down:**

1. A 2000-row random sample of a 500K-row dataset with a typical ~0.5% dup-pair density has only ~50 expected true pairs. After blocking, those pairs often land in singleton blocks and never get scored. The `ScoringProfile` reports `mass_above_threshold = 0.0` -> RED.
2. The 30s wallclock budget covers GoldenCheck + auto-fix + matchkey-transform precompute + blocking + scoring + clustering for the sample. On a synthetic surname fixture at 500K rows, the per-iteration cost dominates the budget. Loop exits with `stop_reason=BUDGET_TIME` after one or two RED iterations.
3. `pick_committed(precision_collapse_floor=0.9)` falls through to the synthetic v0 entry (RED). The caller (`auto_configure_df`) returns config_v0.
4. `_api.dedupe_df` then runs `_run_dedupe_pipeline(full_df, config_v0)` — full 500K-row dedupe with the un-tuned heuristic v0 config. **Measured wall: ~1547s (~26 min) on a `large-new-64GB` runner**, output mostly noise. The user gets a `DedupeResult` plus a warning line buried in stderr.

The same shape surfaces in miniature on `tests/test_autoconfig_regressions.py::test_total_records_equals_input_row_count_with_duplicates`: 24-row fixture -> sample = full df = too small for any blocking key to discriminate -> v0 RED -> downstream `total_records` value flakes between 24, 500, 2000 on different Linux CI runs.

The pathology is real and not specific to one fixture. Controller v3 (the planner that picks `backend=chunked` etc.) doesn't help, because the planner runs *after* `pick_committed` and only chooses how to *execute* the committed config. If the committed config is degenerate, the planner picks the right backend to run degenerate dedupe at scale, but it's still degenerate.

## Goals

1. **At 100K-1M, sample iteration succeeds more often** — by making the sample large enough to carry duplicate signal, with a budget that covers the sampling cost.
2. **At 100K+, the caller can't accidentally burn 26 min on a config the controller knows is degenerate** — by raising a structured error when the committed entry is RED and a fallback explicit config is the only safe option.
3. **At <100K, behavior is unchanged** — the 30s/2000-row defaults work, and breaking the small-N path would regress every existing test and the 100K bench.

## Non-goals (deferred to follow-ups)

- Progressive multi-stage sampling (start small, escalate to larger sample on RED). Single-shot adaptive sampling covers the load-bearing case; multi-stage adds threaded state through the iteration loop for unproven benefit. If a real case shows up where single-shot misses but progressive would have caught it, that's a follow-up.
- A dup-density probe that distinguishes "iteration needs more time" from "no matches exist in this data." `ScoringProfile.random_pair_above_threshold_rate` already carries this signal but isn't wired into a fast-fail gate. The structured error from this spec is good enough to surface the failure clearly; smarter classification is polish.
- LLM-policy escalation when heuristic iteration RED's (`GOLDENMATCH_AUTOCONFIG_LLM`). Already exists, orthogonal to this spec.
- Reworking the v0 heuristic itself. The v0 config is what it is; this spec is about how the controller handles the case where iteration on top of v0 doesn't find a healthy config.

## Design

### Three additions; everything else stays

| Unit | Lives in | Responsibility |
|---|---|---|
| `ControllerBudget.for_dataset(n_rows)` classmethod | `core/autoconfig_controller.py` | Pure function: `n_rows -> ControllerBudget`. Table-driven; single source of truth for sample-size / budget calibration |
| `auto_configure_df` budget wiring | `core/autoconfig.py` | Pass `ControllerBudget.for_dataset(df.height)` to `AutoConfigController(budget=...)` instead of the default-constructed budget |
| `ControllerNotConfidentError` + confidence gate | `core/autoconfig_controller.py` | New exception class + a guard in `run()` after `pick_committed`: when the committed entry's profile is RED AND `df.height >= REFUSE_AT_N`, raise instead of returning the v0 fallback |

Existing units that stay untouched: the 7 planner rules, `RunHistory.pick_committed()` lex key, the `_assemble_v0_history_entry` synthetic-v0 path (still used as the source of the RED entry the gate inspects), the policy loop, the iteration counter, the drift detector, the cross-run memory cache.

### ControllerBudget.for_dataset

```python
@dataclass
class ControllerBudget:
    max_iterations: int = 3
    max_seconds: float = 30.0
    sample_size_default: int = 2000
    sample_skip_below: int = 5000
    converge_epsilon: float = 0.05
    drift_threshold: float = 0.30

    @classmethod
    def for_dataset(cls, n_rows: int) -> ControllerBudget:
        """Calibrate budget + sample size to the input row count.

        At <5K, sample_skip_below kicks in and the controller runs on the
        full df — sample_size_default value doesn't matter, but max_seconds
        still needs to be tight or every tiny test pays the iteration cost.

        At 5K-100K, the historical defaults (30s / 2000 rows) carry the
        measured 100K bench, so we preserve them.

        Above 100K, sample_size grows as ``sqrt(n_rows) * 20`` and
        max_seconds doubles to absorb the higher cost. Sqrt-scaling
        preserves expected dup-pair density in the sample within an
        order of magnitude as N grows from 100K -> 1M:
          - N=100K, dup_rate=0.5%, sample=6324 -> ~158 expected pairs
          - N=1M,   dup_rate=0.5%, sample=20000 -> ~500 expected pairs
        That's enough signal for ScoringProfile.mass_above_threshold to
        register a non-zero on real-shape data.
        """
        if n_rows < 5_000:
            return cls(max_seconds=15.0)            # tight; full df anyway
        if n_rows < 100_000:
            return cls()                            # default: 30s / 2K
        if n_rows < 1_000_000:
            sample = min(int((n_rows ** 0.5) * 20), 20_000)
            return cls(sample_size_default=sample, max_seconds=60.0)
        return cls(sample_size_default=20_000, max_seconds=120.0)
```

Pure function, no side effects, table-driven. Trivially testable. Knob choices are calibration; if measurement shows different numbers work better, the change is a one-liner.

### Confidence gate

`REFUSE_AT_N = 100_000`. Rationale: empirical 500K bench measured ~1547s (~26 min) wall on `large-new-64GB` with degenerate v0 dedupe. Linear back-projection: at 100K rows the same pathology would burn roughly 5 min of wall — the rough boundary where users notice and where blocking-key-discovery cost dominates the iteration budget. Below 100K, today's warn-and-run behavior is acceptable; above it, the cost is real enough to refuse. The kwarg escape hatch (`confidence_required=False`) covers callers who disagree with the threshold.

```python
REFUSE_AT_N = 100_000  # measured: ~26 min at 500K back-projects to ~5 min at 100K


class ControllerNotConfidentError(Exception):
    """Raised when the controller committed a RED-health config on a large
    input. Carries the failing sub-profile + a docs reference so the caller
    can recover. The exception deliberately does NOT carry a "suggested
    config" because the only material the controller has to suggest from is
    config_v0 + the priors that produced the RED commit -- handing those
    back as a suggestion is a footgun (looks authoritative; isn't).

    Caller responsibility: catch this, decide whether to (a) pass an
    explicit config, (b) lower threshold, (c) accept the noisy output by
    re-calling with confidence_required=False (see backward-compat note).
    """

    DOCS_URL = "https://github.com/benseverndev-oss/goldenmatch/blob/main/docs/explicit-config.md"

    def __init__(
        self,
        *,
        n_rows: int,
        failing_sub_profile: str,    # "scoring" / "blocking" / etc.
        stop_reason: str,            # StopReason value name
    ) -> None:
        self.n_rows = n_rows
        self.failing_sub_profile = failing_sub_profile
        self.stop_reason = stop_reason
        super().__init__(
            f"AutoConfigController committed a RED config on a "
            f"{n_rows}-row input (failing sub-profile: {failing_sub_profile}, "
            f"stop_reason: {stop_reason}). Running this config would produce "
            f"degenerate dedupe; passing it back instead of running. "
            f"Options: pass an explicit GoldenMatchConfig, lower the matchkey "
            f"threshold, or re-call with confidence_required=False. See "
            f"{self.DOCS_URL}."
        )
```

In `AutoConfigController.run`, after `best_entry = history.pick_committed(...)`:

```python
        if (confidence_required
                and df.height >= REFUSE_AT_N
                and best_entry.profile.health() == HealthVerdict.RED):
            failing = _identify_failing_subprofile(best_entry.profile)
            raise ControllerNotConfidentError(
                n_rows=df.height,
                failing_sub_profile=failing,
                stop_reason=history.stop_reason.name if history.stop_reason else "unset",
            )
```

`_identify_failing_subprofile` walks the rolled-up `ComplexityProfile` in priority order **`[data, blocking, scoring, matchkey, cluster]`** (root causes upstream first) and returns the name of the first sub-profile reporting RED. If multiple are RED, the caller can still inspect `controller_history.entries[-1].profile` for the full picture; the exception field is the lead diagnostic.

### Backward compatibility

The structured refusal is a behavior change: at N >= 100K, code paths that previously got a `DedupeResult` with a buried warning will now hit an exception. Three callers are affected:

1. **`gm.dedupe_df()`** — most callers. The zero-config path catches `ControllerNotConfidentError`, logs a structured error (severity ERROR not WARNING), and re-raises. The TUI / CLI surface a clean message.
2. **`gm.match_df()`** — same handling.
3. **`gm.auto_configure_df()`** — re-raises directly. This is the "I want the config object back" path; surfacing the failure is the whole point.

For callers that genuinely want the degenerate-but-runnable v0 config back (CI smoke tests, demo notebooks that don't care about output quality):

```python
gm.dedupe_df(df, confidence_required=False)  # opt out of the new gate
```

The kwarg defaults to `True` (raise). `auto_configure_df` plumbs it into the controller; controller's `run()` skips the gate when it's False and returns config_v0 with the warning log line, exactly as today.

The kwarg is **not on `ControllerBudget`** because budget is per-iteration tuning and the gate is per-call policy. Mixing them obscures both.

### Sample selection unchanged

`_sample_one` keeps its current shape — random stratified sample of `min(sample_size_default, df.height)` rows, seeded from a hash of the data shape. Only the sample-size value changes (via the adaptive budget). Stratification by candidate blocking keys (Mode A polish) is out of scope.

## Data flow

```
caller: gm.dedupe_df(df)
        │
        ▼
_api.dedupe_df
        │ confidence_required=True (default)
        ▼
auto_configure_df(df, ..., confidence_required=True)
        │
        │ NEW: budget = ControllerBudget.for_dataset(df.height)
        ▼
AutoConfigController(budget=budget).run(df, ...)
        │
        ├── 1. sample = _sample_one(df)   # size from budget
        ├── 2. iteration loop (unchanged)
        ├── 3. pick_committed -> best_entry
        │
        ├── 4. NEW: confidence gate
        │     if confidence_required (default True)
        │         and df.height >= REFUSE_AT_N
        │         and best_entry.profile.health() == RED:
        │             raise ControllerNotConfidentError(...)
        │     # confidence_required=False short-circuits the gate entirely;
        │     # path then matches today's behavior (warn + return v0).
        │
        ├── 5. planner v3 (unchanged): plan = apply_planner_rules(...)
        │     plan.apply_to(committed_config)
        │
        └── 6. return committed_config, profile, history
        │
        ▼
_api.dedupe_df catches ControllerNotConfidentError,
re-raises after logging (clean error surface)
```

When `confidence_required=False`: step 4 is skipped, behavior is identical to today.

## Error handling

| Failure mode | Before this spec | After this spec |
|---|---|---|
| 100K-1M, sample iteration converges GREEN | Returns committed config (normal) | Same — adaptive budget helps it succeed more often |
| 100K-1M, sample iteration converges RED on real data | Returns v0 + warn -> caller runs 26-min degenerate dedupe | Raises `ControllerNotConfidentError`. Caller passes explicit config OR re-calls with `confidence_required=False` |
| <100K, any outcome | Unchanged | Unchanged (gate not triggered) |
| >=100K with `confidence_required=False` | N/A | Returns v0 + warn (today's behavior, kept as escape hatch) |
| <100K with `confidence_required=True` | N/A | Returns v0 + warn (gate not triggered by row-count) |
| Controller errors on every iteration | Returns config_v0 + `_RED_PROFILE` sentinel | Unchanged. The gate fires only when `pick_committed` *returned* a RED entry; an all-iterations-errored case hits the existing `best_entry is None` branch which still returns the sentinel |

The new error path is a single exception type with structured fields. Callers can either let it propagate (most cases) or catch + opt-out (CI smoke tests etc.).

## Testing

Six new test modules, each one job:

| Test file | What it locks in |
|---|---|
| `tests/test_controller_budget_for_dataset.py` | Each tier of the `ControllerBudget.for_dataset(n_rows)` table: <5K returns 15s budget, 100K returns sqrt-scaled sample, 1M returns the 20K cap |
| `tests/test_controller_not_confident_error.py` | Exception construction: structured fields (`n_rows`, `failing_sub_profile`, `stop_reason`) populated correctly; `DOCS_URL` class attribute is non-empty; rendered `str(exc)` is human-readable and includes the failing sub-profile + stop reason. No `suggested_config` field — verifying the spec's deliberate omission |
| `tests/test_controller_confidence_gate.py` | (a) Branching tests: gate fires when `df.height >= REFUSE_AT_N` AND committed health is RED. Gate does NOT fire on small-N. Gate does NOT fire on GREEN/YELLOW. Gate does NOT fire when `confidence_required=False`. Wired via monkey-patched `pick_committed` for fast branching coverage. (b) End-to-end real-iteration test: build a synthetic 100K-row df via a `_gate_test_df`-style helper (cheap; only `df.height` and minimal column structure matters), let the *real* iteration loop produce a RED commit (force via the same shape that causes the 24-row Phase 4 flake), assert the gate fires. This locks in that a future refactor of `pick_committed` can't silently break the gate while the monkey-patched tests still pass |
| `tests/test_api_confidence_required_kwarg.py` | `gm.dedupe_df(df, confidence_required=False)` returns a result; `gm.dedupe_df(df)` raises on the same fixture |
| `tests/test_autoconfig_regressions.py::test_total_records_*` | Pre-existing Linux CI flake. After this spec, the test fixture (24 rows) is unaffected because `REFUSE_AT_N=100K`. The fix for that flake is independent (see `feedback_synthetic_surname_fixtures.md`-style fixture work) |
| `tests/test_controller_adaptive_e2e.py` | Sanity-check end-to-end: at df.height=10K, the controller picks a 2000-row sample (unchanged); at simulated df.height=200K (via monkey-patched `df.height`), the controller picks an sqrt-scaled sample |

The 500K bench-dispatch case (the original symptom) is the integration test we can't run in CI — but the unit tests above cover every code path it would exercise, and the 100K bench still has to stay <= 24s.

## Acceptance criteria

- All new tests pass.
- **100K zero-config bench wall stays <= 30s median.** Note: the bench dispatches with `n_records=100000`, which lands in the new tier (`100_000 <= n < 1_000_000` -> sample_size=sqrt(100_000)*20=6324, max_seconds=60). That's a 3.16x sample-size increase vs today's 2000 rows; the dominant per-iteration costs (GoldenCheck + auto-fix + matchkey transform) scale roughly linearly with sample size, so expect the wall to climb from today's 21.14s median to ~25-28s. Setting the gate at 30s gives headroom; if the measured number after Phase 1 lands cleanly under 24s the gate can be tightened. Justification for accepting the regression: the 6324-row sample carries 3x more dup-pair signal, increasing GREEN-rate at 100K and reducing the chance of hitting the gate. The 100K-as-bench-target is also the *boundary* case; in practice production 100K workloads either land exactly there or comfortably inside the old tier.
- A re-dispatched 500K bench on the same synthetic surname fixture either (a) converges GREEN/YELLOW within the adaptive 60s budget, OR (b) raises `ControllerNotConfidentError` and exits in well under 26 min. The point is to stop *silently producing 26-min degenerate dedupe*.
- CLAUDE.md updated: the "Recommended for 5M today: backend='chunked' + config_mode='explicit-personlike'" pin can drop the controller-budget caveat from the controller-v3 closeout note; the new caveat is "auto-config raises ControllerNotConfidentError at >=100K rows when it can't find a healthy config — pass an explicit config or `confidence_required=False`."
- Memory: a new `feedback-controller-confidence-required.md` entry pinning the "auto-config now raises on degenerate large-N commits" behavior so future-me doesn't re-introduce silent-degenerate-run code paths.

## Open questions

1. **`REFUSE_AT_N` calibration after Phase 1 lands.** 100K is justified by linear back-projection from the one measured data point (500K -> 26 min, so 100K -> ~5 min). Once Phase 1 is in main, dispatching a 100K bench with a forced RED commit (via the `_gate_test_df`-style fixture in the integration test) gives us a measured wall for "degenerate dedupe at 100K." If that number is under ~3 min, the gate could move up to 250K to align with the chunked-tier threshold. If it's over 7 min, the gate is correctly placed (or could move down). The kwarg escape hatch makes recalibration low-risk; not a blocker for Phase 1.

2. **Whether to add a docs/explicit-config.md companion file.** The exception references `DOCS_URL` pointing at a hypothetical `docs/explicit-config.md`. That file doesn't exist today; the spec assumes it gets written as part of Phase 6 (CLAUDE.md / memory closeout). If the implementer reaches Phase 6 and the doc still doesn't exist, the URL can degrade to a CLAUDE.md-section anchor (which does exist) without redesigning anything.
