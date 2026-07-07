# GoldenPipe scale-hint merge — design

**Status:** approved (design gate)
**Date:** 2026-07-07
**Builds on:** the auto-config brain slices 1 (#1526) + 2 (#1536).
**Sequenced before:** the `goldenpipe-core` Rust port (Slice B), which will port the complete brain including `apply_scale_hints`.

## 1. Goal

Let the auto-config brain route large inputs to GoldenMatch's throughput
(sketch-then-verify) dedup tier **without disabling GoldenMatch's own
auto-config**. At/above a row threshold, the brain attaches a throughput *hint*
to the dedupe stage; the `match.py` adapter forwards it to
`dedupe_df(df, throughput=…)`, which auto-configures **and** applies the hint in
one call (GM merges kwargs with the controller internally). A hint augments GM's
decisions; it does not replace them.

This is a **Python prototype** per the Rust thesis; the new decision-core piece
(`apply_scale_hints`) stays Polars/Pydantic-free so Slice B ports it mechanically.

## 2. Why this shape

- **GM already merges.** `goldenmatch._api.dedupe_df(df, *, config=None, …,
  throughput=…)` auto-configures from its kwargs when `config is None`. So
  `dedupe_df(df, throughput=X)` runs GM's controller **and** enables the
  throughput tier — the merge is GM's existing behavior, not something goldenpipe
  hand-assembles. (`auto_configure_df` likewise takes `throughput`.)
- **The clobber to avoid.** `adapters/match.py` Priority 1 treats *any* dedupe
  `stage_config` as a full `GoldenMatchConfig(**cfg)` override, which bypasses
  GM's controller. Passing throughput *as a full config* would therefore replace
  the controller with a one-setting config. The hint must travel on a channel the
  adapter recognizes as "merge, don't override."
- **A hint is a plan post-transform, not a rule.** Scale routing is orthogonal to
  stage *selection* — it annotates the dedupe stage's config on whatever plan was
  chosen. Modeling it as a rule-table entry would need a whole-plan variant per
  existing rule (combinatorial). A pure post-transform composes with every plan
  (default, confident_schema, low_confidence) and no-ops on plans without dedupe
  (pathological).

## 3. Decision core — `goldenpipe/autoconfig_planner.py`

Pure, portable (no Polars/Pydantic). Add:

```python
SCALE_ROUTE_MIN_ROWS = 1_000_000
_THROUGHPUT_RECALL_TARGET = 0.95


def apply_scale_hints(plan: PipePlan, runtime: PipeProfile) -> PipePlan:
    """Composable post-transform: at/above SCALE_ROUTE_MIN_ROWS, attach a
    throughput hint to the dedupe stage so GoldenMatch routes to its
    sketch-then-verify tier. No-op below the threshold or when the plan has no
    dedupe stage. Pure — returns a new PipePlan, never mutates the input."""
    if runtime.n_rows < SCALE_ROUTE_MIN_ROWS:
        return plan
    if not any(s.name == "goldenmatch.dedupe" for s in plan.stages):
        return plan
    new_stages = tuple(
        PlannedStage(
            s.name,
            {**s.config, "_dedupe_hints": {"throughput": {"recall_target": _THROUGHPUT_RECALL_TARGET}}},
        )
        if s.name == "goldenmatch.dedupe"
        else s
        for s in plan.stages
    )
    return PipePlan(
        stages=new_stages,
        rule_name=plan.rule_name,
        confidence=plan.confidence,
        evidence={**plan.evidence, "scale_hinted": True},
    )
```

- The reserved `_dedupe_hints` key is the merge channel: a nested dict
  `{"throughput": {"recall_target": 0.95}}`. Nested (not flat) so future hints
  (backend, semantic_blocking) slot in without ambiguity.
- `evidence["scale_hinted"] = True` records the decision for telemetry.
- Rationale for `1_000_000`: the throughput tier (#1083) is a high-recall,
  low-cost posture that wins on big data; below ~1M rows per-field FS scoring is
  cheap enough that the sketch tier isn't worth its recall trade-off. Named
  constant, tunable. It sits **above** the 100k refuse threshold, so a large
  *garbage* table refuses (Slice-2 `low_confidence`) before it would ever be
  hinted — only large *clean* tables get throughput.

## 4. Pipeline wiring — `goldenpipe/pipeline.py` `_plan_config`

Insert the post-transform between `plan_pipeline` and the confidence gate:

```python
        inp = build_planner_input(ctx)
        plan = plan_pipeline(inp)
        plan = apply_scale_hints(plan, inp.runtime)
        self._last_plan = plan
        enforce_confidence(plan, inp.runtime)
        return plan_to_config(plan, self._registry.list_all(), self._identity_opts)
```

- `apply_scale_hints` runs first so `_last_plan` reflects the hinted plan.
- Hints don't touch `confidence`, so `enforce_confidence` is unaffected.
- `plan_to_config` passes `PlannedStage.config` through to `StageSpec.config`
  (→ `ctx.stage_config`) unchanged — no change needed there; `_dedupe_hints`
  reaches the dedupe stage as-is.
- Import `apply_scale_hints` in the method's local import block.

## 5. Adapter merge — `goldenpipe/adapters/match.py` `DedupeStage.run`

Add a hint branch at the top of the priority chain. Read (do **not** mutate) the
hint out of `stage_cfg`:

```python
        stage_cfg = ctx.stage_config
        hints = stage_cfg.get("_dedupe_hints") if stage_cfg else None
        if hints:
            # Brain scale-hint: auto-config + hint (do NOT override the
            # controller). GM merges kwargs with its auto-config internally.
            throughput = _throughput_from_hint(hints.get("throughput"))
            logger.info("Applying auto-config scale hint (throughput) from the brain")
            result = _dedupe(ctx.df, throughput=throughput)
        elif stage_cfg:
            # Priority 1: explicit full config from YAML/PipelineConfig (override)
            from goldenmatch.config.schemas import GoldenMatchConfig
            config = GoldenMatchConfig(**stage_cfg)
            logger.info("Using explicit GoldenMatch config from stage spec")
            result = _dedupe(ctx.df, config=config)
        else:
            # Priority 2/3 unchanged (column_contexts → auto-config)
            ...
```

with a small helper:

```python
def _throughput_from_hint(spec: dict | None):
    """Build GoldenMatch's throughput arg from a brain hint. Verified at impl:
    GoldenMatchConfig.throughput is a ThroughputConfig; dedupe_df(throughput=)
    accepts it. Falls back to True if construction is unavailable."""
    from goldenmatch.config.schemas import ThroughputConfig
    opts = spec or {}
    return ThroughputConfig(enabled=True, **opts)
```

- A **full** YAML config (no `_dedupe_hints` key) still hits Priority 1 and
  overrides — unchanged, backward-compatible.
- **Verify at implementation** (do not assume): that `dedupe_df(df,
  throughput=ThroughputConfig(enabled=True, recall_target=0.95))` is accepted and
  runs. If `dedupe_df`'s `throughput=` wants a different type, adapt
  `_throughput_from_hint` (e.g. pass the dict, or `True`). The routing test
  (mocked) does not exercise GM's real handling, so a GM-guarded smoke
  (§6) is what confirms the type.

## 6. Testing (box-runnable Python)

Interpreter `D:/show_case/goldenmatch/.venv/Scripts/python.exe`, `PYTHONPATH`
`;`-joined (`packages/python/goldenpipe;packages/python/infermap;packages/python/goldencheck-types`),
`POLARS_SKIP_CPU_CHECK=1`. `ruff check` touched files.

**Core (`tests/test_autoconfig_planner.py`):**
- `apply_scale_hints` at `n_rows >= 1_000_000` on a plan containing
  `goldenmatch.dedupe` → that stage's config gains
  `{"_dedupe_hints": {"throughput": {"recall_target": 0.95}}}`; `evidence["scale_hinted"] is True`.
- Below the threshold → returns the plan unchanged (identity).
- Plan without a dedupe stage (pathological shape) at scale → unchanged.
- Purity: the input plan's `stages`/`evidence` are not mutated (assert the
  original dedupe stage config is still `{}`).
- Constant is exported and equals `1_000_000`.

**Adapter routing (`tests/test_match_hints.py`, new):**
- Monkeypatch `goldenpipe.adapters.match._dedupe` with a recorder. Call
  `DedupeStage().run(ctx)` with `ctx.df` a tiny frame and
  `ctx.stage_config = {"_dedupe_hints": {"throughput": {"recall_target": 0.95}}}`.
  Assert `_dedupe` was called with `config` unset/None and a truthy `throughput`
  kwarg (NOT a `GoldenMatchConfig`).
- A `ctx.stage_config` that is a full config (e.g. `{"exact": ["email"]}`, no
  `_dedupe_hints`) still routes to the `GoldenMatchConfig` override path (assert
  `_dedupe` called with a `config=` and no `throughput`).
- These need `goldenmatch` importable for `ThroughputConfig` (the host venv has
  it). If `_throughput_from_hint` is changed to avoid the import, adjust.

**GM-guarded smoke (`tests/test_match_hints.py`):**
- If `goldenmatch` importable: run a real `DedupeStage().run(ctx)` with the hint
  on a modest deduplicable frame (a few hundred rows with obvious duplicates) and
  assert it completes and produces `clusters`/`golden` artifacts (no exception) —
  this confirms `dedupe_df(throughput=ThroughputConfig(...))` is accepted
  end-to-end. `pytest.importorskip("goldenmatch")`. If the throughput tier is
  unhappy on small data, the implementer reports and this smoke is downgraded to
  asserting only that no `TypeError` on the `throughput=` arg (routing-level).

**Integration (`tests/test_autoconfig_glue.py`):**
- `_plan_config` on a ≥1M-row frame (a trivial 2-column frame is cheap to build)
  → the returned `PipelineConfig`'s `goldenmatch.dedupe` stage config contains
  `_dedupe_hints`; `_last_plan.evidence["scale_hinted"] is True`.
- A small frame → no `_dedupe_hints` on the dedupe stage.

## 7. Non-goals / limitations

- **Throughput only** this slice. `backend` (ray), `semantic_blocking`, and other
  hints are deferred — the nested `_dedupe_hints` shape accommodates them later.
- Engine-resident (`DuckDBFrame`) inputs: `runtime.n_rows` comes from
  `metadata["input_rows"]` (available), so scale routing *can* fire for them even
  though complexity profiling can't — acceptable and intended (row count is known
  cheaply; the hint is size-driven).
- No cross-surface work; `apply_scale_hints` is ported in Slice B (the Rust
  port). Parity fixture untouched.

## 8. File touch list

- `goldenpipe/autoconfig_planner.py` — add `SCALE_ROUTE_MIN_ROWS`,
  `_THROUGHPUT_RECALL_TARGET`, `apply_scale_hints`.
- `goldenpipe/pipeline.py` — `_plan_config` calls `apply_scale_hints`.
- `goldenpipe/adapters/match.py` — `_dedupe_hints` routing branch +
  `_throughput_from_hint` helper.
- `tests/test_autoconfig_planner.py` — `apply_scale_hints` unit tests.
- `tests/test_match_hints.py` (new) — adapter routing + GM-guarded smoke.
- `tests/test_autoconfig_glue.py` — `_plan_config` scale-hint integration.
