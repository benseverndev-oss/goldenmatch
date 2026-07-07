# goldenpipe brain — TS runtime wiring (Slice C2) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make TS `goldenpipe.run()` plan-first auto-config (default-on) — a TS profiling layer builds the same `PlannerInput` as Python, `planConfig` runs the brain, and the `pipe_parity` fixture is re-aimed at the brain so Python `run()` == TS `run()`.

**Architecture:** New `autoconfigGlue.ts` (profiling + enforce + planToConfig, mirroring Python `autoconfig_glue.py`) + `errors.ts`; `pipeline.ts` gains `planConfig` and `run()` calls it; the Python emitter re-aims from static config to `goldenpipe.run()` and the fixture regenerates. Rust brain is source of truth; TS conforms via the C1 vectors + this run-level parity.

**Tech Stack:** TypeScript (vitest, CI-only — box OOMs), Python (emitter — box-runnable). Domain detection = InferMap cross-surface-gated `detectDomainDetailed`.

**Spec:** `docs/superpowers/specs/2026-07-07-goldenpipe-brain-ts-runtime-design.md`

---

## Environment & constraints

- Repo `D:/show_case/gg-local-llm`, branch `feat/goldenpipe-brain-ts-runtime` (off fresh main, spec committed).
- **TS is CI-only** (vitest/tsc OOM the box) — TS written against the spec + Python mirror, verified by eye + CI. **The Python emitter + fixture regen IS box-runnable** (Task 2 verifies on the box).
- Python emitter env: `INTERP="D:/show_case/goldenmatch/.venv/Scripts/python.exe"`, `PYTHONPATH="packages/python/goldenpipe;packages/python/infermap;packages/python/goldencheck-types"`, `POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8`.
- GitHub auth: `unset GH_TOKEN; gh auth switch --user benzsevern >/dev/null 2>&1; export GH_TOKEN=$(gh auth token --user benzsevern)`.
- **No `Math.max(...spread)`** — the `ts-no-spread-math-min-max` ast-grep rule is error-severity and runs on every PR. Use loop-max.

## File Structure

| File | Change |
|------|--------|
| `packages/typescript/goldenpipe/src/core/errors.ts` | **new** — `PipeNotConfidentError` |
| `packages/typescript/goldenpipe/src/core/autoconfigGlue.ts` | **new** — profiling + enforce + planToConfig |
| `packages/typescript/goldenpipe/tests/unit/autoconfig-glue.test.ts` | **new** — glue + planConfig unit tests |
| `packages/typescript/goldenpipe/src/core/pipeline.ts` | `planConfig` + `run()` switch |
| `packages/python/goldenpipe/scripts/emit_ts_parity_fixtures.py` | re-aim static → `goldenpipe.run()` |
| `packages/typescript/goldenpipe/tests/fixtures/pipe_parity.json` | regenerated (single_row golden changes) |

**Ordering (green at every commit):** Task 1 (errors + glue + glue-unit-tests) is self-contained. **Task 2 is one atomic commit** — the `run()` switch and the fixture regen are coupled (the fixture *is* the expected TS output), so wiring `run()` to the brain (single_row drops dedupe) and regenerating the fixture must land together, or the pipe-parity test fails between commits. Task 3 ships.

---

### Task 1: `errors.ts` + `autoconfigGlue.ts` + glue unit tests

**Files:**
- Create: `packages/typescript/goldenpipe/src/core/errors.ts`
- Create: `packages/typescript/goldenpipe/src/core/autoconfigGlue.ts`
- Create: `packages/typescript/goldenpipe/tests/unit/autoconfig-glue.test.ts`

- [ ] **Step 1: `errors.ts`**
```ts
/** goldenpipe-local exceptions. */
export class PipeNotConfidentError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PipeNotConfidentError";
  }
}
```

- [ ] **Step 2: `autoconfigGlue.ts`** — the impure host bracket, mirroring `autoconfig_glue.py`. Verify field names against `autoconfigPlanner.ts` (nRows/nCols/columnNames/dtypes/inferredDomain/domainConfidence; maxNullDensity/meanNullDensity; ruleName) and the `models.ts` signatures (`makeStageSpec({use, config})`, `makePipelineConfig({pipeline, stages})`, `Row` from `./index.js`, `StageRegistry.listAll()`).
```ts
/**
 * autoconfigGlue.ts — impure host bracket for the auto-config brain
 * (InferMap detect + Row[] profiling in; PipelineConfig out; refuse-on-RED).
 * TS analogue of goldenpipe/autoconfig_glue.py. The pure decision core is
 * autoconfigPlanner.ts.
 */
import type { Row } from "./index.js";
import {
  planPipeline,
  applyScaleHints,
  bandOf,
  type PipeProfile,
  type ComplexityProfile,
  type PlannerInput,
  type PipePlan,
} from "./autoconfigPlanner.js";
import { detectDomainDetailed } from "infermap";
import { PipeNotConfidentError } from "./errors.js";
import { makePipelineConfig, makeStageSpec, type PipelineConfig } from "./models.js";
import type { StageRegistry } from "./engine/registry.js";

export const REFUSE_ROW_THRESHOLD = 100_000;

export function profileContext(rows: readonly Row[]): PipeProfile {
  if (rows.length === 0) {
    return {
      nRows: 0, nCols: 0, columnNames: [], dtypes: [],
      inferredDomain: null, domainConfidence: 0,
    };
  }
  const columnNames = Object.keys(rows[0]!);
  const det = detectDomainDetailed({ columns: columnNames });
  return {
    nRows: rows.length,
    nCols: columnNames.length,
    columnNames,
    dtypes: [],                       // unused by any rule; never emitted
    inferredDomain: det.domain,
    domainConfidence: det.domain !== null ? det.score : 0,
  };
}

export function profileComplexity(rows: readonly Row[]): ComplexityProfile {
  const nRows = rows.length;
  if (nRows === 0) return { maxNullDensity: 0, meanNullDensity: 0 };
  const columnNames = Object.keys(rows[0]!);
  if (columnNames.length === 0) return { maxNullDensity: 0, meanNullDensity: 0 };
  const fractions = columnNames.map((c) => {
    let nulls = 0;
    for (const r of rows) {
      const v = r[c];
      if (v === null || v === undefined) nulls++;
    }
    return nulls / nRows;
  });
  // Loop-max, NOT Math.max(...spread) (ts-no-spread-math-min-max is error-severity).
  let maxNullDensity = 0;
  for (const f of fractions) if (f > maxNullDensity) maxNullDensity = f;
  return {
    maxNullDensity,
    meanNullDensity: fractions.reduce((a, b) => a + b, 0) / fractions.length,
  };
}

export function buildPlannerInput(rows: readonly Row[]): PlannerInput {
  return { runtime: profileContext(rows), complexity: profileComplexity(rows) };
}

export function enforceConfidence(plan: PipePlan, runtime: PipeProfile): void {
  if (bandOf(plan.confidence) !== "red") return;
  if (runtime.nRows >= REFUSE_ROW_THRESHOLD) {
    throw new PipeNotConfidentError(
      `auto-config not confident (rule=${plan.ruleName}, confidence=${plan.confidence}) ` +
        `on ${runtime.nRows} rows; supply an explicit pipeline config or reduce the input size. ` +
        `evidence=${JSON.stringify(plan.evidence)}`,
    );
  }
  // Low confidence below the threshold: proceed on the safe default plan.
}

export function planToConfig(
  plan: PipePlan,
  available: Record<string, unknown>,
  identityOpts: Record<string, unknown>,
): PipelineConfig {
  const stages = plan.stages
    .filter((s) => s.name in available)
    .map((s) => makeStageSpec({ use: s.name, config: s.config }));
  const IDENTITY = "goldenmatch.identity_resolve";
  if (Object.keys(identityOpts).length > 0 && IDENTITY in available) {
    stages.push(makeStageSpec({ use: IDENTITY, config: identityOpts }));
  }
  return makePipelineConfig({ pipeline: "auto", stages });
}
```
If `Row`'s value type makes `r[c]` a type error, use the actual `Row` index type (it is `Record<string, unknown>`-like — confirm in `models.ts`/`index.ts`).

- [ ] **Step 3: `autoconfig-glue.test.ts`** — the glue unit tests (planConfig tests come in Task 2):
```ts
import { describe, it, expect } from "vitest";
import {
  profileContext,
  profileComplexity,
  enforceConfidence,
} from "../../src/core/autoconfigGlue.js";
import { PipeNotConfidentError } from "../../src/core/errors.js";
import type { PipePlan, PipeProfile } from "../../src/core/autoconfigPlanner.js";

const financeRows = [
  { account_number: "A1", currency: "USD" },
  { account_number: "A2", currency: "EUR" },
];
const personRows = [
  { first_name: "John", last_name: "Smith", email: "j@x.co" },
  { first_name: "Jane", last_name: "Doe", email: "d@x.co" },
];

describe("profileContext", () => {
  it("detects finance domain from columns", () => {
    const p = profileContext(financeRows);
    expect(p.nRows).toBe(2);
    expect(p.columnNames).toEqual(["account_number", "currency"]);
    expect(p.inferredDomain).toBe("finance");
    expect(p.domainConfidence).toBe(1.0);
  });
  it("person columns detect no domain", () => {
    const p = profileContext(personRows);
    expect(p.inferredDomain).toBeNull();
    expect(p.domainConfidence).toBe(0);
  });
  it("empty rows -> zeros", () => {
    const p = profileContext([]);
    expect(p.nRows).toBe(0);
    expect(p.inferredDomain).toBeNull();
  });
});

describe("profileComplexity", () => {
  it("computes null density from explicit nulls", () => {
    const rows = [
      { a: 1, b: null },
      { a: null, b: null },
      { a: 3, b: 4 },
      { a: 4, b: undefined },
    ];
    const c = profileComplexity(rows);
    // a: 1/4 null = 0.25 ; b: 3/4 null = 0.75
    expect(c.maxNullDensity).toBe(0.75);
    expect(c.meanNullDensity).toBeCloseTo(0.5, 10);
  });
  it("no nulls -> zeros", () => {
    expect(profileComplexity(personRows)).toEqual({ maxNullDensity: 0, meanNullDensity: 0 });
  });
  it("empty -> zeros", () => {
    expect(profileComplexity([])).toEqual({ maxNullDensity: 0, meanNullDensity: 0 });
  });
});

function redPlan(): PipePlan {
  return { stages: [], ruleName: "low_confidence", confidence: 0.3, evidence: {} };
}
function greenPlan(): PipePlan {
  return { stages: [], ruleName: "default", confidence: 0.7, evidence: {} };
}
function profile(nRows: number): PipeProfile {
  return { nRows, nCols: 0, columnNames: [], dtypes: [], inferredDomain: null, domainConfidence: 0 };
}

describe("enforceConfidence", () => {
  it("RED at scale throws", () => {
    expect(() => enforceConfidence(redPlan(), profile(100_000))).toThrow(PipeNotConfidentError);
  });
  it("RED below threshold proceeds", () => {
    expect(() => enforceConfidence(redPlan(), profile(99_999))).not.toThrow();
  });
  it("green proceeds", () => {
    expect(() => enforceConfidence(greenPlan(), profile(100_000))).not.toThrow();
  });
});
```

- [ ] **Step 4: Eyeball-verify** field names against `autoconfigPlanner.ts` (the `PipeProfile`/`ComplexityProfile`/`PipePlan` interfaces) and that `detectDomainDetailed({columns})` returns `.domain`/`.score`. Confirm no `Math.max(...spread)` remains. (Cannot run vitest on the box — CI gates it.)

- [ ] **Step 5: Commit**
```bash
git add packages/typescript/goldenpipe/src/core/errors.ts packages/typescript/goldenpipe/src/core/autoconfigGlue.ts packages/typescript/goldenpipe/tests/unit/autoconfig-glue.test.ts
git commit -m "feat(goldenpipe-ts): auto-config profiling glue + PipeNotConfidentError

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

---

### Task 2: wire `planConfig` into `run()` + emitter rework + fixture regen (ONE atomic commit)

**Files:**
- Modify: `packages/typescript/goldenpipe/src/core/pipeline.ts`
- Modify: `packages/python/goldenpipe/scripts/emit_ts_parity_fixtures.py`
- Modify (regenerated): `packages/typescript/goldenpipe/tests/fixtures/pipe_parity.json`
- Modify: `packages/typescript/goldenpipe/tests/unit/autoconfig-glue.test.ts` (append planConfig tests)

Why atomic: switching `run()` to the brain makes single_row drop dedupe; the committed fixture must reflect that in the same commit, or the pipe-parity test fails.

- [ ] **Step 1: `pipeline.ts` — add `planConfig`** (after the `computeAutoConfig` block). Add imports at top: `buildPlannerInput, enforceConfidence, planToConfig` from `./autoconfigGlue.js`; `planPipeline, applyScaleHints` from `./autoconfigPlanner.js`.
```ts
/**
 * planConfig — the brain path (pure-TS; mirrors Python _plan_config). Profiles
 * the rows, runs the rule table + scale hints, refuses if not confident at
 * scale, and materializes the chosen plan into a PipelineConfig.
 */
export function planConfig(
  rows: readonly Row[],
  registry: StageRegistry,
  identityOpts: Record<string, unknown>,
): PipelineConfig {
  const inp = buildPlannerInput(rows);
  let plan = planPipeline(inp);
  plan = applyScaleHints(plan, inp.runtime);
  enforceConfidence(plan, inp.runtime); // may throw PipeNotConfidentError
  return planToConfig(plan, registry.listAll(), identityOpts);
}
```

- [ ] **Step 2: `run()` — switch to `planConfig`.** Change the config line (keep it BEFORE the `Resolver.resolve` try, so a thrown `PipeNotConfidentError` propagates out of `run()`):
```ts
    const config = this.config ?? planConfig([...rows], this.registry, this.identityOpts);
```
Leave `computeAutoConfig`/`computeAutoConfigPure` defined (orphaned wrapper is fine — public API; `computeAutoConfigPure` still backs the engine `auto_config` parity). Do NOT change `DEFAULT_STAGE_ORDER`.

- [ ] **Step 3: Rework the Python emitter** `scripts/emit_ts_parity_fixtures.py`. Re-aim from the static config to `goldenpipe.run()`:
  - In `emit_case`: drop the `static_config` param; change the run line to `result = goldenpipe.run(str(csv_path))`.
  - In `main`: drop `static_config = Pipeline()._auto_config()` and pass-through; call `emit_case(case_id, csv_text, tmp_dir)`.
  - **DELETE the now-unused import** `from goldenpipe.pipeline import Pipeline` (line ~39) — after the rework `Pipeline` has zero uses, and root `pyproject.toml` ruff config selects `F` (→ `F401`), so a dangling import FAILS the `ruff check` gate in Step 4 / Task 3. Keep `import goldenpipe` (still used: `goldenpipe.run`, `goldenpipe.__version__`).
  - Update the module docstring + the `emit_case` comment (they describe the STATIC path): it now emits the BRAIN path (`goldenpipe.run`), so single_row → pathological (drops dedupe); the fixture is the cross-surface brain contract (Python run() == TS run()).

- [ ] **Step 4: Regenerate the fixture (BOX-runnable — verify here)**
```bash
INTERP="D:/show_case/goldenmatch/.venv/Scripts/python.exe"
export PYTHONPATH="packages/python/goldenpipe;packages/python/infermap;packages/python/goldencheck-types"
export POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8
"$INTERP" packages/python/goldenpipe/scripts/emit_ts_parity_fixtures.py
```
Then eyeball `packages/typescript/goldenpipe/tests/fixtures/pipe_parity.json`: `single_row` stages must now be `[load, goldencheck.scan, goldenflow.transform]` (NO dedupe); `people_dupes` + `all_unique` unchanged (`[load, scan, flow, dedupe]`), all `status: "success"`. (Verified on the box during design.) Confirm `ruff check` passes on the emitter:
```bash
"$INTERP" -m ruff check packages/python/goldenpipe/scripts/emit_ts_parity_fixtures.py
```

- [ ] **Step 5: Append planConfig unit tests** to `autoconfig-glue.test.ts`:
```ts
import { planConfig } from "../../src/core/pipeline.js";
import { buildDefaultRegistry } from "../../src/core/adapters/index.js";

describe("planConfig (brain wiring)", () => {
  it("confident_schema prepends infer_schema", () => {
    const rows = [
      { account_number: "A1", currency: "USD" },
      { account_number: "A2", currency: "EUR" },
    ];
    const cfg = planConfig(rows, buildDefaultRegistry(), {});
    expect(cfg.stages.map((s) => s.use)).toEqual([
      "infer_schema", "goldencheck.scan", "goldenflow.transform", "goldenmatch.dedupe",
    ]);
  });
  it("single row is pathological (drops dedupe)", () => {
    const rows = [{ first_name: "Solo", last_name: "Person", email: "s@x.co" }];
    const cfg = planConfig(rows, buildDefaultRegistry(), {});
    expect(cfg.stages.map((s) => s.use)).toEqual(["goldencheck.scan", "goldenflow.transform"]);
  });
});
```

- [ ] **Step 6: Eyeball-verify** run() builds config before the resolve try (throw propagates); `planConfig` imports resolve; the fixture single_row change is consistent with the pathological unit test. (CI gates the TS.)

- [ ] **Step 6b: Audit other run()-driven tests stay green.** Switching `run()` to the brain changes behavior for any zero-config test whose input the brain routes differently. Verified safe during review: `tests/e2e/pipeline-e2e.test.ts` drives `runDf(rows)` zero-config and asserts the exact chain `[load, goldencheck.scan, goldenflow.transform, goldenmatch.dedupe]` — its samples are person columns (`first_name,last_name,email,city,state`, 5 & 3 rows) → domain None + >1 row + no nulls → brain picks `default` = the same chain. Grep for any OTHER `runDf(`/`run(` zero-config test asserting a stage list on a single-row or domain-detecting input; if one exists and would now diverge, update it in THIS commit (or flag). Expected: only `pipe_parity.json`'s single_row changes.

- [ ] **Step 7: Commit (atomic)**
```bash
git add packages/typescript/goldenpipe/src/core/pipeline.ts packages/python/goldenpipe/scripts/emit_ts_parity_fixtures.py packages/typescript/goldenpipe/tests/fixtures/pipe_parity.json packages/typescript/goldenpipe/tests/unit/autoconfig-glue.test.ts
git commit -m "feat(goldenpipe-ts): run() uses the brain (plan-first auto-config) + re-aim pipe_parity

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

---

### Task 3: Ship (CI gates TS; box verified the emitter/fixture)

**Files:** none.

- [ ] **Step 1: Local checks**
```bash
INTERP="D:/show_case/goldenmatch/.venv/Scripts/python.exe"
export PYTHONPATH="packages/python/goldenpipe;packages/python/infermap;packages/python/goldencheck-types"
export POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8
"$INTERP" -m ruff check packages/python/goldenpipe/scripts/emit_ts_parity_fixtures.py
# Re-run the emitter and confirm git sees NO diff (fixture already regenerated in Task 2):
"$INTERP" packages/python/goldenpipe/scripts/emit_ts_parity_fixtures.py
git status --short   # pipe_parity.json should NOT reappear as modified
```
(If the emitter re-run dirties the fixture, the committed fixture is stale — re-add.)

- [ ] **Step 2: Rebase + push + PR**
```bash
unset GH_TOKEN; gh auth switch --user benzsevern >/dev/null 2>&1; export GH_TOKEN=$(gh auth token --user benzsevern)
git fetch origin main -q && git rebase origin/main
git push -u origin feat/goldenpipe-brain-ts-runtime --force-with-lease
gh pr create --repo benseverndev-oss/goldenmatch --base main --head feat/goldenpipe-brain-ts-runtime \
  --title "feat(goldenpipe): TS run() uses the brain (Slice C2 — runtime wiring)" \
  --body "<summary: TS autoconfigGlue (profileContext via cross-surface InferMap detect + profileComplexity null density + enforceConfidence refuse + planToConfig); pipeline.planConfig; run() switches to the brain (default-on); emitter re-aimed at goldenpipe.run() so pipe_parity is the Python-run()==TS-run() brain contract (single_row now pathological). Unit tests cover confident_schema/pathological/null-density/refuse. Rust brain is source of truth; TS conforms. Emitter+fixture box-verified; TS CI-gated. Deferred: null-heavy pipe_parity fixture (parseCsv '' vs Polars null), WASM-routed runtime.>

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

- [ ] **Step 3: Watch the TS + freshness jobs** (box can't run vitest). Key jobs: `typescript` (unit tests + tsc + the reworked pipe-parity test) and `ts_parity_freshness` (re-runs the emitter, compares the fixture).
```bash
gh pr checks <PR#> --repo benseverndev-oss/goldenmatch
# for a failing job:
gh run view <run-id> --repo benseverndev-oss/goldenmatch --log-failed | grep -iE "planConfig|profileContext|profileComplexity|pipe_parity|single_row|infer_schema|toEqual|error TS|Expected|Received|DRIFT|STALE" | head -30
```
Likely causes if red: a field-name slip (tsc), the `Math.max` spread (ast-grep — shouldn't be present), a fixture mismatch (freshness gate — the committed fixture must equal a fresh-install emitter run; person fixtures have no infer_schema/entry-point dependency so this should match), or the confident_schema unit test if the finance columns don't score ≥0.5 (they score 1.0 — verified). Fix, commit, push, re-check.

- [ ] **Step 4: Arm auto-merge + STOP**
```bash
gh pr merge <PR#> --auto --squash   # WITHOUT --delete-branch; if 'strategy set by queue', run: gh pr merge <PR#> --auto
```
Then STOP.

---

## Cross-cutting reminders
- **TS CI-only** (vitest OOMs box); **Python emitter + fixture regen box-runnable** (Task 2 verifies).
- **Loop-max, never `Math.max(...spread)`** (ast-grep error gate).
- **`run()` builds config before the resolve try** — refuse propagates out (rejects the promise), matching Python.
- **`computeAutoConfigPure` stays live** (engine auto_config parity); `computeAutoConfig` wrapper orphans harmlessly — leave it.
- **Task 2 is atomic** — run() switch + emitter + fixture regen + planConfig tests in one commit (coupled).
- **Domain via `detectDomainDetailed({columns})`** — columns-only, byte-identical to Python.
- Deferred: null-heavy pipe_parity fixture; WASM-routed runtime brain.
