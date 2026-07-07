# goldenpipe TS `infer_schema` Stage — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the Python goldenpipe `infer_schema` stage to TypeScript, consuming the TS `infermap` package, producing the identical `InferredSchema` + `infer_schema_evidence` artifacts.

**Architecture:** A new TS goldenpipe adapter stage (`infer_schema`) that runs InferMap's `detectDomainDetailed` + `map` and converts the `MapResult` to a shared `goldencheck-types.InferredSchema` — a faithful translation of `stages/infer_schema.py`. Registered but opt-in (not in `DEFAULT_STAGE_ORDER`), matching Python.

**Tech Stack:** TypeScript (goldenpipe adapter + infermap barrel), vitest, `goldencheck-types` shared types.

**Spec:** `docs/superpowers/specs/2026-07-07-goldenpipe-ts-infer-schema-design.md`

**Reference skill:** @superpowers:test-driven-development

---

## Environment & Constraints (READ FIRST)

**Repo:** `D:\show_case\gg-local-llm`, branch `feat/goldenpipe-ts-infer-schema` (off fresh `origin/main`, spec committed).

**Box CANNOT run `tsc`/`vitest`/`tsup`/`pnpm build`** (TS OOMs, CI-only). Box CAN: `node --check` (`.mjs`/`.cjs` only, NOT `.ts`), `python` (to cross-check the Python reference), `git`, `grep`/read, `python -c` JSON validation. **Every TS task is write-against-spec + eye-verify + commit; CI is the first real test.** The Python `infer_schema` reference + its tests ARE box-runnable for cross-checking expected values.

**Reference files (read first):**
- Python stage (the reference): `packages/python/goldenpipe/goldenpipe/stages/infer_schema.py`.
- Python tests (mirror these 7): `packages/python/goldenpipe/tests/test_infer_schema_stage.py`.
- TS goldenpipe stage exemplar: `packages/typescript/goldenpipe/src/core/adapters/check.ts` (`ScanStage` shape).
- TS models: `packages/typescript/goldenpipe/src/core/models.ts` (`Stage`, `PipeContext`, `Row`, `StageResult`, `StageStatus`, `makePipeContext`).
- Registry wiring: `packages/typescript/goldenpipe/src/core/adapters/index.ts` (`buildDefaultRegistry`).
- infermap barrel: `packages/typescript/infermap/src/core/index.ts:5` (`export { detectDomain, DEFAULT_MIN_SCORE } from "./detect.js";`).
- Shared types: `packages/typescript/goldencheck-types/src/index.ts` (`InferredSchema`, `FieldMapping`, `UNMAPPED_TYPE`, `loadDomain`, `DomainPack`).

**Git:** benzsevern (`unset GH_TOKEN; gh auth switch --user benzsevern; export GH_TOKEN=$(gh auth token --user benzsevern)`). Merge-queue — `--auto --squash`, no `--delete-branch`. Trailer:
```
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44
```

---

## File Structure

| File | Change | Action |
| --- | --- | --- |
| `packages/typescript/infermap/src/core/index.ts` | re-export `detectDomainDetailed` from the barrel | Modify |
| `packages/typescript/goldenpipe/package.json` | add `infermap` + `goldencheck-types` deps | Modify |
| `packages/typescript/goldenpipe/src/core/adapters/infer.ts` | new `InferSchemaStage` + `resultToInferredSchema` | Create |
| `packages/typescript/goldenpipe/src/core/adapters/index.ts` | register + export `InferSchemaStage` (NOT in DEFAULT_STAGE_ORDER) | Modify |
| `packages/typescript/goldenpipe/tests/unit/infer-schema-stage.test.ts` | 7-case parity test | Create |
| `pnpm-lock.yaml` | regenerated for the 2 new goldenpipe deps | Modify (generated) |

---

## Task 1: Surface `detectDomainDetailed` from the infermap barrel

**Files:** Modify `packages/typescript/infermap/src/core/index.ts`. Box: eye-review.

The stage imports `detectDomainDetailed` from `"infermap"`, but the barrel only re-exports `detectDomain`. Add it (it's already a public export of `detect.ts`; also aligns with Python's top-level `detect_domain_detailed`).

- [ ] **Step 1: Edit the re-export line.** In `core/index.ts`, change line 5:
```ts
export { detectDomain, DEFAULT_MIN_SCORE } from "./detect.js";
```
to:
```ts
export { detectDomain, detectDomainDetailed, DEFAULT_MIN_SCORE } from "./detect.js";
```

- [ ] **Step 2: Verify.** `grep -n "detectDomainDetailed" packages/typescript/infermap/src/core/index.ts packages/typescript/infermap/src/core/detect.ts` — confirm it's now in the barrel export AND is an `export function` in `detect.ts` (so the re-export resolves). The top-level `index.ts` does `export * from "./core/index.js"`, so it's now reachable as `import { detectDomainDetailed } from "infermap"`.

- [ ] **Step 3: Commit.**
```bash
cd "D:/show_case/gg-local-llm"
git add packages/typescript/infermap/src/core/index.ts
git commit -m "feat(infermap-ts): surface detectDomainDetailed from the barrel (Wave C follow-on)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

**Report:** `DONE`/`BLOCKED`, grep, SHA.

---

## Task 2: Add the two goldenpipe dependencies

**Files:** Modify `packages/typescript/goldenpipe/package.json`; regenerate `pnpm-lock.yaml`. Box: JSON + `pnpm install --lockfile-only` (box-runnable — resolution only, ~seconds, does NOT OOM).

- [ ] **Step 1: Read `package.json`.** `cat packages/typescript/goldenpipe/package.json` — find the `dependencies` block (currently `commander, goldencheck, goldenflow, goldenmatch`).

- [ ] **Step 2: Add both deps** to `dependencies`, matching the existing formatting + alphabetical order:
```json
    "goldencheck-types": "workspace:^",
    "infermap": "workspace:^",
```
(Both are absent today; both are required — `infermap` for the API, `goldencheck-types` for `InferredSchema`/`FieldMapping`/`UNMAPPED_TYPE`/`loadDomain`. Do not rely on transitive resolution.)

- [ ] **Step 3: Validate JSON + regenerate the lockfile.**
```bash
"D:/show_case/goldenmatch/.venv/Scripts/python.exe" -c "import json; json.load(open('packages/typescript/goldenpipe/package.json')); print('package.json OK')"
export COREPACK_INTEGRITY_KEYS=0
pnpm install --lockfile-only 2>&1 | tail -4
git status --short pnpm-lock.yaml
```
Expect: `package.json OK`; `pnpm install` completes (resolves the new workspace links); `pnpm-lock.yaml` shows as modified. If `pnpm install --lockfile-only` fails/OOMs, note it for the controller (CI can regenerate) — but per prior experience `--lockfile-only` is light and works on the box.

- [ ] **Step 4: Commit** (package.json + lockfile together):
```bash
git add packages/typescript/goldenpipe/package.json pnpm-lock.yaml
git commit -m "build(goldenpipe-ts): add infermap + goldencheck-types deps for infer_schema stage

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

**Report:** `DONE`/`BLOCKED`, package.json-OK, lockfile-regen result, SHA.

---

## Task 3: The `InferSchemaStage` adapter (byte-faithful port)

**Files:** Create `packages/typescript/goldenpipe/src/core/adapters/infer.ts`. Box: eye-review (no tsc). Cross-check logic against `infer_schema.py`.

- [ ] **Step 1: Read the Python stage** (`infer_schema.py`) fully — the `_validate_flags`, the 4 branches, `_result_to_inferred_schema`, the `detect_evidence` shapes, the `confidence = detect_score` replace, and the `setdefault("infer_schema_evidence", ...)`.

- [ ] **Step 2: Create `infer.ts`:**
```ts
/**
 * infer_schema — GoldenPipe stage that runs InferMap to label columns.
 * TS port of goldenpipe/stages/infer_schema.py. Produces
 * `inferred_schema` (goldencheck-types.InferredSchema | null) + (on the
 * auto-detect path) `infer_schema_evidence`. Consumes nothing — meant to run
 * before stages that want typed columns. Opt-in (not in DEFAULT_STAGE_ORDER),
 * matching Python.
 *
 * Config (ctx.stageConfig): schema?: InferredSchema | no_infer?: boolean |
 * domain?: string. Precedence: schema > no_infer > domain > auto-detect;
 * the three are mutually exclusive (conflict throws in run()).
 */
import {
  UNMAPPED_TYPE,
  loadDomain,
  type FieldMapping,
  type InferredSchema,
} from "goldencheck-types";
import {
  detectDomainDetailed,
  map,
  DomainPackTarget,
  type MapResult,
} from "infermap";

import type { PipeContext, Stage, StageResult } from "../models.js";
import { StageStatus } from "../models.js";

/** Mirror of Python `_result_to_inferred_schema`. `confidence` is overwritten
 *  by the caller with the detection score. */
function resultToInferredSchema(result: MapResult, domain: string): InferredSchema {
  const fields: Record<string, FieldMapping> = {};
  for (const fm of result.mappings) {
    fields[fm.source] = {
      source_col: fm.source,
      canonical: fm.target, // soft mode sets target = null -> canonical null
      type: fm.target ? fm.target : UNMAPPED_TYPE,
      confidence: fm.confidence,
      evidence: { reasoning: fm.reasoning },
    };
  }
  for (const col of result.unmappedSource) {
    if (!(col in fields)) {
      fields[col] = {
        source_col: col,
        canonical: null,
        type: UNMAPPED_TYPE,
        confidence: 0.0,
        evidence: {},
      };
    }
  }
  const confidence = result.mappings.length
    ? Math.min(...result.mappings.map((m) => m.confidence))
    : 0.0;
  return { domain, fields, confidence };
}

/** Mirror of Python `_validate_flags`: at most one of schema/no_infer/domain. */
function validateFlags(cfg: Record<string, unknown>): void {
  const set =
    (cfg.schema != null ? 1 : 0) +
    (cfg.no_infer ? 1 : 0) +
    (cfg.domain != null ? 1 : 0);
  if (set > 1) {
    throw new Error(
      "conflict: 'schema', 'no_infer', and 'domain' are mutually exclusive. " +
        "Precedence: schema > no_infer > domain > auto-detect.",
    );
  }
}

export const InferSchemaStage: Stage = {
  info: {
    name: "infer_schema",
    produces: ["inferred_schema", "infer_schema_evidence"],
    consumes: [],
  },

  // No precondition: consumes [] and a null df is a valid SUCCESS branch, so
  // validate() must NOT throw. The flag-conflict check lives in run() (mirrors
  // Python's _validate_flags at the top of run()).
  validate(_ctx: PipeContext): void {},

  async run(ctx: PipeContext): Promise<StageResult> {
    const cfg = ctx.stageConfig ?? {};
    validateFlags(cfg);

    if (cfg.schema != null) {
      ctx.artifacts["inferred_schema"] = cfg.schema;
      return { status: StageStatus.SUCCESS };
    }
    if (cfg.no_infer) {
      ctx.artifacts["inferred_schema"] = null;
      return { status: StageStatus.SUCCESS };
    }
    if (ctx.df === null) {
      ctx.artifacts["inferred_schema"] = null;
      return { status: StageStatus.SUCCESS };
    }

    const explicit = cfg.domain as string | undefined;
    let domain: string;
    let detectScore: number;
    let detectEvidence: Record<string, unknown>;

    if (explicit != null) {
      domain = explicit;
      detectScore = 1.0;
      detectEvidence = { detect_reason: "explicit" };
    } else {
      const detection = detectDomainDetailed({ records: ctx.df });
      if (detection.domain !== null) {
        domain = detection.domain;
        detectScore = detection.score;
        detectEvidence = {
          detect_reason: detection.reason,
          detect_score: detection.score,
          runner_up: detection.runner_up,
          runner_up_score: detection.runner_up_score,
        };
      } else {
        domain = "generic";
        detectScore = 0.0;
        detectEvidence = {
          detect_reason: detection.reason,
          detect_score: detection.score,
          runner_up: detection.runner_up,
          runner_up_score: detection.runner_up_score,
          fallback: true,
        };
      }
    }

    const result = map(
      { records: ctx.df },
      new DomainPackTarget(loadDomain(domain)),
      { soft: true },
    );
    const inferred: InferredSchema = {
      ...resultToInferredSchema(result, domain),
      confidence: detectScore,
    };

    ctx.artifacts["inferred_schema"] = inferred;
    // setdefault: only set when a prior stage hasn't already.
    if (!("infer_schema_evidence" in ctx.artifacts)) {
      ctx.artifacts["infer_schema_evidence"] = detectEvidence;
    }
    return { status: StageStatus.SUCCESS };
  },
};
```

- [ ] **Step 3: Eye-verify against the spec + Python.**
  - Imports resolve: `UNMAPPED_TYPE`/`loadDomain`/`FieldMapping`/`InferredSchema` from `goldencheck-types`; `detectDomainDetailed`/`map`/`DomainPackTarget`/`MapResult` from `infermap` (Task 1 surfaced `detectDomainDetailed`). Confirm `MapResult` is exported from the infermap barrel (`grep -n "MapResult" packages/typescript/infermap/src/core/index.ts`); if it's `export type`, keep the `type MapResult` import.
  - Confirm `StageStatus` + `Stage`/`PipeContext`/`StageResult` are the real exports of `../models.js` (`grep -n "export.*StageStatus\|export interface Stage\b\|export interface PipeContext" packages/typescript/goldenpipe/src/core/models.ts`). If `StageResult` needs more than `{status}` (e.g. a required field), match the real interface — read it.
  - `resultToInferredSchema` matches `_result_to_inferred_schema` line-for-line (snake_case fields; `unmappedSource` camelCase; `type: fm.target ? fm.target : UNMAPPED_TYPE`; unmapped-source loop; `min` confidence with 0.0 default).
  - The 4 branches + precedence + the 3 `detectEvidence` shapes + `setdefault` match Python.
  - `.js` on all relative imports.

- [ ] **Step 4: Commit.**
```bash
git add packages/typescript/goldenpipe/src/core/adapters/infer.ts
git commit -m "feat(goldenpipe-ts): infer_schema stage (InferMap port)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

**Report:** `DONE`/`BLOCKED`, the import/StageResult-shape confirmations, SHA.

---

## Task 4: Register the stage (opt-in, NOT default)

**Files:** Modify `packages/typescript/goldenpipe/src/core/adapters/index.ts`. Box: eye-review.

- [ ] **Step 1: Read `adapters/index.ts`** — note the import block, the `export { ... }` re-exports, and `buildDefaultRegistry()` (which does `registry.register(LoadStage)` etc.).

- [ ] **Step 2: Add the import + re-export + registration.**
  - Add to the import block: `import { InferSchemaStage } from "./infer.js";`
  - Add to the re-exports: `export { InferSchemaStage } from "./infer.js";`
  - Add inside `buildDefaultRegistry()`, after the other `registry.register(...)` calls: `registry.register(InferSchemaStage);`

- [ ] **Step 3: Do NOT touch `DEFAULT_STAGE_ORDER`** (`core/pipeline.ts`). The stage is registered (available by name) but opt-in — matching Python, whose default pipeline is the same 3 stages without `infer_schema`. Confirm you did not edit `pipeline.ts`.

- [ ] **Step 4: Verify.**
```bash
grep -n "InferSchemaStage\|infer.js" packages/typescript/goldenpipe/src/core/adapters/index.ts
grep -n "infer_schema\|InferSchema" packages/typescript/goldenpipe/src/core/pipeline.ts || echo "pipeline.ts untouched (correct — opt-in)"
```
Confirm: import + re-export + `registry.register(InferSchemaStage)` present; `pipeline.ts` has no infer_schema reference.

- [ ] **Step 5: Commit.**
```bash
git add packages/typescript/goldenpipe/src/core/adapters/index.ts
git commit -m "feat(goldenpipe-ts): register infer_schema stage (opt-in, not default)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

**Report:** `DONE`/`BLOCKED`, grep, SHA.

---

## Task 5: 7-case parity unit test

**Files:** Create `packages/typescript/goldenpipe/tests/unit/infer-schema-stage.test.ts`. Box: eye-review (no vitest). Mirrors `test_infer_schema_stage.py`.

- [ ] **Step 1: Create the test** (uses `makePipeContext` + `StageStatus` from models; the same finance rows as the Python test → detects `finance` by cross-surface parity):
```ts
import { describe, it, expect } from "vitest";
import { makePipeContext, StageStatus, type Row } from "../../src/core/models.js";
import { InferSchemaStage } from "../../src/core/adapters/infer.js";
import type { InferredSchema } from "goldencheck-types";

// Same columns/values as the Python test's _ctx() -> detects "finance".
const FINANCE_ROWS: Row[] = [
  { account_number: "A1234", currency: "USD" },
  { account_number: "A5678", currency: "EUR" },
];

describe("infer_schema stage (InferMap port)", () => {
  it("auto-detects the finance domain", async () => {
    const ctx = makePipeContext({ df: FINANCE_ROWS });
    const result = await InferSchemaStage.run(ctx);
    expect(result.status).toBe(StageStatus.SUCCESS);
    const inferred = ctx.artifacts["inferred_schema"] as InferredSchema | null;
    expect(inferred).not.toBeNull();
    expect(inferred!.domain).toBe("finance");
  });

  it("honors an explicit domain", async () => {
    const ctx = makePipeContext({ df: FINANCE_ROWS, stageConfig: { domain: "finance" } });
    await InferSchemaStage.run(ctx);
    expect((ctx.artifacts["inferred_schema"] as InferredSchema).domain).toBe("finance");
  });

  it("no_infer returns null", async () => {
    const ctx = makePipeContext({ df: FINANCE_ROWS, stageConfig: { no_infer: true } });
    await InferSchemaStage.run(ctx);
    expect(ctx.artifacts["inferred_schema"]).toBeNull();
  });

  it("passes a user-provided schema through unchanged", async () => {
    const user: InferredSchema = { domain: "user", fields: {}, confidence: 1.0 };
    const ctx = makePipeContext({ df: FINANCE_ROWS, stageConfig: { schema: user } });
    await InferSchemaStage.run(ctx);
    expect(ctx.artifacts["inferred_schema"]).toBe(user);
  });

  it("throws on conflicting schema + domain", async () => {
    const user: InferredSchema = { domain: "user", fields: {}, confidence: 1.0 };
    const ctx = makePipeContext({ stageConfig: { schema: user, domain: "finance" } });
    await expect(InferSchemaStage.run(ctx)).rejects.toThrow(/conflict/);
  });

  it("throws on conflicting no_infer + domain", async () => {
    const ctx = makePipeContext({ stageConfig: { no_infer: true, domain: "finance" } });
    await expect(InferSchemaStage.run(ctx)).rejects.toThrow(/conflict/);
  });

  it("throws on conflicting no_infer + schema", async () => {
    const user: InferredSchema = { domain: "user", fields: {}, confidence: 1.0 };
    const ctx = makePipeContext({ stageConfig: { no_infer: true, schema: user } });
    await expect(InferSchemaStage.run(ctx)).rejects.toThrow(/conflict/);
  });
});
```

- [ ] **Step 2: Cross-check the finance detection on the box (Python reference).** The TS test asserts `domain === "finance"` for these rows; confirm the Python detect agrees (same domain dictionaries, cross-surface parity):
```bash
PYTHONPATH="packages/python/infermap" POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 INFERMAP_NATIVE=0 "D:/show_case/goldenmatch/.venv/Scripts/python.exe" -c "import infermap; r=infermap.detect_domain_detailed({'records':[{'account_number':'A1234','currency':'USD'},{'account_number':'A5678','currency':'EUR'}]}); print('domain=', r.domain)"
```
Expect: `domain= finance`. (If Python's `detect_domain_detailed` takes a different input form, adapt the check — the point is to confirm these columns detect finance so the TS expectation is right. The Python unit test already asserts finance for the same columns, so this is a sanity cross-check.)

- [ ] **Step 3: Eye-verify.** `makePipeContext`/`StageStatus`/`Row` are real exports of `../../src/core/models.js`; imports use `.js`; the 7 cases match the Python tests (3 conflict cases use `rejects.toThrow(/conflict/)`); the finance rows match the Python `_ctx`.

- [ ] **Step 4: Commit.**
```bash
git add packages/typescript/goldenpipe/tests/unit/infer-schema-stage.test.ts
git commit -m "test(goldenpipe-ts): infer_schema stage 7-case parity with Python

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

**Report:** `DONE`/`BLOCKED`, the Python finance-detect cross-check output, SHA.

---

## Task 6: Rebase + push + PR + arm (controller runs this)

**Files:** none.

- [ ] **Step 1: Rebase onto fresh origin/main** (main moves fast):
```bash
unset GH_TOKEN; gh auth switch --user benzsevern; export GH_TOKEN=$(gh auth token --user benzsevern)
git fetch origin main -q
git rebase origin/main
```
Conflicts unlikely (new file + isolated edits). If `pnpm-lock.yaml` conflicts, prefer re-running `pnpm install --lockfile-only` after taking main's lockfile, then re-add. Re-validate.

- [ ] **Step 2: Confirm the three-dot diff is clean:**
```bash
git diff --stat origin/main...HEAD
```
Expect only: the spec, the plan, `infermap/src/core/index.ts`, `goldenpipe/package.json`, `pnpm-lock.yaml`, `goldenpipe/src/core/adapters/infer.ts`, `goldenpipe/src/core/adapters/index.ts`, `goldenpipe/tests/unit/infer-schema-stage.test.ts`. If unrelated files appear, STOP.

- [ ] **Step 3: Push.**
```bash
git push -u origin feat/goldenpipe-ts-infer-schema
```

- [ ] **Step 4: Open the PR.**
```bash
gh pr create --repo benseverndev-oss/goldenmatch --base main \
  --title "feat(goldenpipe): TS infer_schema stage (InferMap port)" \
  --body "$(cat <<'EOF'
## What

Ports goldenpipe's Python `infer_schema` stage to TypeScript, so TS goldenpipe can do domain-aware schema inference via the (now WASM-capable) TS `infermap` — mirroring how the Python pipeline consumes InferMap.

- New adapter `infer_schema` (opt-in; registered but NOT in `DEFAULT_STAGE_ORDER`, matching Python's default pipeline).
- Runs `detectDomainDetailed` → domain (generic fallback), then `map(..., { soft: true })` → converts the `MapResult` to a shared `goldencheck-types.InferredSchema` (byte-faithful mirror of `_result_to_inferred_schema`).
- Produces both `inferred_schema` and `infer_schema_evidence`, same as Python.
- Surfaces `detectDomainDetailed` from the infermap barrel; adds `infermap` + `goldencheck-types` as goldenpipe deps.

## Parity

7-case unit test mirroring `test_infer_schema_stage.py` (auto-detect finance, explicit domain, no_infer→null, schema passthrough, 3 conflict-throws). The `InferredSchema` type is shared via `goldencheck-types`, so the artifact is structurally identical cross-surface. Since the stage calls the InferMap scorers, it inherits their WASM/kernel path when the consumer enables it.

Spec: `docs/superpowers/specs/2026-07-07-goldenpipe-ts-infer-schema-design.md`
Plan: `docs/superpowers/plans/2026-07-07-goldenpipe-ts-infer-schema.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44
EOF
)"
```

- [ ] **Step 5: Arm auto-merge + STOP.**
```bash
gh pr merge <PR#> --repo benseverndev-oss/goldenmatch --squash --auto
```
No `--delete-branch`. Do NOT poll CI. Report the PR number and STOP.

---

## Verification Summary

| What | How | Where |
| --- | --- | --- |
| Barrel exports detectDomainDetailed | grep + CI tsc | Box + CI (Task 1) |
| Deps resolve | `pnpm install --lockfile-only` + CI | Box + CI (Task 2) |
| Stage logic byte-faithful to Python | eye-review vs infer_schema.py | Box (Task 3) |
| Opt-in registration (not default) | grep pipeline.ts untouched | Box (Task 4) |
| 7-case parity | vitest | CI (Task 5) |
| Finance detection correct | Python detect cross-check | Box (Task 5) |
| No unrelated diff | three-dot diff | Box (Task 6) |
