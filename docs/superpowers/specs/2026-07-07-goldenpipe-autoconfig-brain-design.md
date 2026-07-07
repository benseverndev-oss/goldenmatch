# goldenpipe auto-config brain — slice 1 (Python prototype) design

**Date:** 2026-07-07
**Status:** Approved (design)
**Branch:** `feat/goldenpipe-autoconfig-brain` off `origin/main`.

## 1. Goal & the arc this fits

Give goldenpipe a **plan-first "brain"** analogous to GoldenMatch's auto-config
controller: instead of statically assembling `check → flow → dedupe`, profile the
input data up front, run a rule table, and produce a `PipePlan` (which stages, in
what order, with what config) stamped with the rule that fired, a confidence
score, and the evidence that drove it.

This is **slice 1 of the standard Rust-thesis workflow**: prototype the feature in
Python (fast iteration, box-runnable TDD), then — once the rules stabilize —
harden the decision core into a pyo3-free `goldenpipe-core` Rust kernel (source of
truth) with Python-native + TS-WASM as conforming surfaces. GoldenMatch's own
auto-config took exactly this path (Python `autoconfig_planner` → `autoconfig-core`
Rust → `autoconfig-wasm`); goldenpipe follows it. **Slice 1 ships only the Python
prototype**; the Rust port + TS parity are later slices.

Design consequence baked in from day one: the **decision core is written
portable** — the profile→plan rule engine (`PipeProfile` → `PipePlan` via pure
predicates) is free of Polars/pandas/Pydantic and Python-only idioms, so the later
Rust port is a mechanical translation. The Polars-dependent *profiling* and the
Pydantic `PipelineConfig` *materialization* are host glue that bracket the portable
core (the "plain-struct-in, plain-struct-out" kernel boundary).

## 2. Current state (the gap)

- `pipeline.py::_auto_config()` statically lists the registry stages
  `["goldencheck.scan", "goldenflow.transform", "goldenmatch.dedupe"]` (+ optional
  `goldenmatch.identity_resolve` when identity opts are supplied) and returns
  `PipelineConfig(pipeline="auto", stages=[...])`. It inspects **nothing** about
  the data.
- `decisions.py` (`severity_gate`, `pii_router`, `row_count_gate`) is **reactive**
  — it routes *between* stages based on artifacts already produced (findings,
  input_rows). It is not a plan-time decision.
- `run(source=, df=, duckdb_con=/table=)` loads the data into `ctx` (`ctx.df`, or
  the engine-resident `ctx.frame` + `ctx.metadata["input_rows"]`) **before**
  `_auto_config()` is called — so plan-time profiling signals are available.

The brain fills the plan-first gap. The reactive `decisions.py` layer stays as-is:
the brain decides the **up-front shape** from cheap signals; `decisions.py` handles
**post-stage routing** (e.g. PII → PPRL, which can only be known after `scan`
runs). They are complementary, not competing.

## 3. The portable decision core (the future Rust kernel)

### `PipeProfile` — frozen dataclass, portable (no Polars/Pydantic)
Cheap, up-front signals only — nothing that requires running a stage:
```python
@dataclass(frozen=True)
class PipeProfile:
    n_rows: int
    n_cols: int
    column_names: tuple[str, ...]
    dtypes: tuple[str, ...]              # per-column dtype names (sampled), aligned to column_names
    inferred_domain: str | None          # from a cheap detect_domain_detailed on the columns
    domain_confidence: float             # 0.0 when no domain inferred
```

### `PipePlan` — frozen dataclass, portable (goldenpipe's `ExecutionPlan` analog)
```python
@dataclass(frozen=True)
class PlannedStage:
    name: str
    config: dict          # per-stage config (e.g. {"domain": "finance"} for infer_schema)

@dataclass(frozen=True)
class PipePlan:
    stages: tuple[PlannedStage, ...]
    rule_name: str        # which rule fired (evidence) — or "default"
    confidence: float     # 0..1
    evidence: dict        # the signals that drove the decision (n_rows, domain, ...)
```

### `PipePlannerRule` + `plan_pipeline` — pure, deterministic
```python
Predicate = Callable[[PipeProfile], bool]
Action    = Callable[[PipeProfile], PipePlan]

@dataclass(frozen=True)
class PipePlannerRule:
    rule_name: str
    predicate: Predicate
    action: Action

def plan_pipeline(profile: PipeProfile, rules: Sequence[PipePlannerRule] = DEFAULT_RULES) -> PipePlan:
    for rule in rules:
        if rule.predicate(profile):
            return rule.action(profile)   # action stamps rule.rule_name
    return _default_plan(profile)         # rule_name="default"
```
`plan_pipeline` + the rules + the structs are the pyo3-free-portable kernel — the
thing that becomes `goldenpipe-core::plan_pipeline` in a later slice.

## 4. The rule table (slice 1 — small, honest, real)

Ordered; first match wins. All predicates read only cheap `PipeProfile` signals.

| # | rule_name | predicate | action (plan) | confidence |
| --- | --- | --- | --- | --- |
| 1 | `pathological` | `n_rows <= 1` | `[scan, flow]` — skip dedupe (nothing to dedupe with ≤1 row); the proactive, plan-visible form of `row_count_gate` | `1.0` |
| 2 | `confident_schema` | `domain_confidence >= 0.5` | `[infer_schema(domain=inferred_domain), scan, flow, dedupe]` — run schema inference (pinned to the detected domain) so downstream stages get typed columns | `domain_confidence` |
| 3 | `default` (fallthrough) | — | `[scan, flow, dedupe]` — the current static shape (no infer_schema; low-value guessing when the domain isn't confident) | `0.7` |

Rules only include a stage when the registry actually has it (checked at
materialization, §5) — so a checkout missing `infer_schema` degrades to the
`default` shape without error. `identity_resolve` (opt-in via identity opts) is
appended at materialization exactly as today, orthogonal to the rule.

> **Why these three:** they cover the genuinely new behavior worth prototyping —
> the brain proactively **adds `infer_schema` when it will help** (confident
> domain) and **skips it when it won't**, and reflects the tiny-data dedupe-skip
> in the plan up front. That's a real capability the static `_auto_config` never
> had, testable on cheap signals, without needing any stage to run first. More
> rules (complexity/pair-count estimation, quality-driven flow tuning) are later
> slices once the shape is proven.

## 5. Host glue (stays Python; NOT ported to Rust)

### `profile_context(ctx) -> PipeProfile`
Builds the portable `PipeProfile` from the loaded context:
- **Materialized df** (`ctx.df is not None`): `n_rows = len(df)`, `column_names`,
  per-column `dtypes` (from `df.schema`), and a cheap
  `infermap.detect_domain_detailed({"columns": column_names})` for
  `inferred_domain`/`domain_confidence`. (InferMap is already a goldenpipe dep;
  detect is column-name-only — no row scan.)
- **Engine-resident** (`ctx.frame` set, df not materialized): build a minimal
  profile from `ctx.metadata["input_rows"]` (n_rows) with `inferred_domain=None`,
  `domain_confidence=0.0` — a **documented degradation** that avoids forcing
  materialization just to plan. (Column names could come from a cheap engine
  `DESCRIBE` in a later slice; slice 1 keeps it minimal.)

### `plan_to_config(plan, available, identity_opts) -> PipelineConfig`
Converts the portable `PipePlan` into the Pydantic `PipelineConfig`: for each
`PlannedStage` whose `name` is in `available` (registry), emit
`StageSpec(use=name, config=stage.config)`; append `identity_resolve` when
identity opts are supplied and discoverable (unchanged from today); return
`PipelineConfig(pipeline="auto", stages=[...])`.

## 6. Wiring

`_auto_config(self)` → `_auto_config(self, ctx)`:
```python
def _auto_config(self, ctx: PipeContext) -> PipelineConfig:
    profile = profile_context(ctx)
    plan = plan_pipeline(profile)
    self._last_plan = plan            # surface for reporting/telemetry
    return plan_to_config(plan, self._registry.list_all(), self._identity_opts)
```
Called in `run()` as `config = self._config or self._auto_config(ctx)` (ctx is
already built + loaded at that point). An explicit user `config` still bypasses the
planner entirely — **fully backward-compatible**. The `PipePlan` is stashed on the
engine (`self._last_plan`) so a later slice can surface it (report/CLI/MCP);
slice 1 does not add a new output surface.

## 7. Testing (box-runnable — the point of Python-first)

- **`plan_pipeline` unit tests** over synthetic `PipeProfile`s: each rule fires on
  its inputs (pathological on n_rows=1; confident_schema on domain_confidence≥0.5
  with a domain; default otherwise); the returned `PipePlan` has the right
  `stages`, `rule_name`, `confidence`, and `evidence`. Pure + deterministic.
- **`profile_context` tests**: a real Polars df → expected `PipeProfile` (n_rows,
  columns, dtypes, and a domain detected for finance-like columns, cross-checking
  the InferMap detect); the engine-resident path → the minimal degraded profile.
- **`plan_to_config` tests**: a `PipePlan` → the expected `PipelineConfig`
  (registry-availability filtering; identity_opts appended).
- **Integration**: `MapEngine(...).run(df=...)` with the planner active produces a
  plan that (a) includes `infer_schema` for a confident-domain df, (b) omits it +
  skips dedupe for a 1-row df, and still runs end to end.

## 8. Out of scope (later slices — honest)

- **Confidence/refuse-on-RED** (`PipeNotConfidentError` analog + a
  RED/AMBER/GREEN model + safe-default degradation). Slice 1 attaches a confidence
  *score* but never refuses.
- **The `goldenpipe-core` Rust port + TS-WASM parity** — the "harden and go
  cross-surface" phase, done *after* the Python rules stabilize.
- **Any signal that needs a stage to run first** (PII, quality findings) — stays
  in the reactive `decisions.py` layer.
- **New output surfaces** (CLI `--explain-plan`, MCP plan resource) — the plan is
  stashed on the engine but not yet surfaced.
- **Complexity/pair-count estimation, quality-driven flow tuning** — later rules.

## 9. Risk assessment

Low. It's additive + backward-compatible (explicit config bypasses it; missing
stages degrade to the default shape). The decision core is small, pure, and
box-testable — exactly the fast-iteration surface the Python-first phase wants.
The one care point is keeping the portable core Polars/Pydantic-free (so the Rust
port stays mechanical); the spec draws that boundary explicitly (§3 vs §5).

## 10. Build environment constraints

- **Box-runnable:** the entire slice is Python — `plan_pipeline`, the rules,
  `profile_context`, `plan_to_config`, and their tests all run locally with the
  goldenpipe venv (real TDD). `ruff check` touched files.
- **Merge-queue repo:** `gh pr merge --auto --squash` without `--delete-branch`;
  benzsevern gh account.
