# GoldenAnalysis Phase 3a — TypeScript Frame Core + Parity Plan

> **For agentic workers:** REQUIRED: Use superpowers:executing-plans (inline). TypeScript — safe to build/test locally (vitest/tsup; no polars/zombie-python hazards). Steps are TDD-shaped: failing test → red → minimal impl → green → commit.

**Goal:** Ship the TypeScript port of GoldenAnalysis's **generic frame path** with a **cross-surface parity proof**: `goldenanalysis-js` produces an `AnalysisReport` whose engine-independent metrics are byte-identical to the Python-locked `report_frame_summary.json`.

**Architecture:** A standalone TS package (own `package.json`, tsup + vitest, **no cross-package deps** — the frame path is self-contained, like `goldencheck-types`). The `AnalysisReport`/`Metric`/`AnalysisTable` **wire types keep snake_case** (the documented exception in `packages/typescript/CLAUDE.md`) so reports cross the JSON wire between Python and TS without remapping. Frame data is a minimal edge-safe rows array (`readonly Record<string, unknown>[]`) — no polars. Edge-safe throughout (`import type`, `.js` suffixes, no `node:` imports in `src/core/`).

**Tech Stack:** TypeScript ^5.4, tsup ^8, vitest ^4, commander ^13 (CLI). Node ≥20.

**Spec:** `docs/superpowers/specs/2026-06-08-goldenanalysis-cross-cutting-analysis-engine-design.md` — the "TypeScript" surface section + Appendix B.

**Builds on:** the Python package (merged P1/P2). The parity anchor is
`packages/python/goldenanalysis/tests/fixtures/report_frame_summary.json` (the Python
`test_report_schema.py` locks it).

---

## Parity scope (the load-bearing design decision)

The Python `frame.summary` emits 5 metrics + a `per_column` table. Two of them are
**engine-specific and cannot be reproduced byte-for-byte in TS**:
- `frame.memory_bytes` — polars `estimated_size()` (no polars in TS).
- `per_column.dtype` — polars dtype names (`"String"`, `"Int64"`) vs TS type inference.

So the **parity contract covers the engine-independent metrics**: `frame.row_count`,
`frame.column_count`, `frame.null_ratio_mean`, `frame.duplicate_row_ratio`, and the
`per_column` columns `column` / `null_ratio` / `n_unique`. TS still **emits**
`frame.memory_bytes` (a TS-appropriate byte estimate) and `dtype` (a TS type label)
so the report SHAPE matches; the parity assertion strips `generated_at` / `run_id` /
`frame.memory_bytes` and ignores the `dtype` column. This is documented in the parity
test and the README.

**Shared input.** The Python fixture is `customers_small.parquet`; TS can't read
parquet. Commit `customers_small.json` (the same 20 rows as the Python
`build_customers_small()` data) to the TS fixtures; TS produces its report from that.

---

## Conventions

- All commands from `packages/typescript/goldenanalysis/` unless noted. `pnpm install`
  at repo root registers the new workspace member + updates `pnpm-lock.yaml` (committed).
- `npx vitest run` (tests), `pnpm build` (tsup), `pnpm typecheck` (`tsc --noEmit`).
- Branch `feat/goldenanalysis-ts-frame-parity`, cut from `main`.
- Commit per task: `feat(goldenanalysis-js):` / `test(goldenanalysis-js):`.
- **Pre-flight risk:** Windows + pnpm symlinks may need Developer Mode (root CLAUDE.md).
  Validate the toolchain in Task 0 BEFORE building out — `pnpm install` + an empty
  `vitest run` must work. If they don't, STOP and surface it.

---

## Phase 3a.0 — Package skeleton + toolchain validation

### Task 0.1: Scaffold the package

**Files:**
- `packages/typescript/goldenanalysis/package.json`
- `packages/typescript/goldenanalysis/tsconfig.json`
- `packages/typescript/goldenanalysis/tsup.config.ts`
- `packages/typescript/goldenanalysis/vitest.config.ts`
- `packages/typescript/goldenanalysis/LICENSE` (copy goldenpipe TS LICENSE)
- `packages/typescript/goldenanalysis/src/index.ts` (stub: `export const VERSION = "0.1.0";`)
- `packages/typescript/goldenanalysis/src/core/index.ts` (stub)

- [ ] **Step 1:** `package.json` mirroring `packages/typescript/goldenpipe/package.json` but: `"name": "goldenanalysis"`, `"version": "0.1.0"`, description "TypeScript port of GoldenAnalysis — read-only cross-cutting analysis/reporting.", **NO `goldencheck`/`goldenflow`/`goldenmatch` dependencies** (frame path is self-contained), `"dependencies": { "commander": "^13.0.0" }`, devDeps `@types/node`/`rimraf`/`tsup`/`typescript`/`vitest` (same versions as goldenpipe). `bin`: `{ "goldenanalysis-js": "./dist/cli.cjs" }`. `exports` for `.` and `./core`.
- [ ] **Step 2:** Copy `tsconfig.json` + `vitest.config.ts` from goldenpipe verbatim. `tsup.config.ts` entries: `index`, `core/index`, `cli`.
- [ ] **Step 3 (toolchain gate):** from repo root, `pnpm install`. Then in the package, `npx vitest run` (0 tests is fine — it must EXIT 0, proving vitest resolves). If `pnpm install` fails on Windows symlinks, STOP and report.
- [ ] **Step 4: Commit.** `feat(goldenanalysis-js): package skeleton + toolchain`

### Task 0.2: Smoke test

**Files:** `packages/typescript/goldenanalysis/tests/unit/smoke.test.ts`

- [ ] **Step 1 (red):** `import { VERSION } from "../../src/index.js"; expect(VERSION).toBe("0.1.0")`. Run `npx vitest run` red (no export yet) → green after adding `VERSION`.
- [ ] **Step 2: Commit.** `test(goldenanalysis-js): smoke test`

---

## Phase 3a.1 — Wire types (snake_case)

### Task 1.1: `src/core/types.ts`

- [ ] **Step 1 (red):** `tests/unit/types.test.ts` constructs a `Metric` (`{ key, value, unit, direction }`) and an `AnalysisReport` (`{ schema_version, run_id, generated_at, source, metrics, tables, narrative, analyzers_run }`) and round-trips via `JSON.stringify`/`parse`. Run red.
- [ ] **Step 2 (green):** define the interfaces with **snake_case keys** matching the Python JSON wire (lead comment citing the CLAUDE.md exception + the Python sibling `models/report.py`). `Direction = "higher_better" | "lower_better" | "neutral"`. `export const SCHEMA_VERSION = 1`. Also `AnalyzerInfo`/`AnalyzerInput`/`AnalyzerResult` (camelCase is fine here — these are internal, not wire types; `AnalyzerInput` carries `frame: readonly Record<string, unknown>[]`).
- [ ] **Step 3: Commit.** `feat(goldenanalysis-js): wire types (AnalysisReport/Metric, snake_case)`

---

## Phase 3a.2 — Aggregation primitives

### Task 2.1: `src/core/aggregate.ts`

Mirror the Python `core/aggregate.py` semantics value-for-value (it's the reference).

- [ ] **Step 1 (red):** `tests/unit/aggregate.test.ts` — exact values on tiny inputs matching the Python tests: `nullRatioPerColumn([{a:1},{a:null},...])`, `duplicateRowRatio` (one pair in five rows => 0.4; **row equality via canonical JSON of the row** so null/number/string compare like polars), `histogram([1,2,3,4], 2) => [[1,2],[2.5,2]]`, `quantile([1,2,3,4], 0.5) => 2.5`. Run red.
- [ ] **Step 2 (green):** implement. `duplicateRowRatio`: count rows whose canonical-JSON appears >1 time, divide by row count (matches polars `is_duplicated` group-membership semantics). `quantile`: linear interpolation (numpy default). Edge-safe (no `node:`).
- [ ] **Step 3: Commit.** `feat(goldenanalysis-js): aggregation primitives (parity with python core.aggregate)`

---

## Phase 3a.3 — frame.summary analyzer + registry

### Task 3.1: `src/core/analyzers/frameSummary.ts` + `src/core/registry.ts`

- [ ] **Step 1 (red):** `tests/unit/frameSummary.test.ts` builds the 20-row fixture rows (a `buildCustomersSmall()` helper mirroring the Python `_NAMES/_EMAILS/_CITIES/_AGES`), runs the analyzer, asserts `frame.row_count===20`, `frame.column_count===4`, `frame.null_ratio_mean===0.275`, `frame.duplicate_row_ratio===0.1`, directions correct, and a `per_column` table with 4 rows + columns `["column","dtype","null_ratio","n_unique"]`. Run red.
- [ ] **Step 2 (green):** implement `frameSummary(input): AnalyzerResult` delegating to `aggregate`. `info.name="frame.summary"`, `consumes=["frame"]`. `memory_bytes`: a portable estimate (e.g. byte length of the canonical JSON of all rows) — emitted, NOT parity-asserted. `dtype`: a simple TS type label (`"string"`/`"number"`/`"mixed"`). Registry: `loadAnalyzer(name)` + `availableAnalyzers()` over a hard-coded map (no entry-points in TS).
- [ ] **Step 3: Commit.** `feat(goldenanalysis-js): frame.summary analyzer + registry`

---

## Phase 3a.4 — analyze() + exporters

### Task 4.1: `src/core/analyze.ts` + `src/core/render.ts`

- [ ] **Step 1 (red):** `tests/unit/analyze.test.ts` — `analyze(rows, ["frame.summary"], { dataset: "customers" })` returns an `AnalysisReport` with `analyzers_run===["frame.summary"]`, `source.dataset==="customers"`, `schema_version===1`, a `frame.row_count` metric; `analyze(rows)` defaults to frame-compatible analyzers. `toMarkdown(report)` contains `"| Metric | Value |"` + each metric key; `toJson(report)` round-trips. Run red.
- [ ] **Step 2 (green):** `analyze` resolves analyzers via the registry, runs each over the rows, assembles the report (stamp `run_id` = caller or `${generated_at}#${dataset}`, `source` = `{ dataset, producer: "frame" }`, record unavailable in `source.unavailable`). `toMarkdown`/`toJson` in `render.ts` mirror the Python output shape (ASCII; em-dash OK in TS since not Windows-terminal-bound, but keep it simple). Export `analyze`/`toMarkdown`/`toJson`/types from `src/core/index.ts` and `src/index.ts`.
- [ ] **Step 3: Commit.** `feat(goldenanalysis-js): analyze() + exporters (json/markdown)`

---

## Phase 3a.5 — The parity proof

### Task 5.1: cross-surface parity test

**Files:**
- `packages/typescript/goldenanalysis/tests/fixtures/customers_small.json` (the 20 input rows)
- `packages/typescript/goldenanalysis/tests/fixtures/report_frame_summary.json` (COPY of the Python fixture — byte-identical)
- `packages/typescript/goldenanalysis/tests/parity/frameSummary.parity.test.ts`

- [ ] **Step 1:** generate `customers_small.json` from the Python fixture data (hand-author the 20 rows from `_NAMES/_EMAILS/_CITIES/_AGES`; `age` is a number-or-null). Copy `report_frame_summary.json` from `packages/python/goldenanalysis/tests/fixtures/` verbatim; add a header comment in a sibling `PARITY.md` that it MUST stay byte-identical to the Python copy.
- [ ] **Step 2 (red→green):** the parity test reads `customers_small.json`, runs `analyze(rows, ["frame.summary"], { dataset: "customers" })`, strips volatile (`generated_at`, `run_id`) + `frame.memory_bytes`, drops the `dtype` column from `per_column`, and asserts the result equals the same projection of `report_frame_summary.json`. So the **engine-independent metrics + table match the Python-locked report byte-for-byte**. (Anchor fixture paths to `import.meta.url`.)
- [ ] **Step 3: Commit.** `test(goldenanalysis-js): cross-surface parity vs python report_frame_summary.json`

---

## Phase 3a.6 — CLI

### Task 6.1: `src/cli.ts` (commander)

- [ ] **Step 1 (red):** `tests/unit/cli.test.ts` invokes the command action with a temp CSV/JSON path and asserts markdown output contains `frame.row_count` (call the exported action fn directly, or spawn). Run red.
- [ ] **Step 2 (green):** commander program `goldenanalysis-js` with a `report <input>` command (`.json` → parse rows; `.csv` → a minimal CSV parse into rows; `--format markdown|json`, `--analyzers`). Mirror `goldenpipe`'s `src/cli.ts` structure (commander, `bin` already wired). CSV parsing stays edge-light (no heavy dep).
- [ ] **Step 3: Commit.** `feat(goldenanalysis-js): CLI (report)`

---

## Phase 3a.7 — Docs + verify + push

### Task 7.1: README + verify

**Files:** `packages/typescript/goldenanalysis/README.md`

- [ ] **Step 1:** README: quickstart (`import { analyze } from "goldenanalysis"`), the parity note (which metrics are cross-surface-asserted vs engine-specific), and the GoldenCheck-vs-GoldenAnalysis line.
- [ ] **Step 2 (verify):** from the package: `npx vitest run` (all green), `pnpm typecheck` (clean), `pnpm build` (tsup emits `dist/`). From root: `pnpm -w turbo run build test typecheck --filter=goldenanalysis` mirrors what CI runs.
- [ ] **Step 3:** ensure `pnpm-lock.yaml` is committed (updated by Task 0's `pnpm install`). The TS CI lane auto-globs `packages/typescript/**` — no workflow edit needed.
- [ ] **Step 4: Push** `feat/goldenanalysis-ts-frame-parity` (auth dance), open PR vs main.

---

## Acceptance (Phase 3a done when)

- [ ] `goldenanalysis` TS package builds (tsup), typechecks (tsc), and tests (vitest) green; in the pnpm workspace + lockfile; the TS CI lane covers it.
- [ ] `analyze(rows, ["frame.summary"])` returns an `AnalysisReport` with snake_case wire keys; `toJson`/`toMarkdown` work.
- [ ] **Parity:** the engine-independent `frame.summary` metrics + `per_column` (column/null_ratio/n_unique) are **byte-identical to the Python-locked `report_frame_summary.json`**; `memory_bytes`/`dtype` are emitted but documented as engine-specific (out of the parity contract).
- [ ] CLI `goldenanalysis-js report <input>` prints a markdown report.
- [ ] No cross-package deps; edge-safe (`src/core/` free of `node:` imports).

### Explicitly deferred
- **Phase 3b:** TS port of the cross-run layer (ReportHistory + trend + detect_regressions + narrative) — pure/portable, no suite deps.
- **Phase 3c:** TS suite analyzers (match.rates/cluster.distribution/quality.rollup) — need goldenmatch-js/goldencheck-js/goldenflow-js/goldenpipe-js result shapes (heavy coupling).
- npm publish workflow (`publish-goldenanalysis-js.yml`) — a follow-up once 0.1.0 is tagged.
