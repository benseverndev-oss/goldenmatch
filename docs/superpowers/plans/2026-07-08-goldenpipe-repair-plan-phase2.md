# GoldenPipe Repair-Plan Phase 2 (Gated Active Application) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When `apply_repairs` is on, make the `goldenflow.transform` adapter apply the Phase-1 repair plan's *fixer* transforms to the flagged columns; byte-identical when off.

**Architecture:** Pure host-side (no `goldenpipe-core` kernel change). A curated FIXERS allowlist + a `repair_transform_specs` converter turn the `repair_plan` artifact into `GoldenFlowConfig.transforms`; the Flow adapter merges them surgically and runs. Cross-surface: Python (`repair_host.py` + `flow.py`) and TS (new `repairHost.ts` producer + `check.ts` wiring + `flow.ts` consumer — Phase 1 wired the producer only in Python).

**Tech Stack:** Python (polars/goldenflow), TypeScript (goldenflow-js), existing goldenpipe adapters.

---

## Box / environment constraints (read before executing)

- **Python is box-runnable** (this is where real red→green TDD happens). TS **cannot** run on the box (tsc/vitest OOM) → write-against-spec + eyeball + CI-verify.
- Python invocation (native Windows, `;` PYTHONPATH separator — note **goldenflow is added** for Flow tests):
  ```bash
  INTERP="D:/show_case/goldenmatch/.venv/Scripts/python.exe"
  export PYTHONPATH="packages/python/goldenpipe;packages/python/goldencheck;packages/python/infermap;packages/python/goldencheck-types;packages/python/goldenflow"
  export POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8
  ```
  Run pytest: `"$INTERP" -m pytest <path> -v`. `cd "D:/show_case/gg-local-llm"` each Bash call (cwd resets).
- `ruff check` every touched Python file before commit.
- **TS strict-mode traps** (Phase 1 CI bit us here): `noUncheckedIndexedAccess` types `arr[i]` as `T | undefined`; a typed interface object missing a member fails tsc. Phase 2 touches `flow.ts`/`check.ts`/`repairHost.ts` (NOT the wasm backend), so lower risk — but guard indexed access and don't add members to shared interfaces.
- Spec: `docs/superpowers/specs/2026-07-08-goldenpipe-repair-plan-phase2-design.md`.
- @superpowers:test-driven-development for each Python task.

## Canonical shared facts

**FIXERS allowlist (identical Python/TS, host policy):**
`fix_mojibake, normalize_unicode, date_parse, email_normalize, email_canonical, name_proper, phone_national, zip_normalize`.
Everything else in a `suggested_transforms` list (all `*_validate`) is an **assertion** → skipped (returning bool would overwrite the column).

**repair_plan artifact shape:** `{"repairs": [{"column","check","type_tag","suggested_transforms":[str],"reason"}]}`.

**TransformSpec:** `{"column": str, "ops": [str]}` (both surfaces). `GoldenFlowConfig.transforms: [TransformSpec]`.

**transform_df is either/or:** non-empty `transforms` → explicit mode (auto-detect OFF); empty → auto-detect.

**Host config asymmetry:**
- Python `flow.py`: `_transform(ctx.df, **stage_cfg)`; base `GoldenFlowConfig` lives at `stage_config["config"]`; applying path calls `transform_df(df, config=merged)` directly.
- TS `flow.ts`: `new TransformEngine(stageConfig)`; base config IS `stageConfig`; transforms at `stageConfig.transforms`.

---

## File structure

- Modify `packages/python/goldenpipe/goldenpipe/repair_host.py` — add `FIXERS`, `repair_transform_specs`, `merge_transforms`.
- Modify `packages/python/goldenpipe/goldenpipe/adapters/flow.py` — gate + surgical merge + apply.
- Test `packages/python/goldenpipe/tests/test_repair_apply.py` (new) — converter + merge (box).
- Test `packages/python/goldenpipe/tests/test_flow_apply.py` (new) — flow adapter gate/merge (box, monkeypatch `_transform`).
- Create `packages/typescript/goldenpipe/src/core/repairHost.ts` — producer glue + FIXERS + `repairTransformSpecs` + `mergeTransforms`.
- Modify `packages/typescript/goldenpipe/src/core/adapters/check.ts` — wire `attachRepairPlan`.
- Modify `packages/typescript/goldenpipe/src/core/adapters/flow.ts` — gate + merge + apply.
- Test `packages/typescript/goldenpipe/tests/unit/repair-apply.test.ts` (new) — CI-only.

---

## Task 1: Python converter — FIXERS + repair_transform_specs + merge_transforms (box TDD)

**Files:**
- Modify: `packages/python/goldenpipe/goldenpipe/repair_host.py`
- Test: `packages/python/goldenpipe/tests/test_repair_apply.py`

- [ ] **Step 1: Write the failing test** `tests/test_repair_apply.py`:

```python
from goldenpipe.repair_host import FIXERS, repair_transform_specs, merge_transforms


def _plan(*items):
    return {"repairs": list(items)}


def test_fixer_only_grouped_and_deduped():
    plan = _plan(
        {"column": "email", "check": "format_detection", "suggested_transforms": ["email_normalize"], "reason": "x"},
        {"column": "email", "check": "pattern_consistency", "suggested_transforms": ["email_canonical", "email_normalize"], "reason": "y"},
    )
    specs, skipped = repair_transform_specs(plan)
    assert specs == [{"column": "email", "ops": ["email_normalize", "email_canonical"]}]  # dedup, order preserved
    assert skipped == []


def test_assertion_ops_are_skipped_not_applied():
    plan = _plan(
        {"column": "iban", "check": "pattern_consistency", "suggested_transforms": ["iban_validate"], "reason": "bad"},
        {"column": "signup", "check": "future_dated", "suggested_transforms": ["date_validate"], "reason": "future"},
    )
    specs, skipped = repair_transform_specs(plan)
    assert specs == []
    assert {"column": "iban", "op": "iban_validate"} in skipped
    assert {"column": "signup", "op": "date_validate"} in skipped


def test_mixed_item_keeps_fixer_skips_assertion():
    # a column flagged for both a fixer and an assertion
    plan = _plan({"column": "dob", "check": "format_detection", "suggested_transforms": ["date_parse", "date_validate"], "reason": "z"})
    specs, skipped = repair_transform_specs(plan)
    assert specs == [{"column": "dob", "ops": ["date_parse"]}]
    assert skipped == [{"column": "dob", "op": "date_validate"}]


def test_fixers_membership():
    assert "email_normalize" in FIXERS and "iban_validate" not in FIXERS


def test_merge_user_first_then_repair_deduped():
    user = [{"column": "email", "ops": ["email_lowercase"]}, {"column": "name", "ops": ["strip"]}]
    repair = [{"column": "email", "ops": ["email_normalize", "email_lowercase"]}, {"column": "zip", "ops": ["zip_normalize"]}]
    merged = merge_transforms(user, repair)
    assert merged == [
        {"column": "email", "ops": ["email_lowercase", "email_normalize"]},  # user-first, dup dropped
        {"column": "name", "ops": ["strip"]},
        {"column": "zip", "ops": ["zip_normalize"]},
    ]
```

- [ ] **Step 2: Run to verify FAIL**

Run: `cd "D:/show_case/gg-local-llm" && "$INTERP" -m pytest packages/python/goldenpipe/tests/test_repair_apply.py -v`
Expected: FAIL — `ImportError: cannot import name 'FIXERS'`.

- [ ] **Step 3: Append to `repair_host.py`** (do NOT touch the existing Phase-1 functions):

```python
# ── Phase 2: active application (fixer allowlist + config conversion) ─────
# FIXERS are transforms that CLEAN in place. Everything else the kernel can
# suggest is a *_validate ASSERTION that returns a boolean series — applying it
# as a column op would overwrite the column with True/False, so it is skipped.
# Host policy, kept identical in repairHost.ts (NOT the parity-gated kernel).
FIXERS = frozenset({
    "fix_mojibake", "normalize_unicode", "date_parse", "email_normalize",
    "email_canonical", "name_proper", "phone_national", "zip_normalize",
})


def repair_transform_specs(plan: dict) -> tuple[list[dict], list[dict]]:
    """repair_plan -> (specs, skipped). specs = [{column, ops}] fixer ops grouped
    per column, deduped, order-preserving. skipped = [{column, op}] assertion ops."""
    by_col: dict[str, list[str]] = {}
    order: list[str] = []
    skipped: list[dict] = []
    for item in plan.get("repairs", []):
        col = item["column"]
        for op in item.get("suggested_transforms", []):
            if op in FIXERS:
                if col not in by_col:
                    by_col[col] = []
                    order.append(col)
                if op not in by_col[col]:
                    by_col[col].append(op)
            else:
                skipped.append({"column": col, "op": op})
    specs = [{"column": c, "ops": by_col[c]} for c in order]
    return specs, skipped


def merge_transforms(user: list[dict], repair: list[dict]) -> list[dict]:
    """Per-column merge, user ops first then repair ops, dedup exact dupes,
    preserving first-seen column + op order."""
    by_col: dict[str, list[str]] = {}
    order: list[str] = []
    for spec in list(user) + list(repair):
        col = spec["column"]
        if col not in by_col:
            by_col[col] = []
            order.append(col)
        by_col[col].extend(spec.get("ops") or [])
    result = []
    for col in order:
        seen: set[str] = set()
        ops: list[str] = []
        for op in by_col[col]:
            if op not in seen:
                seen.add(op)
                ops.append(op)
        result.append({"column": col, "ops": ops})
    return result
```

- [ ] **Step 4: Run to verify PASS**

Run: `cd "D:/show_case/gg-local-llm" && "$INTERP" -m pytest packages/python/goldenpipe/tests/test_repair_apply.py -v`
Expected: PASS (5).

- [ ] **Step 5: ruff + commit**

```bash
"$INTERP" -m ruff check packages/python/goldenpipe/goldenpipe/repair_host.py packages/python/goldenpipe/tests/test_repair_apply.py
cd "D:/show_case/gg-local-llm" && git add packages/python/goldenpipe/goldenpipe/repair_host.py packages/python/goldenpipe/tests/test_repair_apply.py && git commit -m "feat(goldenpipe): repair-plan fixer allowlist + config conversion (Phase 2)"
```

---

## Task 2: Python flow.py — gated surgical application (box TDD)

**Files:**
- Modify: `packages/python/goldenpipe/goldenpipe/adapters/flow.py`
- Test: `packages/python/goldenpipe/tests/test_flow_apply.py`

Test strategy: **monkeypatch `goldenpipe.adapters.flow._transform`** with a spy that records the `config=` it receives and returns a minimal result object. This tests Phase 2's gate/merge/pop logic deterministically without depending on goldenflow's actual transform execution (Task 1 + goldenflow's own suite cover the transforms).

- [ ] **Step 1: Write the failing test** `tests/test_flow_apply.py`:

```python
import types
import pytest
from goldenpipe.models.context import PipeContext, StageStatus


class _SpyResult:
    def __init__(self):
        self.df = "DF_OUT"
        self.manifest = types.SimpleNamespace(records=[])


def _install_spy(monkeypatch):
    calls = {}
    def spy(df, config=None, **kw):
        calls["df"] = df
        calls["config"] = config
        calls["kw"] = kw
        return _SpyResult()
    import goldenpipe.adapters.flow as flowmod
    monkeypatch.setattr(flowmod, "_transform", spy)
    monkeypatch.setattr(flowmod, "HAS_FLOW", True)
    return calls


def _ctx(stage_config=None, repair_plan=None):
    ctx = PipeContext(df="DF_IN")
    ctx.stage_config = stage_config or {}
    if repair_plan is not None:
        ctx.artifacts["repair_plan"] = repair_plan
    return ctx


def _run(ctx):
    from goldenpipe.adapters.flow import TransformStage
    return TransformStage().run(ctx)


def test_gate_off_no_config_calls_autodetect(monkeypatch):
    calls = _install_spy(monkeypatch)
    _run(_ctx())
    # existing behavior: transform_df(df) with no config kwarg
    assert calls["config"] is None and calls["kw"] == {}


def test_gate_off_with_user_config_unchanged(monkeypatch):
    calls = _install_spy(monkeypatch)
    _run(_ctx(stage_config={"config": {"transforms": [{"column": "a", "ops": ["strip"]}]}}))
    # gate absent -> old splat path: transform_df(df, config={...})
    assert calls["config"] == {"transforms": [{"column": "a", "ops": ["strip"]}]}


def test_gate_on_injects_fixer_specs(monkeypatch):
    calls = _install_spy(monkeypatch)
    plan = {"repairs": [{"column": "email", "check": "format_detection", "suggested_transforms": ["email_normalize"], "reason": "x"}]}
    _run(_ctx(stage_config={"apply_repairs": True}, repair_plan=plan))
    assert calls["config"] == {"transforms": [{"column": "email", "ops": ["email_normalize"]}]}


def test_gate_on_all_assertion_falls_through_to_autodetect(monkeypatch):
    calls = _install_spy(monkeypatch)
    plan = {"repairs": [{"column": "iban", "check": "pattern_consistency", "suggested_transforms": ["iban_validate"], "reason": "b"}]}
    ctx = _ctx(stage_config={"apply_repairs": True}, repair_plan=plan)
    _run(ctx)
    # no fixer specs, no user transforms -> do NOT flip to explicit; auto-detect
    assert calls["config"] is None
    # the assertion skip is recorded
    assert "iban_validate" in ctx.reasoning.get("repair_skipped", "")


def test_gate_on_merges_user_and_repair(monkeypatch):
    calls = _install_spy(monkeypatch)
    plan = {"repairs": [{"column": "email", "check": "pattern_consistency", "suggested_transforms": ["email_canonical"], "reason": "y"}]}
    sc = {"apply_repairs": True, "config": {"transforms": [{"column": "email", "ops": ["email_lowercase"]}]}}
    _run(_ctx(stage_config=sc, repair_plan=plan))
    assert calls["config"] == {"transforms": [{"column": "email", "ops": ["email_lowercase", "email_canonical"]}]}


def test_gate_pop_does_not_mutate_ctx_stage_config(monkeypatch):
    _install_spy(monkeypatch)
    sc = {"apply_repairs": True}
    ctx = _ctx(stage_config=sc, repair_plan={"repairs": []})
    _run(ctx)
    assert sc == {"apply_repairs": True}  # original dict untouched (copied before pop)
```

- [ ] **Step 2: Run to verify FAIL**

Run: `cd "D:/show_case/gg-local-llm" && "$INTERP" -m pytest packages/python/goldenpipe/tests/test_flow_apply.py -v`
Expected: FAIL (current flow.py splats `apply_repairs` → the spy gets `apply_repairs` in kw / or wrong config).

- [ ] **Step 3: Rewrite `flow.py` `run()`** (keep imports, `validate`, manifest/enrich tail unchanged). Replace the top of `run`:

```python
    def run(self, ctx: PipeContext) -> StageResult:
        cfg = dict(ctx.stage_config or {})           # COPY: ctx.stage_config IS StageSpec.config
        apply = cfg.pop("apply_repairs", False)       # pop even when False (never leak to transform_df)

        if apply:
            result = self._run_with_repairs(ctx, cfg)
        elif cfg:
            result = _transform(ctx.df, **cfg)
        else:
            result = _transform(ctx.df)

        if hasattr(result, "df"):
            ctx.df = result.df
        if hasattr(result, "manifest"):
            ctx.artifacts["manifest"] = result.manifest
            if "column_contexts" in ctx.artifacts:
                try:
                    from goldenpipe.models.column_context import enrich_contexts_from_flow
                    enrich_contexts_from_flow(ctx.artifacts["column_contexts"], result.manifest)
                except Exception:
                    logger.exception("Failed to enrich column contexts from flow manifest")

        return StageResult(status=StageStatus.SUCCESS)

    def _run_with_repairs(self, ctx: PipeContext, cfg: dict):
        """apply_repairs is on: merge the repair plan's fixer transforms into the
        base GoldenFlowConfig and run explicit mode. Falls through to the normal
        path when there is nothing to apply (keeps auto-detect / byte-identity)."""
        from goldenpipe.repair_host import repair_transform_specs, merge_transforms

        plan = ctx.artifacts.get("repair_plan")
        specs, skipped = repair_transform_specs(plan) if plan else ([], [])
        base = dict(cfg.get("config") or {})
        user_transforms = list(base.get("transforms") or [])

        if not specs and not user_transforms:
            if skipped:
                ctx.reasoning["repair_skipped"] = "; ".join(f"{s['column']}:{s['op']}" for s in skipped)
            # nothing to inject -> behave exactly like the gate-off path
            return _transform(ctx.df, **cfg) if cfg else _transform(ctx.df)

        base["transforms"] = merge_transforms(user_transforms, specs)
        if skipped:
            ctx.reasoning["repair_skipped"] = "; ".join(f"{s['column']}:{s['op']}" for s in skipped)
        logger.info("Applying %d repair transform spec(s); skipped %d assertion(s)", len(specs), len(skipped))
        return _transform(ctx.df, config=base)
```

- [ ] **Step 4: Run to verify PASS**

Run: `cd "D:/show_case/gg-local-llm" && "$INTERP" -m pytest packages/python/goldenpipe/tests/test_flow_apply.py packages/python/goldenpipe/tests/test_adapters.py -v`
Expected: new file PASS (6); `test_adapters.py` still PASS (no regression).

- [ ] **Step 5: ruff + commit**

```bash
"$INTERP" -m ruff check packages/python/goldenpipe/goldenpipe/adapters/flow.py packages/python/goldenpipe/tests/test_flow_apply.py
cd "D:/show_case/gg-local-llm" && git add packages/python/goldenpipe/goldenpipe/adapters/flow.py packages/python/goldenpipe/tests/test_flow_apply.py && git commit -m "feat(goldenpipe): gated active repair application in flow adapter (Phase 2)"
```

---

## Task 3: TS producer — repairHost.ts (write + CI-verify)

**Files:**
- Create: `packages/typescript/goldenpipe/src/core/repairHost.ts`

Mirror `repair_host.py`, reusing the existing `repair.ts` kernel. **Do not reimplement `buildRepairPlan`.** TS `ColumnContext.inferredType` is already a lowercase string (no `.value` dance). TS df is `Row[]` (array of objects), so sampling reads `row[colName]`.

- [ ] **Step 1: Write `repairHost.ts`**

```ts
/**
 * repairHost.ts — TS producer glue for the repair plan (mirror of
 * goldenpipe/repair_host.py). Samples column values from the row array, builds
 * ColumnInputs, calls the pure repair.ts kernel, attaches the advisory artifact.
 * Also holds the Phase-2 FIXERS allowlist + config conversion (host policy,
 * identical to repair_host.py — NOT the parity-gated kernel).
 */
import { buildRepairPlan } from "./repair.js";
import type { ColumnContext } from "./columnContext.js";
import type { PipeContext, Row } from "./models.js";
import type { TransformSpec } from "goldenflow/core";

const SAMPLE_LIMIT = 20;

export function sampleColumn(rows: Row[], col: string, limit = SAMPLE_LIMIT): string[] {
  const out: string[] = [];
  for (const row of rows) {
    const v = (row as Record<string, unknown>)[col];
    if (v === null || v === undefined) continue;
    const s = String(v);
    if (s.trim() === "") continue;
    out.push(s);
    if (out.length >= limit) break;
  }
  return out;
}

export function buildColumnInputs(contexts: ColumnContext[], rows: Row[]): Array<{ name: string; coarse_type: string; samples: string[] }> {
  const names = new Set<string>(rows.length > 0 ? Object.keys(rows[0] as object) : []);
  const cols: Array<{ name: string; coarse_type: string; samples: string[] }> = [];
  for (const ctx of contexts) {
    if (!names.has(ctx.name)) continue;
    cols.push({ name: ctx.name, coarse_type: ctx.inferredType, samples: sampleColumn(rows, ctx.name) });
  }
  return cols;
}

interface RepairItem {
  column: string;
  check: string;
  type_tag: string;
  suggested_transforms: string[];
  reason: string;
}

export function attachRepairPlan(
  ctx: PipeContext,
  findings: unknown[],
  contexts: ColumnContext[],
  rows: Row[],
): { repairs: RepairItem[] } {
  const columns = buildColumnInputs(contexts, rows);
  // buildRepairPlan returns a typed RepairPlan structurally identical to
  // {repairs: RepairItem[]}; call it directly (no JSON round-trip). If repair.ts
  // exports Finding/ColumnInput, use those instead of the casts.
  const plan = buildRepairPlan(findings as never, columns as never) as unknown as { repairs: RepairItem[] };
  ctx.artifacts["repair_plan"] = plan;
  const lines = plan.repairs.map(
    (item) => `repair: ${item.column} (${item.check}) -> ${item.suggested_transforms.join(",")} [${item.reason}]`,
  );
  if (lines.length > 0) ctx.reasoning["repair_plan"] = lines.join("\n");
  return plan;
}

// ── FIXERS allowlist + conversion (identical to repair_host.py) ──────────
export const FIXERS: ReadonlySet<string> = new Set([
  "fix_mojibake", "normalize_unicode", "date_parse", "email_normalize",
  "email_canonical", "name_proper", "phone_national", "zip_normalize",
]);

export function repairTransformSpecs(plan: { repairs: RepairItem[] } | undefined): {
  specs: TransformSpec[];
  skipped: Array<{ column: string; op: string }>;
} {
  const byCol = new Map<string, string[]>();
  const order: string[] = [];
  const skipped: Array<{ column: string; op: string }> = [];
  for (const item of plan?.repairs ?? []) {
    const col = item.column;
    for (const op of item.suggested_transforms ?? []) {
      if (FIXERS.has(op)) {
        let ops = byCol.get(col);
        if (ops === undefined) { ops = []; byCol.set(col, ops); order.push(col); }
        if (!ops.includes(op)) ops.push(op);
      } else {
        skipped.push({ column: col, op });
      }
    }
  }
  const specs: TransformSpec[] = order.map((c) => ({ column: c, ops: byCol.get(c) ?? [] }));
  return { specs, skipped };
}

export function mergeTransforms(user: readonly TransformSpec[], repair: readonly TransformSpec[]): TransformSpec[] {
  const byCol = new Map<string, string[]>();
  const order: string[] = [];
  for (const spec of [...user, ...repair]) {
    let ops = byCol.get(spec.column);
    if (ops === undefined) { ops = []; byCol.set(spec.column, ops); order.push(spec.column); }
    ops.push(...spec.ops);
  }
  return order.map((col) => {
    const seen = new Set<string>();
    const ops: string[] = [];
    for (const op of byCol.get(col) ?? []) if (!seen.has(op)) { seen.add(op); ops.push(op); }
    return { column: col, ops };
  });
}
```

**Note for implementer:** check `repair.ts`'s exact export signature for `buildRepairPlan` — it takes `(findings, columns)` and returns `{repairs: [...]}`. If its param types are strict, adapt the `as never` casts to the real exported types rather than `never`. Confirm `Row` is exported from `models.js` and `TransformSpec` from `goldenflow/core` (grep the existing `flow.ts` import line — it imports `GoldenFlowConfig` from `goldenflow/core`; add `TransformSpec` there).

- [ ] **Step 2: Grep-verify + commit** (no tsc on box)

Verify: `buildRepairPlan` import path matches how other core files import repair (`./repair.js`); `Row`/`ColumnContext`/`PipeContext` import paths match `flow.ts`/`check.ts`; no raw `arr[i]` indexed access without a guard (the only index is `rows[0]` guarded by `rows.length > 0` — under `noUncheckedIndexedAccess` `Object.keys(rows[0] as object)` needs `rows[0]` non-undefined; guard with `rows.length > 0 ? Object.keys(rows[0] as object) : []` — already done, but confirm tsc-acceptable, else use `const first = rows[0]; ... first ? Object.keys(first) : []`).

```bash
cd "D:/show_case/gg-local-llm" && git add packages/typescript/goldenpipe/src/core/repairHost.ts && git commit -m "feat(goldenpipe): TS repair producer + fixer conversion (Phase 2)"
```

---

## Task 4: TS check.ts — wire attachRepairPlan (write + CI-verify)

**Files:**
- Modify: `packages/typescript/goldenpipe/src/core/adapters/check.ts`

- [ ] **Step 1: Read `check.ts`** around the `ctx.artifacts["column_contexts"] = ...` assignment (near line 83) and the final `return { status: StageStatus.SUCCESS }` (near 88).

- [ ] **Step 2: Add the `ColumnContext` type import** at the top of `check.ts` (the reviewer confirmed it is NOT currently imported — omitting this is a `TS2304` in CI):

```ts
import type { ColumnContext } from "../columnContext.js";
```
(Merge into an existing `columnContext.js` import line if one exists.)

- [ ] **Step 3: Insert the attach call** immediately before the final `return`, mirroring `check.py` (advisory, non-fatal):

```ts
    // Advisory repair-plan (Phase 1 producer, TS side). Never throws.
    try {
      const { attachRepairPlan } = await import("../repairHost.js");
      const rp_findings = (ctx.artifacts["findings"] as unknown[]) ?? [];
      const rp_contexts = (ctx.artifacts["column_contexts"] as ColumnContext[]) ?? [];
      if (ctx.df && rp_findings.length > 0 && rp_contexts.length > 0) {
        attachRepairPlan(ctx, rp_findings, rp_contexts, ctx.df);
      }
    } catch {
      /* advisory: never break the scan stage */
    }
```

Prefer a top-of-file `import { attachRepairPlan } from "../repairHost.js";` if the file's style uses static imports (check whether `check.ts` uses dynamic `await import` elsewhere; match the file — a static import is simpler if the module graph allows it. repairHost imports repair.ts + goldenflow/core types only, no cycle with check.ts, so a static import is safe).

- [ ] **Step 4: Grep-verify + commit**

Verify the insertion is inside `run` after `column_contexts` is set, before `return`. Confirm no `noUncheckedIndexedAccess` issue (the `?? []` guards cover it).

```bash
cd "D:/show_case/gg-local-llm" && git add packages/typescript/goldenpipe/src/core/adapters/check.ts && git commit -m "feat(goldenpipe): attach advisory repair_plan in TS check adapter (Phase 2)"
```

---

## Task 5: TS flow.ts — gated active application (write + CI-verify)

**Files:**
- Modify: `packages/typescript/goldenpipe/src/core/adapters/flow.ts`
- Test: `packages/typescript/goldenpipe/tests/unit/repair-apply.test.ts` (new, CI-only)

TS asymmetry: `stageConfig` IS the `GoldenFlowConfig`; transforms at `stageConfig.transforms` (NOT nested under `"config"`).

- [ ] **Step 1: Rewrite the config-building block of `flow.ts` `run()`.** Replace:

```ts
    const stageCfg = ctx.stageConfig;
    const config =
      stageCfg && Object.keys(stageCfg).length > 0
        ? (stageCfg as Partial<GoldenFlowConfig>)
        : undefined;
```

with:

```ts
    const rawCfg: Record<string, unknown> = { ...(ctx.stageConfig ?? {}) };
    const apply = rawCfg["apply_repairs"] === true;
    delete rawCfg["apply_repairs"];

    let config: Partial<GoldenFlowConfig> | undefined;
    if (apply) {
      const { repairTransformSpecs, mergeTransforms } = await import("../repairHost.js");
      const plan = ctx.artifacts["repair_plan"] as { repairs: never[] } | undefined;
      const { specs, skipped } = repairTransformSpecs(plan as never);
      const userTransforms = (rawCfg["transforms"] as TransformSpec[] | undefined) ?? [];
      if (skipped.length > 0) {
        ctx.reasoning["repair_skipped"] = skipped.map((s) => `${s.column}:${s.op}`).join("; ");
      }
      if (specs.length > 0 || userTransforms.length > 0) {
        config = { ...rawCfg, transforms: mergeTransforms(userTransforms, specs) } as Partial<GoldenFlowConfig>;
      } else {
        config = Object.keys(rawCfg).length > 0 ? (rawCfg as Partial<GoldenFlowConfig>) : undefined;
      }
    } else {
      config = Object.keys(rawCfg).length > 0 ? (rawCfg as Partial<GoldenFlowConfig>) : undefined;
    }
```

Add `TransformSpec` to the existing `import type { GoldenFlowConfig } from "goldenflow/core";` line → `import type { GoldenFlowConfig, TransformSpec } from "goldenflow/core";`. The rest of `run()` (engine construction, `ctx.df = ...`, enrich) stays unchanged.

- [ ] **Step 2: Write the CI-only test** `tests/unit/repair-apply.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { repairTransformSpecs, mergeTransforms, FIXERS } from "../../src/core/repairHost.js";

describe("repairTransformSpecs", () => {
  it("keeps fixers grouped+deduped, skips assertions", () => {
    const plan = { repairs: [
      { column: "email", check: "format_detection", type_tag: "email", suggested_transforms: ["email_normalize"], reason: "x" },
      { column: "iban", check: "pattern_consistency", type_tag: "iban", suggested_transforms: ["iban_validate"], reason: "b" },
    ] };
    const { specs, skipped } = repairTransformSpecs(plan as never);
    expect(specs).toEqual([{ column: "email", ops: ["email_normalize"] }]);
    expect(skipped).toEqual([{ column: "iban", op: "iban_validate" }]);
  });
  it("FIXERS excludes validators", () => {
    expect(FIXERS.has("email_normalize")).toBe(true);
    expect(FIXERS.has("iban_validate")).toBe(false);
  });
  it("merges user-first then repair, deduped", () => {
    const merged = mergeTransforms(
      [{ column: "email", ops: ["email_lowercase"] }],
      [{ column: "email", ops: ["email_normalize", "email_lowercase"] }],
    );
    expect(merged).toEqual([{ column: "email", ops: ["email_lowercase", "email_normalize"] }]);
  });
});
```

- [ ] **Step 3: Grep-verify + commit** (no tsc/vitest on box; CI runs them)

Verify: `TransformSpec` imported; `apply_repairs` deleted from `rawCfg` before use; no unguarded indexed access; the dynamic `await import("../repairHost.js")` matches the file (flow.ts `run` is already `async`).

```bash
cd "D:/show_case/gg-local-llm" && git add packages/typescript/goldenpipe/src/core/adapters/flow.ts packages/typescript/goldenpipe/tests/unit/repair-apply.test.ts && git commit -m "feat(goldenpipe): gated active repair application in TS flow adapter (Phase 2)"
```

---

## Task 6: Ship (Phase-1 dependency handling)

- [ ] **Step 1: Check whether Phase 1 (#1577) merged**

```bash
unset GH_TOKEN; gh auth switch --user benzsevern; export GH_TOKEN=$(gh auth token --user benzsevern)
gh pr view 1577 --json state -q .state
```

- [ ] **Step 2a: If MERGED** — rebase Phase 2 onto fresh main so only Phase-2 commits remain:

```bash
git fetch origin
git rebase origin/main
# Phase-1 commits are now in main and drop out of the range; resolve any conflicts
# (unlikely — Phase 2 touches different lines). Re-run the box suite:
export PYTHONPATH="packages/python/goldenpipe;packages/python/goldencheck;packages/python/infermap;packages/python/goldencheck-types;packages/python/goldenflow"
export POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8
"$INTERP" -m pytest packages/python/goldenpipe/tests/test_repair_apply.py packages/python/goldenpipe/tests/test_flow_apply.py packages/python/goldenpipe/tests/test_adapters.py -q
```

- [ ] **Step 2b: If NOT merged** — keep Phase 2 stacked on `feat/goldenpipe-repair-plan`; the PR will target `main` but contain both waves until Phase 1 lands (note this in the PR body). Prefer to WAIT for Phase 1 to merge, then do 2a, to avoid a stacked-PR squash-closure (see repo CLAUDE.md). If waiting isn't viable, open the PR and note the dependency.

- [ ] **Step 3: Push + PR + arm auto-merge, then STOP**

```bash
git push
gh pr create --base main --title "feat(goldenpipe): repair-plan Phase 2 — gated active application" --body "<summary: apply_repairs gate; flow adapter applies fixer transforms from the Phase-1 repair_plan; fixer-only (validators skipped as bool-destructive); cross-surface; byte-identical when off. Links to spec + plan. Note Phase-1 dependency if stacked.>"
# merge-queue repo: NO --delete-branch
gh pr merge <N> --auto --squash
```
Then **STOP** — do not poll CI. Watch that CI covers: python goldenpipe (test_repair_apply, test_flow_apply, test_adapters) + typescript (repair-apply.test.ts, tsc).

---

## Verification summary

- Box-green: `test_repair_apply.py` (converter/merge), `test_flow_apply.py` (gate/pop/merge/fall-through via `_transform` spy), `test_adapters.py` (no regression).
- CI-green: TS `repair-apply.test.ts`, `tsc` over the modified `flow.ts`/`check.ts` + new `repairHost.ts`.
- Byte-identical-when-off: `test_gate_off_*` assert the exact pre-Phase-2 `_transform` call; gate-off path in both adapters is structurally unchanged.
