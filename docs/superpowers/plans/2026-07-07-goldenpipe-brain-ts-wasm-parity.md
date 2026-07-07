# goldenpipe brain — TS + WASM parity (Slice C1) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the auto-config brain decision core to TypeScript and add the WASM faces, so the pure-TS brain (Leg A) and the Rust-core-via-WASM (Leg B) both reproduce the shared, already-committed vectors. Rust stays source of truth; TS conforms. TS `run()` is unchanged (C2 wires runtime).

**Architecture:** New pure-TS `autoconfigPlanner.ts` mirrors `planner.rs`/`autoconfig_planner.py`; pure JSON bridges in `plannerJsonPure.ts` (Leg A); 3 `#[wasm_bindgen]` faces in `goldenpipe-wasm` + backend interface + loader wiring (Leg B); 3 cases added to each parity test.

**Tech Stack:** TypeScript (vitest, CI-only — box OOMs), Rust wasm-bindgen (CI-only build; rustfmt local). Shared vectors: `packages/rust/extensions/goldenpipe-core/tests/vectors/{plan_pipeline,apply_scale_hints,band_of}.json`.

**Spec:** `docs/superpowers/specs/2026-07-07-goldenpipe-brain-ts-wasm-parity-design.md`

---

## Environment & constraints

- Repo `D:/show_case/gg-local-llm`, branch `feat/goldenpipe-brain-wasm-ts` (off fresh main, spec committed).
- **Box CANNOT run vitest (OOM) or `cargo build`** — TS + wasm-Rust are written against this plan + the committed vectors and validated in CI. Verify TS by eye against the Python brain / the vectors.
- **`rustfmt` the wasm `lib.rs` locally before pushing** (Task 1) — the only locally-runnable Rust check:
  `RUSTFMT="D:/.rustup/toolchains/1.94.0-x86_64-pc-windows-msvc/bin/rustfmt.exe"; "$RUSTFMT" --edition 2021 <file>`.
- GitHub auth: `unset GH_TOKEN; gh auth switch --user benzsevern >/dev/null 2>&1; export GH_TOKEN=$(gh auth token --user benzsevern)`.
- The 3 `pub fn *_json` the wasm faces delegate to already exist in `goldenpipe_core::json` (from #1545).

## File Structure

| File | Change |
|------|--------|
| `packages/rust/extensions/goldenpipe-wasm/src/lib.rs` | 3 `#[wasm_bindgen]` faces |
| `packages/typescript/goldenpipe/src/core/autoconfigPlanner.ts` | **new** — pure-TS brain |
| `packages/typescript/goldenpipe/src/core/wasm/plannerJsonPure.ts` | 3 pure bridge fns + helpers |
| `packages/typescript/goldenpipe/src/core/wasm/backend.ts` | 3 interface methods |
| `packages/typescript/goldenpipe/src/core/wasm/loader.ts` | glue cast + 3 wirings |
| `packages/typescript/goldenpipe/tests/parity/planner-parity.test.ts` | 3 Leg A cases |
| `packages/typescript/goldenpipe/tests/parity/planner-wasm-parity.test.ts` | 3 Leg B families |

---

### Task 1: goldenpipe-wasm brain faces (Rust, CI-gated; rustfmt local)

**Files:** Modify `packages/rust/extensions/goldenpipe-wasm/src/lib.rs`.

- [ ] **Step 1: Add the 3 faces** inside the `#[cfg(target_arch = "wasm32")] mod wasm { ... }` block, after `skip_if_falsy_json`, mirroring the existing 5:
```rust
    #[wasm_bindgen]
    pub fn plan_pipeline_json(input: &str) -> String {
        json::plan_pipeline_json(input)
    }

    #[wasm_bindgen]
    pub fn apply_scale_hints_json(input: &str) -> String {
        json::apply_scale_hints_json(input)
    }

    #[wasm_bindgen]
    pub fn band_of_json(input: &str) -> String {
        json::band_of_json(input)
    }
```

- [ ] **Step 2: rustfmt (local) + verify**
```bash
RUSTFMT="D:/.rustup/toolchains/1.94.0-x86_64-pc-windows-msvc/bin/rustfmt.exe"
"$RUSTFMT" --edition 2021 packages/rust/extensions/goldenpipe-wasm/src/lib.rs
"$RUSTFMT" --edition 2021 --check packages/rust/extensions/goldenpipe-wasm/src/lib.rs   # expect no output
grep -c "wasm_bindgen" packages/rust/extensions/goldenpipe-wasm/src/lib.rs   # expect 8 (5 + 3)
```

- [ ] **Step 3: Commit**
```bash
git add packages/rust/extensions/goldenpipe-wasm/src/lib.rs
git commit -m "feat(goldenpipe-wasm): brain faces (plan_pipeline/apply_scale_hints/band_of)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

---

### Task 2: pure-TS brain + pure bridges + Leg A (CI-gated; write against vectors)

**Files:**
- Create: `packages/typescript/goldenpipe/src/core/autoconfigPlanner.ts`
- Modify: `packages/typescript/goldenpipe/src/core/wasm/plannerJsonPure.ts`
- Modify: `packages/typescript/goldenpipe/tests/parity/planner-parity.test.ts`

- [ ] **Step 1: Create `autoconfigPlanner.ts`** — mirror `planner.rs`/`autoconfig_planner.py` EXACTLY. Internal fields camelCase; the JSON shape (emitted by the bridge in Step 2) is snake_case.
```ts
/**
 * autoconfigPlanner.ts — pure-TS auto-config BRAIN (decision core), ported from
 * autoconfig_planner.py / goldenpipe-core planner.rs. Rust is the source of
 * truth; this is the SP3 TS fallback proven to reproduce the golden vectors.
 * Pure: no InferMap/profiling (that is a PlannerInput the caller supplies).
 */
export interface PipeProfile {
  nRows: number;
  nCols: number;
  columnNames: string[];
  dtypes: string[];
  inferredDomain: string | null;
  domainConfidence: number;
}
export interface ComplexityProfile {
  maxNullDensity: number;
  meanNullDensity: number;
}
export interface PlannerInput {
  runtime: PipeProfile;
  complexity: ComplexityProfile;
}
export interface PlannedStage {
  name: string;
  config: Record<string, unknown>;
}
export interface PipePlan {
  stages: PlannedStage[];
  ruleName: string;
  confidence: number;
  evidence: Record<string, unknown>;
}

const RED_NULL_DENSITY = 0.6;
const CONFIDENT_DOMAIN_THRESHOLD = 0.5;
export const SCALE_ROUTE_MIN_ROWS = 1_000_000;
const THROUGHPUT_RECALL_TARGET = 0.95;
const GREEN_THRESHOLD = 0.7;
const AMBER_THRESHOLD = 0.4;

export function bandOf(confidence: number): "green" | "amber" | "red" {
  if (confidence >= GREEN_THRESHOLD) return "green";
  if (confidence >= AMBER_THRESHOLD) return "amber";
  return "red";
}

function stage(name: string, config: Record<string, unknown> = {}): PlannedStage {
  return { name, config };
}

function defaultDedupeStages(): PlannedStage[] {
  return [stage("goldencheck.scan"), stage("goldenflow.transform"), stage("goldenmatch.dedupe")];
}

// Evidence keys are snake_case, in the exact Python/Rust order.
function defaultEvidence(inp: PlannerInput): Record<string, unknown> {
  return {
    n_rows: inp.runtime.nRows,
    n_cols: inp.runtime.nCols,
    inferred_domain: inp.runtime.inferredDomain,
    domain_confidence: inp.runtime.domainConfidence,
    max_null_density: inp.complexity.maxNullDensity,
    mean_null_density: inp.complexity.meanNullDensity,
  };
}

export function planPipeline(inp: PlannerInput): PipePlan {
  const r = inp.runtime;
  if (r.nRows <= 1) {
    return {
      stages: [stage("goldencheck.scan"), stage("goldenflow.transform")],
      ruleName: "pathological",
      confidence: 1.0,
      evidence: defaultEvidence(inp),
    };
  }
  if (r.inferredDomain !== null && r.domainConfidence >= CONFIDENT_DOMAIN_THRESHOLD) {
    return {
      stages: [
        stage("infer_schema", { domain: r.inferredDomain }),
        stage("goldencheck.scan"),
        stage("goldenflow.transform"),
        stage("goldenmatch.dedupe"),
      ],
      ruleName: "confident_schema",
      confidence: r.domainConfidence,
      evidence: defaultEvidence(inp),
    };
  }
  if (r.inferredDomain === null && inp.complexity.maxNullDensity > RED_NULL_DENSITY) {
    return {
      stages: defaultDedupeStages(),
      ruleName: "low_confidence",
      confidence: 0.3,
      evidence: defaultEvidence(inp),
    };
  }
  return {
    stages: defaultDedupeStages(),
    ruleName: "default",
    confidence: 0.7,
    evidence: defaultEvidence(inp),
  };
}

export function applyScaleHints(plan: PipePlan, runtime: PipeProfile): PipePlan {
  const hasDedupe = plan.stages.some((s) => s.name === "goldenmatch.dedupe");
  if (runtime.nRows < SCALE_ROUTE_MIN_ROWS || !hasDedupe) {
    // No-op: return a NEW, structurally-identical plan (never mutate the input).
    return {
      stages: plan.stages.map((s) => ({ name: s.name, config: { ...s.config } })),
      ruleName: plan.ruleName,
      confidence: plan.confidence,
      evidence: { ...plan.evidence },
    };
  }
  const stages = plan.stages.map((s) =>
    s.name === "goldenmatch.dedupe"
      ? {
          name: s.name,
          config: {
            ...s.config,
            _dedupe_hints: { throughput: { recall_target: THROUGHPUT_RECALL_TARGET } },
          },
        }
      : { name: s.name, config: { ...s.config } },
  );
  return {
    stages,
    ruleName: plan.ruleName,
    confidence: plan.confidence,
    evidence: { ...plan.evidence, scale_hinted: true },
  };
}
```

- [ ] **Step 2: Add the 3 pure bridge fns to `plannerJsonPure.ts`** (append at end). NOTE: this module already imports `PlannedStage` from `../engine/resolver.js` (the engine type) — do NOT import the brain's `PlannedStage`; type only on `PipePlan`/`PipeProfile`/`PlannerInput`.
```ts
import {
  planPipeline,
  applyScaleHints,
  bandOf,
  type PipePlan,
  type PipeProfile,
  type PlannerInput,
} from "../autoconfigPlanner.js";

// snake_case JSON shapes matching the golden vectors.
interface ProfileJson {
  n_rows: number;
  n_cols: number;
  column_names: string[];
  dtypes: string[];
  inferred_domain: string | null;
  domain_confidence: number;
}
interface PlanJson {
  stages: Array<{ name: string; config: Record<string, unknown> }>;
  rule_name: string;
  confidence: number;
  evidence: Record<string, unknown>;
}

function profileFromJson(d: ProfileJson): PipeProfile {
  return {
    nRows: d.n_rows,
    nCols: d.n_cols,
    columnNames: d.column_names,
    dtypes: d.dtypes,
    inferredDomain: d.inferred_domain,
    domainConfidence: d.domain_confidence,
  };
}

function planFromJson(d: PlanJson): PipePlan {
  return {
    stages: d.stages.map((s) => ({ name: s.name, config: s.config })),
    ruleName: d.rule_name,
    confidence: d.confidence,
    evidence: d.evidence,
  };
}

function planToJson(plan: PipePlan): PlanJson {
  return {
    stages: plan.stages.map((s) => ({ name: s.name, config: s.config })),
    rule_name: plan.ruleName,
    confidence: plan.confidence,
    evidence: plan.evidence,
  };
}

export function planPipelineJsonPure(inputStr: string): string {
  const arg = JSON.parse(inputStr) as {
    runtime: ProfileJson;
    complexity: { max_null_density: number; mean_null_density: number };
  };
  const inp: PlannerInput = {
    runtime: profileFromJson(arg.runtime),
    complexity: {
      maxNullDensity: arg.complexity.max_null_density,
      meanNullDensity: arg.complexity.mean_null_density,
    },
  };
  return JSON.stringify(planToJson(planPipeline(inp)));
}

export function applyScaleHintsJsonPure(inputStr: string): string {
  const arg = JSON.parse(inputStr) as { plan: PlanJson; runtime: ProfileJson };
  return JSON.stringify(
    planToJson(applyScaleHints(planFromJson(arg.plan), profileFromJson(arg.runtime))),
  );
}

export function bandOfJsonPure(inputStr: string): string {
  return JSON.stringify(bandOf(JSON.parse(inputStr) as number));
}
```

- [ ] **Step 3: Add the 3 Leg A cases to `planner-parity.test.ts`** — extend the top import from `plannerJsonPure.js` with `planPipelineJsonPure, applyScaleHintsJsonPure, bandOfJsonPure`, and add to the `FAMILIES` array:
```ts
  ["plan_pipeline", planPipelineJsonPure],
  ["apply_scale_hints", applyScaleHintsJsonPure],
  ["band_of", bandOfJsonPure],
```

- [ ] **Step 4: Eyeball-verify against the vectors (no vitest on box).** Open `tests/vectors/plan_pipeline.json` + `apply_scale_hints.json` + `band_of.json` and hand-trace 1-2 cases through the TS logic: confirm the emitted shape (snake_case keys, `rule_name`, `_dedupe_hints`, `scale_hinted`, evidence six keys) matches an `expected`. Confirm no `PlannedStage` name clash (the brain type is NOT imported into plannerJsonPure.ts).

- [ ] **Step 5: Commit**
```bash
git add packages/typescript/goldenpipe/src/core/autoconfigPlanner.ts packages/typescript/goldenpipe/src/core/wasm/plannerJsonPure.ts packages/typescript/goldenpipe/tests/parity/planner-parity.test.ts
git commit -m "feat(goldenpipe-ts): pure-TS brain + Leg A parity (plan_pipeline/apply_scale_hints/band_of)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

---

### Task 3: backend interface + loader + Leg B (CI-gated)

**Files:**
- Modify: `packages/typescript/goldenpipe/src/core/wasm/backend.ts`
- Modify: `packages/typescript/goldenpipe/src/core/wasm/loader.ts`
- Modify: `packages/typescript/goldenpipe/tests/parity/planner-wasm-parity.test.ts`

- [ ] **Step 1: `backend.ts`** — add 3 methods to `interface PipeWasmBackend` (after `skipIfFalsyJson`):
```ts
  planPipelineJson(input: string): string;
  applyScaleHintsJson(input: string): string;
  bandOfJson(input: string): string;
```

- [ ] **Step 2: `loader.ts`** — (a) extend the inline `glue` cast type with the 3 snake_case exports, and (b) add the 3 return-object wirings:
```ts
    // in the `glue` cast type:
    plan_pipeline_json: (s: string) => string;
    apply_scale_hints_json: (s: string) => string;
    band_of_json: (s: string) => string;
```
```ts
    // in the returned backend object:
    planPipelineJson: (s) => glue.plan_pipeline_json(s),
    applyScaleHintsJson: (s) => glue.apply_scale_hints_json(s),
    bandOfJson: (s) => glue.band_of_json(s),
```

- [ ] **Step 3: `planner-wasm-parity.test.ts`** — add to the `dispatch` map (inside `call`):
```ts
      plan_pipeline: b.planPipelineJson,
      apply_scale_hints: b.applyScaleHintsJson,
      band_of: b.bandOfJson,
```
and add the three names to the family loop array:
```ts
  for (const family of ["resolve", "apply_decision", "evaluate_builtin", "auto_config", "skip_if", "plan_pipeline", "apply_scale_hints", "band_of"]) {
```

- [ ] **Step 4: Eyeball-verify** the loader glue names (`plan_pipeline_json`) match the wasm exports from Task 1, and the backend method names (`planPipelineJson`) match the interface + the dispatch map.

- [ ] **Step 5: Commit**
```bash
git add packages/typescript/goldenpipe/src/core/wasm/backend.ts packages/typescript/goldenpipe/src/core/wasm/loader.ts packages/typescript/goldenpipe/tests/parity/planner-wasm-parity.test.ts
git commit -m "feat(goldenpipe-ts): wasm backend brain faces + Leg B parity

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

---

### Task 4: Ship (CI gates TS + WASM)

**Files:** none.

- [ ] **Step 1: Final local checks**
```bash
RUSTFMT="D:/.rustup/toolchains/1.94.0-x86_64-pc-windows-msvc/bin/rustfmt.exe"
"$RUSTFMT" --edition 2021 --check packages/rust/extensions/goldenpipe-wasm/src/lib.rs   # no output = clean
git status --short   # only the 7 intended files across the 3 commits
```
(Cannot run vitest/tsc on the box — CI is the gate.)

- [ ] **Step 2: Rebase + push + PR**
```bash
unset GH_TOKEN; gh auth switch --user benzsevern >/dev/null 2>&1; export GH_TOKEN=$(gh auth token --user benzsevern)
git fetch origin main -q && git rebase origin/main
git push -u origin feat/goldenpipe-brain-wasm-ts --force-with-lease
gh pr create --repo benseverndev-oss/goldenmatch --base main --head feat/goldenpipe-brain-wasm-ts \
  --title "feat(goldenpipe): brain TS + WASM parity (Slice C1)" \
  --body "<summary: pure-TS autoconfigPlanner (planPipeline/applyScaleHints/bandOf) + pure bridges (Leg A) + 3 goldenpipe-wasm faces + backend/loader wiring (Leg B), both replaying the shared vectors from #1545. TS run() unchanged (C2 wires runtime + profiling parity). rustfmt clean on wasm lib.rs. CI-only verification (box can't vitest/cargo build).>

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

- [ ] **Step 3: Watch the TS + goldenpipe_wasm jobs** (box can't run them locally, so watch the first CI run). Leg A runs in the `typescript`/goldenpipe TS test job; Leg B runs in the `goldenpipe_wasm` lane (builds the wasm artifact, un-skips the suite).
```bash
gh pr checks <PR#> --repo benseverndev-oss/goldenmatch
# for a failing job, pull the vitest assertion or tsc error:
gh run view <run-id> --repo benseverndev-oss/goldenmatch --log-failed | grep -iE "plan_pipeline|apply_scale|band_of|toEqual|expected|error TS|Expected|Received|assertion" | head -30
```
If red: common causes — a snake_case key typo (evidence key / `_dedupe_hints` / `scale_hinted`), a missing `glue` cast member (tsc), a family-name mismatch. Fix, rustfmt if Rust touched, commit, push, re-check. Iterate until the TS job and `goldenpipe_wasm` are green.

- [ ] **Step 4: Arm auto-merge + STOP**
```bash
gh pr merge <PR#> --auto --squash   # WITHOUT --delete-branch; if 'strategy set by queue', run: gh pr merge <PR#> --auto
```
Then STOP.

---

## Cross-cutting reminders
- **CI-only** for TS + wasm build; **rustfmt the wasm lib.rs locally** before every push (the #1545/#1546 lesson).
- **snake_case JSON output** everywhere (vector contract): `n_rows`, `rule_name`, `_dedupe_hints`, `scale_hinted`, the six evidence keys.
- **Do NOT import the brain's `PlannedStage`** into `plannerJsonPure.ts` (engine `PlannedStage` already imported) — type on `PipePlan` only.
- **Extend the `glue` cast type** in loader.ts (not just the wiring) or tsc errors.
- TS `run()` untouched; ViaWasm typed wrappers + profiling + pipe_parity are C2.
