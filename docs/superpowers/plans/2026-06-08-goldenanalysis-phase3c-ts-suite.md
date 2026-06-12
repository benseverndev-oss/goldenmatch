# GoldenAnalysis Phase 3c — TypeScript Suite Analyzers Plan

> Use superpowers:executing-plans (inline; TS is safe to build/test locally). TDD-shaped.

**Goal:** Port the suite analyzers + adapters + suite entry points to TypeScript — `match.rates`, `cluster.distribution`, `quality.rollup`, the match/flow/check/pipe artifact adapters, and `analyzeMatch`/`analyzePipeline`. Behavioral parity with Python Phase 2a on hand-built artifacts.

**Architecture:** All edge-safe (`src/core`, no `node:` imports). Analyzers consume the SAME snake_case artifact keys the Python sibling reads (`scored_pairs`/`match_stats`/`clusters`/`findings`/`manifest`/`recall_certificate`/`__producer__`) so a serialized Python `PipeResult.artifacts` feeds the TS analyzers identically. Adapters are duck-typed functions (read properties off an `unknown` producer object — no goldenmatch/goldenflow/goldencheck/goldenpipe import). `analyze.ts` is refactored to share one `assembleReport` across the frame + suite entry points.

**Spec/Reference:** Python Phase 2a — `packages/python/goldenanalysis/goldenanalysis/{analyzers/{match_rates,cluster_dist,quality_rollup}.py,adapters/{match,flow,check,pipe}.py,_api.py,registry.py}`.

**Builds on:** Phase 3a/3b (merged) — `AnalysisReport`/`Metric` types, `analyze`, `aggregate` (has `histogram`/`quantile`), registry, render.

---

## Conventions
- From `packages/typescript/goldenanalysis/`. `npx vitest run`, `npx tsc --noEmit`, `npx tsup`.
- Branch `feat/goldenanalysis-ts-suite`, off `main` (has 3b). Commit per task.
- Parity gotchas (caught from the Python source/tests):
  - empty `clusters` `{}` is **truthy** in JS → guard with `Object.keys(clusters).length === 0`.
  - `findings_by_class` rows follow `Counter.most_common()` (count desc, ties in first-appearance order).
  - pipe dataset = `Path(source).stem`; `<DataFrame>` / non-string / empty → `"frame"`.
  - `analyzePipeline` fan-out iterates **sorted** `availableAnalyzers()`; `analyzeMatch` uses the explicit ordered pair `["match.rates","cluster.distribution"]`.
  - `cluster.record_count` uses `match_stats.total_records` only when present, else `sum(sizes)`.
  - recall cert normalizes to `{estimate, safe_bound}` accepting `{recall, recall_lower}` too.

## Tasks

### 3c.0 — `match.rates` analyzer (`src/core/analyzers/matchRates.ts`)
- [ ] Test (`tests/unit/matchRates.test.ts`): core metrics (`pair_count`/`match_rate`/`threshold`/`mean_pair_score`; recall omitted w/o cert); recall from `{estimate,safe_bound}` cert (both `higher_better`); estimate-only (safe_bound omitted); `score_histogram` table present; empty pairs degrades (no `mean_pair_score`). Mirror `test_match_rates.py`.
- [ ] Impl: `MatchRatesAnalyzer implements Analyzer`, consumes `["scored_pairs","match_stats"]`. Score = last element of each pair. `histogram(scores, 10)`. `certValues(cert)` accepts `{estimate|recall, safe_bound|recall_lower}`.
- [ ] Commit `feat(goldenanalysis-js): match.rates analyzer`

### 3c.1 — `cluster.distribution` analyzer (`src/core/analyzers/clusterDist.ts`)
- [ ] Test (`tests/unit/clusterDist.test.ts`): 4 clusters sizes [1,1,3,2] → count 4 / record_count 7 / singleton_ratio 0.5 / size_max 3 / reduction 1-4/7; histogram rows `[[1,2],[2,1],[3,1],["4+",0]]`; `total_records` in `match_stats` overrides record_count (→20); empty `clusters` `{}` emits nothing. Mirror `test_cluster_dist.py`.
- [ ] Impl: consumes `["clusters"]`. **Guard `Object.keys(clusters).length===0` → empty result.** Size = `c.size ?? c.members.length` for object, else `Number(c)`. `quantile(sizes, .5/.95)`, `max(sizes)`, discrete 1/2/3/4+ histogram.
- [ ] Commit `feat(goldenanalysis-js): cluster.distribution analyzer`

### 3c.2 — `quality.rollup` analyzer (`src/core/analyzers/qualityRollup.ts`)
- [ ] Test (`tests/unit/qualityRollup.test.ts`): findings+manifest → `findings_total` 3 (`lower_better`) / `columns_with_findings` 2 / `flow.rows_changed` 1200 / `flow.rules_fired` 2; `findings_by_class` {email_blanked:2, phone_unparseable:1}; `quality.score` from a stub `profile.healthScore` (→0.8, `higher_better`); degrades findings-only / manifest-only. Mirror `test_quality_rollup.py`.
- [ ] Impl: consumes `["findings","manifest"]`. `get(obj,key)` reads dict/object. `findings_by_class` via most-common-stable sort. `healthScore`: if `profile.healthScore`/`health_score` is a function, call with the per-column `{errors,warnings}` map; interpret return `[grade,score]` or `score`; `/100`; guard in try/catch. `flow.*` from `manifest.records` (`affected_rows` sum + count).
- [ ] Commit `feat(goldenanalysis-js): quality.rollup analyzer`

### 3c.3 — suite adapters (`src/core/adapters/{match,flow,check,pipe}.ts` + `index.ts`)
- [ ] Test (`tests/unit/adapters.test.ts`): `matchArtifacts(result, {certificate})` → `__producer__=goldenmatch`, clusters/scored_pairs/match_stats passthrough, cert normalized; reads `result.recallCertificate`/`recall_certificate` when no explicit cert (RecallEstimate `{recall, recall_lower:null}` → `{estimate, safe_bound:null}`); `flowArtifacts` → `goldenflow`, manifest passthrough, frame=df; `checkArtifacts(findings, profile)` pure → `goldencheck`; `pipeArtifacts` passthrough + dataset from `source` stem + cert normalize; `<DataFrame>` source → `frame`. Mirror `test_adapters_unit.py`.
- [ ] Impl: duck-typed functions returning `AnalyzerInput`. `normalizeCert` shared (match + pipe). `datasetFromSource(stem)`. The goldencheck-`load(df)` variant (lazy import) is OUT of scope — TS has no goldencheck dep; only the pure `checkArtifacts` (fromScan) seam ships (documented).
- [ ] Commit `feat(goldenanalysis-js): suite artifact adapters (match/flow/check/pipe)`

### 3c.4 — suite entry points + registry (`src/core/analyze.ts`, `src/core/registry.ts`)
- [ ] Test (`tests/unit/analyzeSuite.test.ts`): `analyzeMatch(result, {dataset})` → `analyzers_run` == {match.rates, cluster.distribution}, has `match.pair_count`+`cluster.count`, `source.producer=goldenmatch`; `analyzePipeline(result)` fans out to present-artifact analyzers (quality.rollup+cluster.distribution+match.rates, NOT frame.summary); manifest-only → `["quality.rollup"]`. Mirror `test_analyze_suite.py`.
- [ ] Impl: extract `assembleReport(input, names, options)` (producer = `artifacts["__producer__"] ?? "frame"`); refactor `analyze()` to delegate (frame path byte-identical: empty artifacts → producer "frame"). Add `artifactCompatibleAnalyzers(input)` (sorted; any `consumes` key present in `artifacts`). `analyzeMatch`/`analyzePipeline`. Registry: add the 3 analyzers to `FACTORIES`.
- [ ] Verify the 3a parity test still passes (`npx vitest run tests/parity`).
- [ ] Commit `feat(goldenanalysis-js): analyzeMatch + analyzePipeline + register suite analyzers`

### 3c.5 — exports + README
- [ ] `src/core/index.ts`: export the 3 analyzers, the adapters (`matchArtifacts`/`flowArtifacts`/`checkArtifacts`/`pipeArtifacts`/`normalizeCert`), and `analyzeMatch`/`analyzePipeline`. (No new tsup entry — all under `core`.)
- [ ] README: phase banner → 3c; a "Suite analyzers" section (match/cluster/quality + analyzeMatch/analyzePipeline + the snake_case artifact-key contract).
- [ ] Commit `docs(goldenanalysis-js): README suite analyzers + exports`

### 3c.6 — verify + push
- [ ] `npx vitest run` green (incl. the 3a parity + all 3b cross-run tests); `npx tsc --noEmit` clean; `npx tsup` build clean. No new deps.
- [ ] Push `feat/goldenanalysis-ts-suite` (auth dance); PR vs main; babysit (ci-required + CodeQL); merge.

## Acceptance
- [ ] `match.rates`/`cluster.distribution`/`quality.rollup` match the Python 2a unit scenarios; adapters duck-type the same shapes; `analyzeMatch`/`analyzePipeline` select + assemble identically (sorted fan-out; explicit match pair).
- [ ] Frame path unchanged (3a parity byte-identical; producer reads `__producer__ ?? "frame"`). Edge-safe; no new deps. tsc + tsup + vitest green.

### Deferred
goldencheck-js `load(df)` adapter variant (needs a goldencheck-js dep). P4 Rust accelerator. P5 GoldenPipe stage + MCP + publish.
