# GoldenPipe auto-config brain — Slice 2 design

**Status:** approved (design gate); revised after spec review (dropped the
unsound `weak_duplication_skip` rule — see §2.2).
**Date:** 2026-07-07
**Builds on:** Slice 1 (`docs/superpowers/specs/2026-07-07-goldenpipe-autoconfig-brain-design.md`) — the portable `autoconfig_planner` core, the rule table, `autoconfig_glue`, and `_plan_config` (default-on).

## 1. Goal

Give the GoldenPipe auto-config brain the one property that separates a
controller from a lookup table: the ability to **refuse** when it cannot plan
well at scale. Slice 1 decides *which* stages to run from cheap up-front signals
(row count, InferMap-detected domain). Slice 2 adds:

1. **ComplexityProfile** — a data-derived signal (null density) from one
   columnar pass. The struct is the home for future complexity signals.
2. **Confidence bands** — every plan carries a green/amber/red band derived from
   its confidence float.
3. **Refuse-on-RED (size-gated)** — a red-band plan on a large input raises
   `PipeNotConfidentError` instead of running an expensive, likely-wrong
   pipeline. Below the threshold it warns and proceeds on the safe default.

This is a **Python prototype** per the Rust thesis: the decision core stays free
of Polars/Pydantic so the later `goldenpipe-core` port is mechanical.

## 2. Scope decisions (locked during brainstorming)

- **Config scope = orchestration-level only.** The brain decides pipeline shape,
  never a downstream stage's internals. This is load-bearing: `adapters/match.py`
  treats an explicit dedupe `stage_config` as authoritative (Priority 1), which
  **disables GoldenMatch's own auto-config**. So the brain must NOT hand-build a
  `GoldenMatchConfig` — that would replace the controller (RuntimeProfile +
  ComplexityProfile + 7-rule table) with a dumber config. GoldenMatch keeps
  owning matchkeys / scorers / thresholds.
- **Signals from one up-front columnar pass** (not two-phase re-planning, not a
  redundant scan). On a **local** frame this is a full per-column `null_count`
  pass (a light Arrow-backed aggregate). On an **engine-resident** frame
  (`ctx.df is None`, e.g. `DuckDBFrame`) it profiles to **zeros**, the same
  degraded contract slice-1's runtime profile already uses; we do not force
  materialization just to profile.
- **Refuse = size-gated raise**, mirroring GoldenMatch: red band AND
  `n_rows >= 100_000` raises; red below the threshold warns and proceeds on the
  safe default; non-red proceeds on the chosen plan.

### 2.1 Explicitly deferred (each is its own slice)

- **Scale-hint merge into GM auto-config.** Real scale routing (e.g. throughput
  tier at large N) needs a `match.py` change so a brain *hint* MERGES with
  GoldenMatch's auto-config instead of replacing it. Not this slice.
- **PII/redact stage.** There is no registered PII stage to route to today
  (PII handling is reactive via `decisions.py:pii_router`, which consumes
  `goldencheck.scan` findings post-hoc). A net-new stage is its own design, so
  `ComplexityProfile` does **not** carry PII signals this slice (YAGNI — no rule
  would consume them).
- **`goldenpipe-core` Rust port + TS-WASM parity.** Python leads; TS reconciles
  later. The TS pipe-parity fixture stays aimed at the shared static engine
  (slice-1's emitter fix), so this slice does not touch cross-surface parity.

### 2.2 Cut during spec review: `weak_duplication_skip`

The design originally added a `weak_duplication_skip` rule that dropped dedupe
when a `duplication_hint = 1 - n_unique()/n_rows` signal was ~0. **Removed.**
`df.n_unique()` measures *whole-row exact* uniqueness, which is an unsound proxy
for "no fuzzy duplicates": the whole job of `goldenmatch.dedupe` is finding
near-duplicate entities among rows that are almost never byte-identical (a
surrogate id column, a middle initial, or a typo all make rows exact-unique
while the table is full of duplicate people). The rule would drop dedupe exactly
on legitimate dedupe inputs. There is no cheap, sound up-front proxy for "no
fuzzy dups" (a real estimate needs blocking/collision counting — dedupe's own
job), so the rule and the `duplication_hint` signal are cut. This slice ships
the sound refuse-core; a genuine skip signal, if one is ever found, is future
work.

## 3. Portable decision core — `goldenpipe/autoconfig_planner.py`

No Polars, no Pydantic. New/changed:

```python
@dataclass(frozen=True)
class ComplexityProfile:
    """Data-derived signals from one columnar pass. Zeros = unknown
    (engine-resident frame not profiled this slice)."""
    max_null_density: float    # 0..1, worst column's null fraction
    mean_null_density: float   # 0..1, mean across columns


@dataclass(frozen=True)
class PlannerInput:
    """Everything a rule sees: cheap runtime signals + deeper complexity."""
    runtime: PipeProfile
    complexity: ComplexityProfile


GREEN_THRESHOLD = 0.7
AMBER_THRESHOLD = 0.4


def band_of(confidence: float) -> str:
    """Map a confidence float to a traffic-light band (Rust-portable strings)."""
    if confidence >= GREEN_THRESHOLD:
        return "green"
    if confidence >= AMBER_THRESHOLD:
        return "amber"
    return "red"
```

- `Predicate` / `Action` change from `Callable[[PipeProfile], ...]` to
  `Callable[[PlannerInput], ...]`.
- `plan_pipeline(inp: PlannerInput, rules=None) -> PipePlan` (parameter renamed
  from `profile`).
- `_default_plan(inp)` and `default_evidence(inp)` migrate to read
  `inp.runtime.*`; `default_evidence` additionally records `max_null_density`
  so the band is explainable.
- `PipePlan` is unchanged (still `stages`, `rule_name`, `confidence`,
  `evidence`). The band is *derived* via `band_of(plan.confidence)`, not stored —
  keeps the core numeric and the Rust port trivial.

## 4. Rules — `goldenpipe/autoconfig_planner_rules.py`

Ordered, first-match. All predicates read `PlannerInput`. **Stage names are the
EXACT dotted registry names** (`plan_to_config` drops any `PlannedStage.name`
not in the registry — the slice-1 inert-rule footgun). Reuse the exact tuples
the shipped `_pathological_plan` / `_default_plan` already use.

| # | Rule | Predicate | Plan (exact names) | Confidence |
|---|------|-----------|--------------------|-----------|
| 1 | `pathological` (kept) | `runtime.n_rows <= 1` | `goldencheck.scan`, `goldenflow.transform` | 1.0 |
| 2 | `confident_schema` (kept) | `runtime.inferred_domain is not None and runtime.domain_confidence >= 0.5` | `infer_schema`, `goldencheck.scan`, `goldenflow.transform`, `goldenmatch.dedupe` | `domain_confidence` |
| 3 | `low_confidence` (**new, RED source**) | `runtime.inferred_domain is None and complexity.max_null_density > RED_NULL_DENSITY` | `goldencheck.scan`, `goldenflow.transform`, `goldenmatch.dedupe` (safe default) | 0.3 (RED) |
| 4 | `default` (kept, in `plan_pipeline`) | — | `goldencheck.scan`, `goldenflow.transform`, `goldenmatch.dedupe` | 0.7 |

Constant: `RED_NULL_DENSITY = 0.6` (module-level, named).

**Ordering rationale (documented in code):**
- `low_confidence` is the sole RED source: no usable signal (`domain is None`)
  **and** the data is mostly empty (`max_null_density > 0.6`) means running the
  full dedupe pipeline is likely to produce garbage clusters. It still returns
  the safe default plan (so small inputs proceed) but tags it RED so the glue can
  refuse at scale.
- It sits after the positive rules (`pathological`, `confident_schema`) — a
  clear positive signal wins — and before the plain `default`. With
  `weak_duplication_skip` cut, nothing shadows it: the garbage case
  (`domain None`, high null) reaches rule 3 and gets RED regardless of row
  distinctness.

`DEFAULT_RULES = (rule_pathological, rule_confident_schema, rule_low_confidence)`.

## 5. Host glue — `goldenpipe/autoconfig_glue.py`

- **`profile_complexity(ctx: PipeContext) -> ComplexityProfile`** — one pass:
  - `ctx.df is None` (engine-resident or no data) **or** `n_rows == 0`: return
    `ComplexityProfile(0.0, 0.0)` (unknown; not profiled this slice).
  - Local frame: `nulls = df.null_count()` (a 1-row per-column frame) → per-column
    null fraction `count / n_rows`; `max_null_density = max(fractions)`,
    `mean_null_density = mean(fractions)`.
- **`build_planner_input(ctx) -> PlannerInput`** — assembles
  `PlannerInput(runtime=profile_context(ctx), complexity=profile_complexity(ctx))`.
  `profile_context` and `profile_complexity` stay independently testable.
- **`enforce_confidence(plan: PipePlan, runtime: PipeProfile) -> None`** —
  ```python
  if band_of(plan.confidence) == "red":
      if runtime.n_rows >= REFUSE_ROW_THRESHOLD:
          raise PipeNotConfidentError(
              f"auto-config not confident (rule={plan.rule_name}, "
              f"confidence={plan.confidence}) on {runtime.n_rows} rows; "
              f"supply an explicit pipeline config or reduce the input size. "
              f"evidence={plan.evidence}"
          )
      logger.warning("auto-config low confidence (rule=%s) on %d rows; "
                     "proceeding on safe default plan", plan.rule_name,
                     runtime.n_rows)
  ```
  `REFUSE_ROW_THRESHOLD = 100_000`.
- `plan_to_config` is unchanged.

**`PipeNotConfidentError`** — a new goldenpipe-local exception (in
`goldenpipe/errors.py`). goldenpipe-local so the pipeline does not take a hard
`goldenmatch` dependency just to name the error; the name parallels GoldenMatch's
`ControllerNotConfidentError` for a consistent suite-wide story.

## 6. Pipeline wiring — `goldenpipe/pipeline.py`

`_plan_config(self, ctx)` becomes:

```python
from goldenpipe.autoconfig_glue import (
    build_planner_input, enforce_confidence, plan_to_config,
)
from goldenpipe.autoconfig_planner import plan_pipeline

inp = build_planner_input(ctx)
plan = plan_pipeline(inp)
self._last_plan = plan
enforce_confidence(plan, inp.runtime)   # may raise PipeNotConfidentError
return plan_to_config(plan, self._registry.list_all(), self._identity_opts)
```

- The raise propagates out of `run()`: `_plan_config` is called at
  `pipeline.py:106`, *before* the `Resolver.resolve` try-block at line 108, so the
  refuse is a loud exception, not a swallowed FAILED `PipeResult`. This matches
  GoldenMatch: refuse is not a silent fallback. (Verified against shipped
  `run()`.)
- `run()`'s docstring documents that it raises `PipeNotConfidentError` on
  RED-at-scale.
- `_auto_config` stays untouched. It is `_auto_config(self)` (no `ctx`) and is
  called by `core/_planner_json.py` (the goldenpipe-core Rust parity bridge) and
  `tests/test_pipeline.py`; those rely on the static default. Same constraint as
  slice 1.

## 7. Testing (all box-runnable pure Python)

Interpreter `D:/show_case/goldenmatch/.venv/Scripts/python.exe`, `PYTHONPATH`
with `;` separator (Windows native), `POLARS_SKIP_CPU_CHECK=1`. `ruff check` all
touched files. Core + glue + integration tests need only `polars` + `infermap`
importable (the slice-1 `test_autoconfig_glue.py` pattern already relies on
`infermap.detect_domain_detailed`); they do NOT import `goldenflow`/`goldenmatch`
(the integration tests use a stub registry).

**Core (`tests/test_autoconfig_planner.py`, MIGRATE + extend):**
- The existing tests call `plan_pipeline(_profile(...))` at six sites and use an
  inline `lambda p: p.n_rows == 100` predicate reading `PipeProfile` directly.
  All of these must be **migrated** to the new `PlannerInput` signature — add a
  `_planner_input(**kw)` helper wrapping `runtime=_profile(...)` +
  `complexity=ComplexityProfile(...)` and rewrite each call site + the lambda to
  read `inp.runtime.*`.
- New: `ComplexityProfile` / `PlannerInput` frozen; `band_of` boundaries
  (0.7→green, 0.69→amber, 0.4→amber, 0.39→red); each rule fires in order
  (pathological, confident_schema, low_confidence → RED band + safe-default
  stages, default); `low_confidence` reachable for the garbage `PlannerInput`.

**Glue (`tests/test_autoconfig_glue.py`, extend):**
- `profile_complexity`: null-heavy column → correct `max_null_density`;
  no-null df → zeros; `ctx.df is None` → zeros; `n_rows == 0` → zeros.
- `enforce_confidence`: RED (`confidence=0.3`) + `n_rows >= 100_000` raises
  `PipeNotConfidentError`; RED + small warns and returns None; green/amber
  returns None.

**Integration (`tests/test_autoconfig_glue.py`, extend, `_registry_with` helper):**
- Garbage df (no detectable domain, `max_null_density > 0.6`) synthesized at
  `n_rows >= 100_000` → `_plan_config` raises `PipeNotConfidentError`; the same
  shape under the threshold → proceeds on the safe default (`_last_plan.rule_name
  == "low_confidence"`) and warns.
- Existing slice-1 integration tests still pass (confident_schema includes
  infer_schema; pathological skips dedupe; order preserved).

## 8. Non-goals / limitations (documented)

- Engine-resident (`DuckDBFrame`) inputs are not complexity-profiled this slice
  (complexity = zeros → `low_confidence` can't fire → never refuses). Acceptable;
  profiling engine frames without forced materialization is future work tied to
  the scale-hint slice.
- Rules return whole-plan lists (slice-1 pattern); decisions don't compose. Not a
  problem for this rule set (all positive rules + one RED refuse), but noted as
  the shape a future composable-planning refactor would change.
- No cross-surface (TS) work; no Rust port. Parity fixture untouched.

## 9. File touch list

- `goldenpipe/autoconfig_planner.py` — add `ComplexityProfile`, `PlannerInput`,
  `band_of` + thresholds; migrate `Predicate`/`Action`/`plan_pipeline`/
  `_default_plan`/`default_evidence` to `PlannerInput`.
- `goldenpipe/autoconfig_planner_rules.py` — migrate rule signatures to
  `PlannerInput`; add `rule_low_confidence` + `RED_NULL_DENSITY`; extend
  `DEFAULT_RULES`.
- `goldenpipe/autoconfig_glue.py` — add `profile_complexity`,
  `build_planner_input`, `enforce_confidence`; import `PipeNotConfidentError`.
- `goldenpipe/errors.py` (new) — `PipeNotConfidentError`.
- `goldenpipe/pipeline.py` — `_plan_config` builds `PlannerInput`, calls
  `enforce_confidence`; `run()` docstring notes the raise.
- `tests/test_autoconfig_planner.py` — **migrate** the six `plan_pipeline` call
  sites + inline lambda to `PlannerInput`; add new-rule / band tests.
- `tests/test_autoconfig_glue.py` — extend with `profile_complexity`,
  `enforce_confidence`, and refuse integration tests.
