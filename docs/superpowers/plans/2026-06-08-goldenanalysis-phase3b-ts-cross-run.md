# GoldenAnalysis Phase 3b — TypeScript Cross-Run Plan

> Use superpowers:executing-plans (inline; TS is safe to build/test locally). TDD-shaped.

**Goal:** Port the cross-run layer to TypeScript — regression decision logic + narrative (edge-safe, `src/core`) and a JSONL-backed `ReportHistory` (`src/node`), plus the `trend`/`regressions` CLI. Behavioral parity with Python 2b (same flags on the same report sequence).

**Architecture:** The pure decision logic + report-level `detectRegressions`/`buildTrend` + models live in `src/core` (edge-safe). The `ReportHistory` that persists to a JSONL file lives in `src/node` (uses `node:fs`). **JSONL only** — Node 20 has no stable built-in SQLite (`node:sqlite` is experimental in 22+); the SQLite backend is a documented follow-up (Python's default IS jsonl, so the surface parity holds). The cross-run models (`RegressionPolicy`/`Regression`/`TrendSeries`/`Baseline`) are camelCase TS-idiomatic — they are NOT wire types (the wire type is `AnalysisReport`, already snake_case).

**Spec/Reference:** Python Phase 2b — `packages/python/goldenanalysis/goldenanalysis/{_regressions.py,history.py,narrative.py}` + the spec's `ReportHistory`/`Baseline`/`RegressionPolicy` blocks.

**Builds on:** Phase 3a (merged) — the `AnalysisReport`/`Metric` types, `analyze`, `render.toMarkdown`.

---

## Conventions
- From `packages/typescript/goldenanalysis/`. `npx vitest run`, `npx tsc --noEmit`, `npx tsup`.
- Branch `feat/goldenanalysis-ts-cross-run`, off `main` (has 3a). Commit per task.

## Tasks

### 3b.0 — cross-run models + pure regression logic (`src/core/regressions.ts`)
- [ ] Test (`tests/unit/regressions.test.ts`): `baselineValue([0.97,0.96,0.98,0.97,0.97,0.96,0.97], "rolling_median", 7)===0.97`; `previous`===last; `isRegression("higher_better", 0.97, 0.89, 2)===true` and `===false` at gate 10; direction-aware (lower_better flags on rise; neutral either way); rolling_median ignores one noisy night.
- [ ] Impl: `type Baseline = "previous"|"rolling_median"|"last_known_good"|string`. `interface RegressionPolicy { defaultPct: number; perMetric: Record<string, number> }` + a `thresholdFor(policy, key)` helper. `interface Regression { metric; baseline; current; deltaPct; flagged; direction }`. `interface TrendSeries { metricKey; dataset; points: [string, number][] }`. `baselineValue`, `deltaPct`, `isRegression(direction, base, cur, pct)`.
- [ ] Commit `feat(goldenanalysis-js): cross-run models + regression decision logic`

### 3b.1 — report-level cross-run functions (`src/core/history.ts`)
- [ ] Test (`tests/unit/history.test.ts`): over a hand-built `AnalysisReport[]` (7 healthy + 1 regressed, Maya scenario), `detectRegressions(reports, { baseline: "rolling_median", policy: { defaultPct: 10, perMetric: { "match.recall_safe_bound": 2 } } })` flags `match.recall_safe_bound` (a 10% gate misses it) + `cluster.singleton_ratio`; `buildTrend(reports, "cluster.singleton_ratio", "customers")` returns ordered points; `baseline:"previous"` over a post-step pair flags nothing.
- [ ] Impl: edge-safe. `numericValue(report, key)`; `buildTrend(reports, key, dataset, lastN=30)`; `detectRegressions(reports, opts)` — LATEST report is current, prior is history; per numeric metric compute baseline over the prior series + flag via policy/direction; return flagged. (Filtering by dataset happens in the node ReportHistory.)
- [ ] Commit `feat(goldenanalysis-js): report-level detectRegressions + buildTrend (edge-safe)`

### 3b.2 — narrative (`src/core/narrative.ts`)
- [ ] Test (`tests/unit/narrative.test.ts`): `buildNarrative(report, regressions)` names the worst flagged regression (largest |deltaPct|) + co-movers + top `findings_by_class`; no-regression path is a neutral summary.
- [ ] Impl: deterministic template (mirror Python). ASCII-clean.
- [ ] Commit `feat(goldenanalysis-js): narrative generation`

### 3b.3 — markdown regression callout (`src/core/render.ts`)
- [ ] Test (extend `tests/unit/analyze.test.ts` or new): `toMarkdown(report, regressions)` adds a `> WARNING: N regression(s) flagged.` callout + a `Δ vs baseline` column; without regressions, **byte-identical** to 3a output (existing tests stay green).
- [ ] Impl: add optional `regressions` param to `toMarkdown` (default undefined → current behavior). Use `.trimEnd()` (no `\s+$` — ReDoS, see 3a #813).
- [ ] Commit `feat(goldenanalysis-js): markdown regression callout + delta column`

### 3b.4 — `ReportHistory` (JSONL, `src/node/history.ts` + `src/node/index.ts`)
- [ ] Test (`tests/node/history.test.ts`): with a tmp `.jsonl` path — `append` then `reports(dataset)` in order; idempotent upsert per `(analysisName, dataset, runId)`; `trend(...)`; `detectRegressions(...)` flags the scenario; a fresh handle on the same file sees persisted reports (durability).
- [ ] Impl: `ReportHistory` class (constructor `{ path }`), `node:fs` append-only jsonl (one record/line `{analysisName, dataset, runId, schemaVersion, recordedAt, report}`), read = parse all lines, last-wins per key; `reports`/`trend`/`detectRegressions` delegate to `src/core/history`. Export from `src/node/index.ts`.
- [ ] Commit `feat(goldenanalysis-js): ReportHistory (jsonl, node) + node entry`

### 3b.5 — wire `./node` export + CLI trend/regressions
- [ ] `package.json`: add `"./node"` export (mirror goldenpipe). `tsup.config.ts`: add `"node/index": "src/node/index.ts"`.
- [ ] `src/cli.ts`: add `trend` + `regressions` commands (open `ReportHistory({ path: --history })`; `--metric`/`--dataset`/`--last`; `--baseline`/`--policy` (JSON or `key=pct`)/`--fail-on-regression`). Test (extend `tests/unit/cli.test.ts` or `tests/node/`): seed a tmp jsonl, assert `trend`/`regressions` output.
- [ ] Commit `feat(goldenanalysis-js): trend/regressions CLI + ./node export`

### 3b.6 — docs + verify + push
- [ ] README: a "Cross-run" section (ReportHistory, detectRegressions, CLI). CHANGELOG-equivalent note if present.
- [ ] Verify: `npx vitest run` green; `npx tsc --noEmit` clean; `npx tsup` build clean. `pnpm-lock.yaml` unchanged (no new deps).
- [ ] Push `feat/goldenanalysis-ts-cross-run` (auth dance); PR vs main.

## Acceptance
- [ ] Edge-safe core (regression logic + narrative + render free of `node:`); `ReportHistory` (jsonl) in node; **no new deps**.
- [ ] `detectRegressions` matches the Python 2b scenario (per-metric 2% gate catches a recall drop a 10% gate misses; direction-aware; previous-over-post-step flags nothing); `trend` ordered; narrative + markdown callout work; no-regression markdown byte-identical to 3a.
- [ ] CLI `trend`/`regressions` real; tsc + tsup + vitest green.

### Deferred
SQLite backend (node lacks stable built-in sqlite). TS suite analyzers (3c). npm publish workflow.
