# GoldenPipe auto-config brain — Slice 2 design

**Status:** approved (design gate)
**Date:** 2026-07-07
**Builds on:** Slice 1 (`docs/superpowers/specs/2026-07-07-goldenpipe-autoconfig-brain-design.md`) — the portable `autoconfig_planner` core, the rule table, `autoconfig_glue`, and `_plan_config` (default-on).

## 1. Goal

Make the GoldenPipe auto-config brain reason about *data complexity*, carry a
*confidence band*, and *refuse* when it cannot plan well at scale — closing the
gap between slice 1's "switch statement" and GoldenMatch's controller.

Slice 1 decides *which* stages to run from cheap up-front signals (row count,
InferMap-detected domain). Slice 2 adds:

1. **ComplexityProfile** — deeper, data-derived signals (null density,
   duplication hint) from one columnar pass.
2. **Confidence bands** — every plan carries a green/amber/red band derived from
   its confidence float.
3. **Refuse-on-RED (size-gated)** — a red-band plan on a large input raises
   `PipeNotConfidentError` instead of running an expensive, likely-wrong
   pipeline. Below the threshold it warns and proceeds on the safe default.
4. **One clean orchestration rule** — `weak_duplication_skip`: drop dedupe when
   the data has no detectable duplication.

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
  redundant scan). On a **local** frame this is a full `null_count` / `n_unique`
  pass (Arrow-backed, cheap — no sampling machinery). On an **engine-resident**
  frame (`ctx.df is None`, e.g. `DuckDBFrame`) it profiles to **zeros**, the same
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

## 3. Portable decision core — `goldenpipe/autoconfig_planner.py`

No Polars, no Pydantic. New/changed:

```python
@dataclass(frozen=True)
class ComplexityProfile:
    """Data-derived signals from one columnar pass. Zeros = unknown
    (engine-resident frame not profiled this slice)."""
    max_null_density: float    # 0..1, worst column's null fraction
    mean_null_density: float   # 0..1, mean across columns
    duplication_hint: float    # 0..1; ~0 = all-unique rows, ->1 = heavy dup


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
- `plan_pipeline(inp: PlannerInput, rules=None) -> PipePlan` (was `profile`).
- `_default_plan(inp)` and `default_evidence(inp)` migrate to read
  `inp.runtime.*`; `default_evidence` additionally records
  `max_null_density` and `duplication_hint` so the band is explainable.
- `PipePlan` is unchanged (still `stages`, `rule_name`, `confidence`, `evidence`).
  The band is *derived* via `band_of(plan.confidence)`, not stored — keeps the
  core numeric and the Rust port trivial.

## 4. Rules — `goldenpipe/autoconfig_planner_rules.py`

Ordered, first-match. All predicates read `PlannerInput`. Order and rationale:

| # | Rule | Predicate | Plan | Confidence |
|---|------|-----------|------|-----------|
| 1 | `pathological` (kept) | `runtime.n_rows <= 1` | scan, transform | 1.0 |
| 2 | `confident_schema` (kept) | `domain is not None and domain_confidence >= 0.5` | infer_schema, scan, transform, dedupe | `domain_confidence` |
| 3 | `weak_duplication_skip` (**new**) | `complexity.duplication_hint <= DUP_EPS and runtime.n_rows >= WEAK_DUP_MIN_ROWS` | scan, transform | 0.75 |
| 4 | `low_confidence` (**new**) | `runtime.inferred_domain is None and complexity.max_null_density > RED_NULL_DENSITY` | scan, transform, dedupe (safe default) | 0.3 (RED) |
| 5 | `default` (kept, in `plan_pipeline`) | — | scan, transform, dedupe | 0.7 |

Constants (module-level, named): `DUP_EPS = 0.0` (exact-unique only — sampling
could miss rare dups, so be conservative), `WEAK_DUP_MIN_ROWS = 1000` (don't
bother skipping on tiny inputs), `RED_NULL_DENSITY = 0.6`.

**Ordering rationale (documented in code):**
- `confident_schema` before `weak_duplication_skip`: a strong positive schema
  signal takes its full plan (with dedupe) even if this input happens to have no
  dups. `weak_duplication_skip` therefore fires only for **non-confident-domain**
  data with no duplication — the common "generic table, all unique" case where
  skipping dedupe saves a needless pass at scale. GoldenMatch dedupe on a
  zero-dup confident-domain input is fast and safe, so the minor inefficiency is
  acceptable. Composable per-decision planning (so both `infer_schema` *and*
  skip-dedupe could apply) is a deliberate future refactor, not this slice.
- `low_confidence` is the sole RED source: no usable signal (`domain is None`)
  **and** the data is mostly empty (`max_null_density > 0.6`) means running the
  full dedupe pipeline is likely to produce garbage clusters. It still returns
  the safe default plan (so small inputs proceed) but tags it RED so the glue can
  refuse at scale.

`DEFAULT_RULES = (rule_pathological, rule_confident_schema,
rule_weak_duplication_skip, rule_low_confidence)`.

## 5. Host glue — `goldenpipe/autoconfig_glue.py`

- **`profile_complexity(ctx: PipeContext) -> ComplexityProfile`** — one pass:
  - `ctx.df is None` (engine-resident or no data): return
    `ComplexityProfile(0.0, 0.0, 0.0)` (unknown; not profiled this slice).
  - Local frame: compute from `df` via Polars columnar aggregates —
    `null_count()` per column → `max`/`mean` null density over `n_rows`;
    `duplication_hint = 1 - df.n_unique() / n_rows` (whole-row distinctness).
    Guard `n_rows == 0` → zeros.
- **`profile_context`** is unchanged (still builds `PipeProfile`). A small
  assembler builds `PlannerInput(runtime=profile_context(ctx),
  complexity=profile_complexity(ctx))` — either a new
  `build_planner_input(ctx)` helper here, or inline in `_plan_config`. (Plan
  will pick one; keep `profile_context` and `profile_complexity` independently
  testable.)
- **`enforce_confidence(plan: PipePlan, runtime: PipeProfile) -> None`** —
  ```python
  if band_of(plan.confidence) == "red":
      if runtime.n_rows >= REFUSE_ROW_THRESHOLD:
          raise PipeNotConfidentError(
              f"auto-config not confident (rule={plan.rule_name}, "
              f"confidence={plan.confidence}) on {runtime.n_rows} rows; "
              f"supply an explicit pipeline config or lower the input size. "
              f"evidence={plan.evidence}"
          )
      logger.warning("auto-config low confidence (rule=%s) on %d rows; "
                     "proceeding on safe default plan", plan.rule_name,
                     runtime.n_rows)
  ```
  `REFUSE_ROW_THRESHOLD = 100_000`.
- `plan_to_config` is unchanged.

**`PipeNotConfidentError`** — a new goldenpipe-local exception (e.g. in
`goldenpipe/errors.py` or alongside the glue). goldenpipe-local so the pipeline
does not take a hard `goldenmatch` dependency just to name the error; the name
parallels GoldenMatch's `ControllerNotConfidentError` for a consistent
suite-wide story.

## 6. Pipeline wiring — `goldenpipe/pipeline.py`

`_plan_config(self, ctx)` becomes:

```python
from goldenpipe.autoconfig_glue import (
    enforce_confidence, plan_to_config, profile_complexity, profile_context,
)
from goldenpipe.autoconfig_planner import PlannerInput, plan_pipeline

inp = PlannerInput(
    runtime=profile_context(ctx),
    complexity=profile_complexity(ctx),
)
plan = plan_pipeline(inp)
self._last_plan = plan
enforce_confidence(plan, inp.runtime)   # may raise PipeNotConfidentError
return plan_to_config(plan, self._registry.list_all(), self._identity_opts)
```

- The raise propagates out of `run()` (the `_plan_config` call is *before* the
  `Resolver.resolve` try-block, so it is not swallowed into a FAILED
  `PipeResult`). This matches GoldenMatch: refuse is loud, not a silent fallback.
- `run()`'s docstring documents that it raises `PipeNotConfidentError` on
  RED-at-scale.
- `_auto_config` stays untouched (its `_planner_json` + `test_pipeline` callers
  rely on the static default — same constraint as slice 1).

## 7. Testing (all box-runnable pure Python)

Interpreter `D:/show_case/goldenmatch/.venv/Scripts/python.exe`, `PYTHONPATH`
with `;` separator, `POLARS_SKIP_CPU_CHECK=1`. `ruff check` all touched files.

**Core (`tests/test_autoconfig_planner.py`, extend):**
- `ComplexityProfile` / `PlannerInput` frozen.
- `band_of` boundaries: 0.7→green, 0.69→amber, 0.4→amber, 0.39→red.
- Each rule fires on a crafted `PlannerInput`, in order (first-match): pathological,
  confident_schema, weak_duplication_skip (drops dedupe), low_confidence (RED
  band, safe default stages), default. Confirm `weak_duplication_skip` does NOT
  fire below `WEAK_DUP_MIN_ROWS` and does NOT fire when a confident domain
  precedes it.

**Glue (`tests/test_autoconfig_glue.py`, extend):**
- `profile_complexity`: all-unique df → `duplication_hint == 0`; a df with
  repeated rows → `duplication_hint > 0`; a df with null-heavy column →
  `max_null_density` correct; `ctx.df is None` → zeros; `n_rows == 0` → zeros.
- `enforce_confidence`: RED + `n_rows >= 100_000` raises `PipeNotConfidentError`;
  RED + small warns and returns None; green/amber returns None.

**Integration (`tests/test_autoconfig_glue.py`, extend, `_registry_with` helper):**
- All-unique df (≥ `WEAK_DUP_MIN_ROWS`, no confident domain) → `_plan_config`
  drops dedupe; `_last_plan.rule_name == "weak_duplication_skip"`.
- Garbage df (no domain, `max_null_density > 0.6`) synthesized at
  `n_rows >= 100_000` → `_plan_config` raises `PipeNotConfidentError`; the same
  shape under the threshold → proceeds on the safe default and warns.
- Existing slice-1 integration tests still pass (confident_schema includes
  infer_schema; pathological skips dedupe; order preserved).

## 8. Non-goals / limitations (documented)

- Engine-resident (`DuckDBFrame`) inputs are not complexity-profiled this slice,
  so they never trigger `weak_duplication_skip` or refuse-on-RED (complexity =
  zeros → those rules can't fire). Acceptable; profiling engine frames without
  forced materialization is future work tied to the scale-hint slice.
- Rules return whole-plan lists (slice-1 pattern), so decisions don't compose
  (a confident-domain no-dup input still dedupes). Documented trade-off;
  composable planning is a future refactor.
- No cross-surface (TS) work; no Rust port. Parity fixture untouched.

## 9. File touch list

- `goldenpipe/autoconfig_planner.py` — add `ComplexityProfile`, `PlannerInput`,
  `band_of` + thresholds; migrate `Predicate`/`Action`/`plan_pipeline`/
  `_default_plan`/`default_evidence` to `PlannerInput`.
- `goldenpipe/autoconfig_planner_rules.py` — migrate rule signatures; add
  `rule_weak_duplication_skip`, `rule_low_confidence`; extend `DEFAULT_RULES`.
- `goldenpipe/autoconfig_glue.py` — add `profile_complexity`,
  `enforce_confidence` (+ assembler); import `PipeNotConfidentError`.
- `goldenpipe/errors.py` (new, or co-located) — `PipeNotConfidentError`.
- `goldenpipe/pipeline.py` — `_plan_config` builds `PlannerInput`, calls
  `enforce_confidence`; `run()` docstring notes the raise.
- `tests/test_autoconfig_planner.py`, `tests/test_autoconfig_glue.py` — extend.
