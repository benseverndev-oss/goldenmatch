# TS AgentSession Port — Wave 1 (Core) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the Python `AgentSession` decision core (profile → strategy → config → analyze/autoconfigure/deduplicate) to the edge-safe TS package as `src/core/agent/**`, plus a shared `AGENT_SKILLS` registry + `dispatchSkill`, behaviour-fixture-verified against Python (the `selectStrategy` decision table is the keystone).

**Architecture:** Pure decision logic on `Row[]` (no `node:*`), reusing existing TS primitives (`dedupe`, `match`, `autoConfigureRowsIterate`, `gatePairs`/`ReviewQueue`, `detectDomain`). The I/O seam is a dependency-injected `loadTable` on `SkillContext`, so core never reads files. Waves 2 (MCP), 3 (A2A), 4 (node+docs) are separate plans.

**Tech Stack:** TypeScript (strict: `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`), vitest, Python emitter (rapidfuzz/polars venv) for goldens.

**Spec:** `docs/superpowers/specs/2026-06-15-ts-agentsession-a2a-port-design.md`

---

## Environment / runner notes (READ FIRST)

- **Worktree:** `D:\show_case\gm-agent`, branch `feat/ts-agentsession-a2a`. TS package dir: `packages/typescript/goldenmatch`.
- **This box OOM-kills vitest/tsup (exit 137).** Local gate = `npx tsc --noEmit` (typecheck, light). Tests: attempt a SINGLE targeted file `npx vitest run tests/parity/<one>.test.ts` only; if it OOMs, commit and let **CI be the test gate**. NEVER run the full `pnpm test`/`vitest run` locally.
- **TS deps in this worktree may not be installed.** Before any `tsc`/`vitest`, check `packages/typescript/goldenmatch/node_modules` exists; if not, installing on exFAT `D:` is fiddly (see memory `reference_ts_worktree_install_exfat`). Prefer pushing and letting CI typecheck+test. Treat local runs as best-effort, CI as authoritative.
- **Python emitter** runs in the main venv with the worktree shadowed: `PYTHONPATH=D:/show_case/gm-agent/packages/python/goldenmatch POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 D:/show_case/goldenmatch/.venv/Scripts/python.exe scripts/emit_agent_fixtures.py` (run from the python package dir). See memory `reference_py_worktree_test_native_skew`.
- **Edge-safety is manual** (no lint gate): every task that adds a `src/core/agent` file ends with `grep -rn "node:" packages/typescript/goldenmatch/src/core/agent/` returning nothing.
- **Commit after every task.** Do not push until the final task; the user merges via PR.
- **Config construction uses the existing `make*` factories.** `src/core/types.ts` ships `makeMatchkeyField` / `makeMatchkeyConfig` / `makeBlockingConfig` (they fill required defaults: `maxBlockSize: 5000`, `skipOversized: false`), and `src/core/api.ts::buildConfigFromOptions` (api.ts:58-145) is essentially `decisionToConfig` already — use it as the reference. (The "full literals, no factories" rule in the TS CLAUDE.md applies to TEST fixtures, not production code.)

## Pre-flight confirmations (Task 0)

Three contracts the exploration could not fully pin; confirm them by reading the source before coding the tasks that use them. Each is a 2-minute read.

- [ ] **`DedupeOptions.config`**: read `packages/typescript/goldenmatch/src/core/api.ts` (`dedupe`/`match` + `DedupeOptions`). Confirm how a `GoldenMatchConfig` is passed (likely `dedupe(rows, { config })`). Note the exact option key.
- [ ] **`confidence_distribution` keys**: read `packages/python/goldenmatch/goldenmatch/core/agent.py` `deduplicate()` body (~lines 448–533). It emits **FOUR** keys: `auto_merged` / `review` / `auto_rejected` / `total_pairs` (= `len(scored_pairs)`), mapped from `gate_pairs`. The TS must emit all four (a structural deep-equal fixture will fail if `total_pairs` is missing).
- [ ] **`DomainProfile` fields**: read `packages/typescript/goldenmatch/src/core/domain.ts` (`detectDomain` return). It exposes `name` + `confidence` (confirmed). Use that `confidence` directly as `domain_confidence` for the `> 0.5` branch (Python computes `hits/len(signals)`; the TS `detectDomain.confidence` is the ported equivalent — keep the `> 0.5` threshold + branch semantics identical).
- [ ] **`autoConfigureRowsIterate` is async** (returns `Promise<{config, profile, history}>`, `autoconfig.ts:583`). So `AgentSession.autoconfigure` — which needs `history` for telemetry — is **`async`** / returns a Promise. Settle this signature before Task 5; the spec's "sync" framing for `autoconfigure` is superseded (only `analyze` stays sync).

Record the three answers as a comment block at the top of `src/core/agent/strategy.ts` so later tasks don't re-derive them.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/core/agent/types.ts` (new) | `DataProfile`, `FieldProfile`, `StrategyDecision`, `AnalyzeResult`, `Telemetry`, `SkillDef`, `SkillContext`, result shapes. Pure types. |
| `src/core/agent/strategy.ts` (new) | `profileForAgent` / `selectStrategy` / `buildAlternatives` / `decisionToConfig`. The parity keystone. No `node:*`. |
| `src/core/agent/session.ts` (new) | `AgentSession` class: `analyze` / `autoconfigure` / `deduplicate` / `matchSources` / `compareStrategies`. Delegates to existing core primitives. |
| `src/core/agent/skills.ts` (new) | `AGENT_SKILLS` registry + `dispatchSkill` + the DI `SkillContext` (`loadTable` seam). |
| `src/core/agent/index.ts` (new) | Re-exports; wired into `src/core/index.ts`. |
| `packages/python/goldenmatch/scripts/emit_agent_fixtures.py` (new) | Emit goldens for analyze/selectStrategy/autoconfigure. |
| `packages/typescript/goldenmatch/tests/parity/agent-strategy.test.ts` (new) | `selectStrategy` decision-table parity (structural). |
| `packages/typescript/goldenmatch/tests/parity/agent-analyze.test.ts` (new) | `analyze()` shape + numeric-rounding parity (4-decimal). |
| `packages/typescript/goldenmatch/tests/unit/agent-skills.test.ts` (new) | registry/dispatch unit (rows-or-path seam, error catch). |

---

### Task 1: Types + `profileForAgent`

**Files:**
- Create: `packages/typescript/goldenmatch/src/core/agent/types.ts`
- Create: `packages/typescript/goldenmatch/src/core/agent/strategy.ts`
- Test: `packages/typescript/goldenmatch/tests/unit/agent-profile.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// tests/unit/agent-profile.test.ts
import { describe, it, expect } from "vitest";
import { profileForAgent } from "../../src/core/agent/strategy.js";

describe("profileForAgent", () => {
  it("computes uniqueness, null_rate, avg_length, type per field", () => {
    const rows = [
      { id: "1", name: "Alice", note: null },
      { id: "2", name: "Alice", note: "x" },
      { id: "3", name: "Bob", note: "yy" },
      { id: "4", name: "Carol", note: "zzz" },
    ];
    const p = profileForAgent(rows);
    expect(p.row_count).toBe(4);
    const id = p.fields.find((f) => f.name === "id")!;
    expect(id.uniqueness).toBeCloseTo(1.0, 4);     // 4 unique / 4
    expect(id.null_rate).toBeCloseTo(0.0, 4);
    const name = p.fields.find((f) => f.name === "name")!;
    expect(name.uniqueness).toBeCloseTo(0.75, 4);  // Alice,Bob,Carol = 3/4
    const note = p.fields.find((f) => f.name === "note")!;
    expect(note.null_rate).toBeCloseTo(0.25, 4);   // 1 null / 4
    expect(name.type).toBe("string");
  });

  it("flags sensitive columns by name pattern", () => {
    expect(profileForAgent([{ ssn: "x" }]).has_sensitive).toBe(true);
    expect(profileForAgent([{ date_of_birth: "x" }]).has_sensitive).toBe(true);
    expect(profileForAgent([{ name: "x" }]).has_sensitive).toBe(false);
  });
});
```

- [ ] **Step 2: Run to verify it fails** — `npx vitest run tests/unit/agent-profile.test.ts` (Expected: module not found). If vitest OOMs locally, skip to Step 3 and rely on CI.

- [ ] **Step 3: Implement types + profileForAgent**

`types.ts`:
```ts
// Edge-safe: no `node:` imports. Ported from goldenmatch/core/agent.py.
export interface FieldProfile {
  name: string;
  type: "string" | "numeric" | "other";
  uniqueness: number;   // n_unique / row_count, 0-1
  null_rate: number;    // fraction null, 0-1
  avg_length: number;   // mean string length (0 for non-string)
}
export interface DataProfile {
  row_count: number;
  fields: FieldProfile[];
  has_sensitive: boolean;
}
export interface StrategyDecision {
  strategy: string;
  why: string;
  domain: string | null;
  strong_ids: string[];
  fuzzy_fields: string[];
  backend: string | null;
  auto_execute: boolean;
}
export type Alternative = { strategy: string; why_not: string };
export interface Telemetry { available: boolean; source: string; stop_reason?: string; health?: string }
```

`strategy.ts` (port `profile_for_agent` exactly — uniqueness = distinct/row_count, null_rate, avg byte length for strings):
```ts
// Edge-safe: no `node:` imports. Ported from goldenmatch/core/agent.py.
// Task-0 confirmations: <DedupeOptions.config key>, <confidence_distribution keys>, <DomainProfile fields>.
import type { Row } from "../types.js";
import type { DataProfile, FieldProfile } from "./types.js";

const SENSITIVE_PATTERNS = new Set([
  "ssn", "social_security", "dob", "date_of_birth",
  "birth_date", "drivers_license", "dl_number",
]);

export function profileForAgent(rows: readonly Row[]): DataProfile {
  const height = rows.length;
  const cols = height > 0 ? Object.keys(rows[0]!) : [];
  let hasSensitive = false;
  const fields: FieldProfile[] = [];
  for (const col of cols) {
    const colLower = col.toLowerCase().replace(/ /g, "_");
    if (SENSITIVE_PATTERNS.has(colLower)) hasSensitive = true;
    const values = rows.map((r) => r[col]);
    const nonNull = values.filter((v) => v !== null && v !== undefined && v !== "");
    const nullCount = height - nonNull.length;
    const distinct = new Set(nonNull.map((v) => String(v))).size;
    const uniqueness = height > 0 ? distinct / height : 0;
    const nullRate = height > 0 ? nullCount / height : 0;
    const allNumeric = nonNull.length > 0 && nonNull.every((v) => typeof v === "number" || !Number.isNaN(Number(v)));
    const type: FieldProfile["type"] = nonNull.length === 0 ? "string" : allNumeric ? "numeric" : "string";
    let avgLength = 0;
    if (type === "string" && nonNull.length > 0) {
      const total = nonNull.reduce((acc, v) => acc + new TextEncoder().encode(String(v)).length, 0);
      avgLength = total / nonNull.length;
    }
    fields.push({ name: col, type, uniqueness, null_rate: nullRate, avg_length: avgLength });
  }
  return { row_count: height, fields, has_sensitive: hasSensitive };
}
```

NOTE to implementer: byte length uses `new TextEncoder().encode(String(v)).length` (edge-safe; never `Buffer`, which is node-only and would fail the Task-7/10 edge greps). Python's `str.len_bytes()` is UTF-8 byte length, so this matches.

- [ ] **Step 4: Run test to verify it passes** (or push for CI).
- [ ] **Step 5: Edge-safety + commit**

```bash
grep -rn "node:" packages/typescript/goldenmatch/src/core/agent/ && echo "FAIL: node import in core" || echo "edge-safe OK"
git add packages/typescript/goldenmatch/src/core/agent/types.ts packages/typescript/goldenmatch/src/core/agent/strategy.ts packages/typescript/goldenmatch/tests/unit/agent-profile.test.ts
git commit -m "feat(agent): types + profileForAgent (edge-safe)"
```

---

### Task 2: `selectStrategy` — the decision keystone

**Files:**
- Modify: `src/core/agent/strategy.ts`
- Test: `packages/typescript/goldenmatch/tests/unit/agent-select-strategy.test.ts`

Port the exact decision tree + thresholds: sensitive→`pprl`(auto_execute=false); strong id = string & `uniqueness>0.90` & `null_rate<0.05`; fuzzy candidate = string & `uniqueness<0.90` & `avg_length>3` & `null_rate<0.50`; backend=`ray` if `row_count>500_000`; tree: strong&!fuzzy→`exact_only`; strong&fuzzy→`exact_then_fuzzy`; fuzzy→`fuzzy`; domain & `domain_confidence>0.5`→`domain_extraction`; else→`fuzzy` (fallback). Use `detectDomain` for the domain branch (per Task-0 confirmation).

- [ ] **Step 1: Write the failing test (every branch)**

```ts
// tests/unit/agent-select-strategy.test.ts
import { describe, it, expect } from "vitest";
import { selectStrategy } from "../../src/core/agent/strategy.js";
import type { DataProfile, FieldProfile } from "../../src/core/agent/types.js";

const f = (name: string, o: Partial<FieldProfile>): FieldProfile =>
  ({ name, type: "string", uniqueness: 0.5, null_rate: 0, avg_length: 8, ...o });
const prof = (fields: FieldProfile[], extra: Partial<DataProfile> = {}): DataProfile =>
  ({ row_count: 1000, fields, has_sensitive: false, ...extra });

describe("selectStrategy decision table", () => {
  it("sensitive -> pprl, auto_execute false", () => {
    const d = selectStrategy(prof([f("ssn", {})], { has_sensitive: true }));
    expect(d.strategy).toBe("pprl");
    expect(d.auto_execute).toBe(false);
  });
  it("strong id only -> exact_only", () => {
    const d = selectStrategy(prof([f("id", { uniqueness: 0.99, null_rate: 0.0 })]));
    expect(d.strategy).toBe("exact_only");
    expect(d.strong_ids).toEqual(["id"]);
  });
  it("strong id + fuzzy -> exact_then_fuzzy", () => {
    const d = selectStrategy(prof([
      f("id", { uniqueness: 0.99, null_rate: 0.0 }),
      f("name", { uniqueness: 0.4, avg_length: 8, null_rate: 0.1 }),
    ]));
    expect(d.strategy).toBe("exact_then_fuzzy");
    expect(d.fuzzy_fields).toEqual(["name"]);
  });
  it("fuzzy only -> fuzzy", () => {
    const d = selectStrategy(prof([f("name", { uniqueness: 0.4, avg_length: 8, null_rate: 0.1 })]));
    expect(d.strategy).toBe("fuzzy");
  });
  it("no usable fields -> fuzzy fallback", () => {
    const d = selectStrategy(prof([f("x", { uniqueness: 0.95, null_rate: 0.9 })])); // not strong (null), not fuzzy (uniq>0.9)
    expect(d.strategy).toBe("fuzzy");
    expect(d.why).toMatch(/defaulting to fuzzy/i);
  });
  it("domain recognized, no strong/fuzzy -> domain_extraction", () => {
    // Fields that are NEITHER strong (uniq>0.9 & null<0.05) NOR fuzzy
    // (uniq<0.9 & avg_length>3 & null<0.5), but whose COLUMN NAMES a real
    // detectDomain rulebook recognizes with confidence > 0.5.
    // IMPLEMENTER: read src/core/domain.ts, pick column names from one rulebook's
    // signals so detectDomain(cols).confidence > 0.5; give each uniqueness>0.9 +
    // null_rate>=0.05 so it's non-strong and non-fuzzy.
    const d = selectStrategy(prof([
      f("<domain_signal_col_1>", { uniqueness: 0.95, null_rate: 0.1 }),
      f("<domain_signal_col_2>", { uniqueness: 0.95, null_rate: 0.1 }),
    ]));
    expect(d.strategy).toBe("domain_extraction");
    expect(d.domain).not.toBeNull();
  });
  it("backend=ray above 500k rows", () => {
    const d = selectStrategy(prof([f("id", { uniqueness: 0.99, null_rate: 0 })], { row_count: 600_000 }));
    expect(d.backend).toBe("ray");
  });
});
```

- [ ] **Step 2: Run (fail / CI).**
- [ ] **Step 3: Implement `selectStrategy`** (verbatim port of the Python tree; domain branch via `detectDomain`, computing `domain_confidence` per Task-0). Each branch sets `why`, `strong_ids`, `fuzzy_fields`, `backend`, `domain`, `auto_execute` to match Python's `StrategyDecision` literals.
- [ ] **Step 4: Run (pass / CI).**
- [ ] **Step 5: Edge-safety grep + commit** `feat(agent): selectStrategy decision table`.

---

### Task 3: `buildAlternatives` + `decisionToConfig`

**Files:**
- Modify: `src/core/agent/strategy.ts`
- Test: `packages/typescript/goldenmatch/tests/unit/agent-decision-config.test.ts`

`buildAlternatives`: if strategy != `pprl` push `{strategy:"pprl", why_not:"No sensitive fields detected, but PPRL is available if data leaves your network."}`; if != `fellegi_sunter` push `{strategy:"fellegi_sunter", why_not:"Probabilistic model available for automatic parameter estimation."}`.

`decisionToConfig`: build the config with the existing `make*` factories (see env note) — `src/core/api.ts::buildConfigFromOptions` (api.ts:58-145) is nearly a rename of this; mirror it. Exact MK per strong id: `makeMatchkeyConfig({ name: "exact_"+col, type: "exact", fields: [makeMatchkeyField({ field: col, transforms: ["lowercase","strip"], scorer: "exact", weight: 1.0 })] })`. Weighted MK from fuzzy fields: one `makeMatchkeyConfig({ name: "fuzzy", type: "weighted", threshold: 0.85, fields: [...] })` with each field `scorer: "jaro_winkler", weight: 1.0, transforms: ["lowercase","strip"]`. Blocking (only if fuzzy): `makeBlockingConfig({ strategy: "static", keys: [{ fields: [firstFuzzy], transforms: ["lowercase","first_token"] }] })` (factory supplies `maxBlockSize: 5000`, `skipOversized: false`). Set `backend: decision.backend ?? undefined`.

- [ ] **Step 1: Failing test**

```ts
// tests/unit/agent-decision-config.test.ts
import { describe, it, expect } from "vitest";
import { buildAlternatives, decisionToConfig } from "../../src/core/agent/strategy.js";

describe("buildAlternatives", () => {
  it("offers pprl + fellegi_sunter for a fuzzy decision", () => {
    const alts = buildAlternatives({ strategy: "fuzzy" } as any, {} as any);
    expect(alts.map((a) => a.strategy)).toEqual(["pprl", "fellegi_sunter"]);
  });
});
describe("decisionToConfig", () => {
  it("builds exact + weighted matchkeys + blocking with full literals", () => {
    const cfg = decisionToConfig({
      strategy: "exact_then_fuzzy", why: "", domain: null,
      strong_ids: ["id"], fuzzy_fields: ["name"], backend: null, auto_execute: true,
    });
    const names = (cfg.matchkeys ?? []).map((m: any) => m.name);
    expect(names).toContain("exact_id");
    expect(names).toContain("fuzzy");
    const fuzzy = (cfg.matchkeys ?? []).find((m: any) => m.name === "fuzzy")!;
    expect(fuzzy.fields[0].scorer).toBe("jaro_winkler");
    expect(fuzzy.fields[0].weight).toBe(1.0);
    expect(cfg.blocking).toBeDefined();
    expect(cfg.blocking!.strategy).toBe("static");
    expect(cfg.blocking!.keys[0].fields).toEqual(["name"]);
  });
});
```

NOTE: use the `make*` factories (they satisfy the `MatchkeyConfig` union and fill `maxBlockSize: 5000` / `skipOversized: false` — don't re-specify those). The test asserts the observable shape (`name`, `fields[].scorer/weight`, blocking). If a factory's required args differ from the above, match what `buildConfigFromOptions` (api.ts:58-145) passes.

- [ ] **Step 2–4: red → implement → green (or CI).**
- [ ] **Step 5: grep + commit** `feat(agent): buildAlternatives + decisionToConfig`.

---

### Task 4: `AgentSession.analyze`

**Files:**
- Create: `src/core/agent/session.ts`
- Test: `packages/typescript/goldenmatch/tests/unit/agent-analyze.test.ts`

`analyze(rows)` → sets `this.data = rows`, computes `profileForAgent` → `selectStrategy` → `buildAlternatives`, runs the domain detection, and returns `this.reasoning` with the EXACT shape from Python `analyze()` (profile.fields rounded: uniqueness/null_rate to 4dp, avg_length to 1dp; plus strategy/why/domain/strong_ids/fuzzy_fields/backend/auto_execute/alternatives).

- [ ] **Step 1: Failing test** — assert the returned dict keys + rounding (e.g. `uniqueness` rounded to 4dp, `avg_length` to 1dp) on a small fixed dataset.
- [ ] **Step 2–4: red → implement → green (or CI).** `AgentSession` constructor mirrors Python: `data=null, config=null, result=null, reviewQueue=new ReviewQueue({backend:"memory"}), reasoning={}, lastTelemetry=null`.
- [ ] **Step 5: grep + commit** `feat(agent): AgentSession.analyze`.

---

### Task 5: `autoconfigure` / `deduplicate` / `matchSources` / `compareStrategies` + telemetry

**Files:**
- Modify: `src/core/agent/session.ts`
- Test: `packages/typescript/goldenmatch/tests/unit/agent-session.test.ts`

- `autoconfigure(rows)` — **`async`** (returns a Promise; `autoConfigureRowsIterate` is async, per Task-0): `await autoConfigureRowsIterate(rows)` → `{config, history}`; set `this.config`, `this.lastTelemetry = captureTelemetry(history, "autoconfigure")`; return `{config, telemetry}`. `captureTelemetry` builds the minimal `{available:true, source, stop_reason?, health?}` from `history` (serializeTelemetry is NOT ported — minimal shape only, matching Python's fallback).
- `deduplicate(rows, config?)` — **`async`**: resolve config (arg or `await autoConfigureRowsIterate`), `await dedupe(rows, { config })` (option key per Task-0), gate via `gatePairs(result.scoredPairs)` → `confidence_distribution = { auto_merged: gated.autoApproved.length, review: gated.needsReview.length, auto_rejected: gated.rejected.length, total_pairs: result.scoredPairs.length }` (**all FOUR keys** per Task-0 — `total_pairs` is required for parity). Note `gated.needsReview` is `ReviewItem[]` while `autoApproved`/`rejected` are `ScoredPair[]` (asymmetric) — only `.length` is used here. Set `this.result`, `this.lastTelemetry = {available:false, source:"deduplicate"}`; return `{results, reasoning, confidence_distribution, storage}` (`storage` = `"memory"`).
- `matchSources(rowsA, rowsB, config?)`: `await match(rowsA, rowsB, { config })`; return `{results, reasoning}`.
- `compareStrategies(rows, groundTruth?)`: run dedupe under ≥2 candidate strategies (e.g. exact_then_fuzzy vs fuzzy), score each (use `evaluateClusters` if groundTruth supplied — grep its path in `src/core`), return per-strategy metrics. Mirror Python `compare_strategies` keys.

- [ ] **Step 1: Failing tests** — `deduplicate` returns the four keys with correct `confidence_distribution` counts on a tiny dataset; `autoconfigure` telemetry `source==="autoconfigure"`; `deduplicate` telemetry `{available:false, source:"deduplicate"}`.
- [ ] **Step 2–4: red → implement → green (or CI).**
- [ ] **Step 5: grep + commit** `feat(agent): session autoconfigure/deduplicate/match/compare`.

---

### Task 6: `AGENT_SKILLS` registry + `dispatchSkill` (the seam)

**Files:**
- Create: `src/core/agent/skills.ts`
- Test: `packages/typescript/goldenmatch/tests/unit/agent-skills.test.ts`

`SkillContext = { session: AgentSession; loadTable(src: string): Promise<Row[]>; memoryStore?: …; identityStore?: … }`. Each `SkillDef` handler does `const rows = (args.rows as Row[] | undefined) ?? await ctx.loadTable(args.file_path as string)`. `dispatchSkill(id, args, ctx)` finds the def, runs the handler, and on throw returns `{ error: String(err) }` (the dispatcher catches; surfaces format errors per-surface in later waves). Wave 1 registers the AgentSession-backed skills (`analyze_data`, `auto_configure`, `agent_deduplicate`, `agent_match_sources`, `agent_compare_strategies`, `suggest_pprl`); the standalone/optional-dep tools (`scan_quality` etc.) and identity/memory routing land in Waves 2–3 (the registry is extensible).

- [ ] **Step 1: Failing test**

```ts
// tests/unit/agent-skills.test.ts
import { describe, it, expect } from "vitest";
import { AGENT_SKILLS, dispatchSkill } from "../../src/core/agent/skills.js";
import { AgentSession } from "../../src/core/agent/session.js";

const ctx = () => ({ session: new AgentSession(), loadTable: async () => [{ id: "1", name: "a" }] });

describe("dispatchSkill", () => {
  it("accepts inline rows (edge path, no loadTable call)", async () => {
    let loaded = false;
    const out = await dispatchSkill("analyze_data",
      { rows: [{ id: "1", name: "Alice" }, { id: "2", name: "Alyce" }] },
      { session: new AgentSession(), loadTable: async () => { loaded = true; return []; } });
    expect(loaded).toBe(false);
    expect(out.strategy).toBeDefined();
  });
  it("falls back to loadTable when only file_path given", async () => {
    const out = await dispatchSkill("analyze_data", { file_path: "x.csv" }, ctx());
    expect(out.strategy).toBeDefined();
  });
  it("returns {error} on handler throw", async () => {
    const out = await dispatchSkill("analyze_data", {},
      { session: new AgentSession(), loadTable: async () => { throw new Error("no loader"); } });
    expect(out.error).toMatch(/no loader/);
  });
  it("every skill has id + description + inputSchema + handler", () => {
    for (const s of AGENT_SKILLS) {
      expect(s.id && s.description && s.inputSchema && typeof s.handler).toBeTruthy();
    }
  });
});
```

- [ ] **Step 2–4: red → implement → green (or CI).**
- [ ] **Step 5: grep + commit** `feat(agent): AGENT_SKILLS registry + dispatchSkill`.

---

### Task 7: Wire exports + edge-safety + typecheck

**Files:**
- Create: `src/core/agent/index.ts`
- Modify: `src/core/index.ts` (add `export * from "./agent/index.js";`)

- [ ] **Step 1:** Create `index.ts` re-exporting the public surface (`AgentSession`, `AGENT_SKILLS`, `dispatchSkill`, `profileForAgent`, `selectStrategy`, types).
- [ ] **Step 2:** Add the re-export to `src/core/index.ts` (confirm it doesn't pull node — it won't).
- [ ] **Step 3:** Edge-safety: `grep -rn "node:" packages/typescript/goldenmatch/src/core/agent/` → nothing. Also grep for `Buffer` / `process.` / `fs` in the agent dir → nothing.
- [ ] **Step 4:** Typecheck: `cd packages/typescript/goldenmatch && npx tsc --noEmit` (if deps installed; else rely on CI). Expected: clean.
- [ ] **Step 5: Commit** `feat(agent): export core agent surface`.

---

### Task 8: Python fixture emitter

**Files:**
- Create: `packages/python/goldenmatch/scripts/emit_agent_fixtures.py`

Model on `scripts/emit_scorer_parity_fixtures.py`: deterministic (no `datetime.now()`), writes `Path(__file__).resolve().parents[3] / "typescript/goldenmatch/tests/parity/fixtures/agent-decisions.json"`. For a fixed set of datasets — **sensitive, strong-id-only, strong+fuzzy, fuzzy-only, domain-recognized (non-strong/non-fuzzy cols a Python `match_domain` rulebook recognizes, to exercise `domain_extraction`), fallback, and >500k-row simulated** (via a `row_count` override path if cheap) — capture from the Python `AgentSession`/`select_strategy`:
- `select_strategy` decision (strategy/why/strong_ids/fuzzy_fields/backend/auto_execute/domain).
- `analyze()` reasoning dict (with Python's rounding).
- `autoconfigure()` telemetry `{available, source}` + config matchkey/blocking shape.

- [ ] **Step 1:** Write the emitter (datasets inline as small row lists; build a `pl.DataFrame`; call `profile_for_agent` + `select_strategy` + `AgentSession().analyze`/`autoconfigure`). Pin any ids.
- [ ] **Step 2: Run it**

```bash
cd packages/python/goldenmatch && PYTHONPATH="D:/show_case/gm-agent/packages/python/goldenmatch" POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 "D:/show_case/goldenmatch/.venv/Scripts/python.exe" scripts/emit_agent_fixtures.py
```
Expected: `wrote N cases -> .../tests/parity/fixtures/agent-decisions.json`.

- [ ] **Step 3: Commit** the emitter + the generated fixture `test(agent): Python emitter + decision goldens`.

---

### Task 9: TS parity tests

**Files:**
- Create: `packages/typescript/goldenmatch/tests/parity/agent-strategy.test.ts` (structural: every emitted `select_strategy` decision matches)
- Create: `packages/typescript/goldenmatch/tests/parity/agent-analyze.test.ts` (4-decimal on numeric profile fields; structural on the rest)

- [ ] **Step 1:** Load `tests/parity/fixtures/agent-decisions.json`; for each case, build rows → `selectStrategy`/`analyze` (TS) → assert deep-equal on the decision (strategy/strong_ids/fuzzy_fields/backend/auto_execute/domain) and `toBeCloseTo(…, 4)` on numeric profile fields. Mirror the `scorer-ground-truth.test.ts` loader style.
- [ ] **Step 2: Run** the two files (single-file vitest, or CI). Expected: PASS. If a case mismatches, the TS port diverges from Python — fix the port, not the fixture.
- [ ] **Step 3: Commit** `test(agent): selectStrategy + analyze parity`.

---

### Task 10: Wave-1 gate + review

- [ ] **Step 1: Full typecheck** `cd packages/typescript/goldenmatch && npx tsc --noEmit` (or CI). Clean.
- [ ] **Step 2: Edge-safety sweep** `grep -rn "node:\|Buffer\|process\.\|require(" packages/typescript/goldenmatch/src/core/agent/` → nothing.
- [ ] **Step 3: Push the branch; CI is the authoritative test gate** (box OOMs vitest). Confirm CI vitest + typecheck green on the PR before considering Wave 1 done.
- [ ] **Step 4: Final code review** of the Wave-1 diff (`git diff origin/main...HEAD`), focused on: parity to the Python thresholds, zero `node:*` in core, the rows-or-path seam, and the registry being extensible for Waves 2–3.
- [ ] **Step 5: Finish** via superpowers:finishing-a-development-branch (open the Wave-1 PR; do NOT flip the versioning-policy parity matrix yet — that waits for Wave 4).

---

## Notes for the implementer

- **YAGNI:** Wave 1 registers only the AgentSession-backed skills. Do NOT build MCP/A2A wiring, file loaders, or the optional-dep tools here — those are Waves 2–4. The registry just has to be shaped so they slot in later.
- **Parity fidelity over cleverness:** `selectStrategy` must reproduce Python's branch order and thresholds exactly. When in doubt, match the Python source line-for-line; the fixture is the judge.
- **Box reality:** lean on `tsc --noEmit` locally and CI for vitest. Don't fight the OOM.
