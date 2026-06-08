# goldenanalysis (TypeScript)

Read-only cross-cutting analysis, metrics, and reporting for the Golden Suite — the
TypeScript port of the Python [`goldenanalysis`](../../python/goldenanalysis) package.

> **Phase 3a** ships the generic **frame path** with cross-surface parity; **Phase
> 3b** adds the **cross-run layer** (`ReportHistory`, regression detection, narrative,
> `trend`/`regressions` CLI). Suite analyzers land in a later phase.

## Quickstart

```ts
import { analyze, toMarkdown } from "goldenanalysis";

const rows = [
  { name: "Alice", email: "a@x.com", age: 30 },
  { name: "Alice", email: "a@x.com", age: 30 },
  { name: "Bob", email: null, age: 41 },
];

const report = analyze(rows, ["frame.summary"], { dataset: "customers" });
console.log(toMarkdown(report));
report.metrics; // row_count, column_count, null_ratio_mean, duplicate_row_ratio, memory_bytes
```

CLI:

```bash
goldenanalysis-js report customers.json --format markdown   # or a .csv
goldenanalysis-js report customers.csv --analyzers frame.summary
```

## Cross-run (trend + regressions)

`ReportHistory` is an append-only **JSONL** log of `AnalysisReport`s, keyed by
`(analysisName, dataset, runId)` (last-wins). It powers trend series and
direction-aware regression detection across runs. The pure decision logic
(`detectRegressions` / `buildTrend` / `buildNarrative` + the models) is edge-safe in
`goldenanalysis/core`; the file-backed store needs `node:fs` and lives in
`goldenanalysis/node`.

```ts
import { ReportHistory } from "goldenanalysis/node";

const hist = new ReportHistory({ path: ".golden/analysis.jsonl" });
hist.append(report); // after each run

// A per-metric 2% gate on recall catches a drop a global 10% gate would miss.
const flagged = hist.detectRegressions("customers", {
  baseline: "rolling_median", // or "previous" / "last_known_good" / a pinned runId
  policy: { defaultPct: 10, perMetric: { "match.recall_safe_bound": 2 } },
});
const series = hist.trend("cluster.singleton_ratio", "customers", { lastN: 30 });
```

Regression flags are **direction-aware**: a `higher_better` metric flags only on a
drop, `lower_better` only on a rise, `neutral` either way. `rolling_median` is immune
to one noisy night where `previous` would alternately flag and un-flag.

CLI:

```bash
# trend of one metric across the run history
goldenanalysis-js trend cluster.singleton_ratio --history .golden/analysis.jsonl --dataset customers

# detect regressions in the latest run vs history (exit 1 on any flag, for CI gating)
goldenanalysis-js regressions --history .golden/analysis.jsonl --dataset customers \
  --policy "match.recall_safe_bound=2,*=10" --fail-on-regression
```

> **JSONL only.** Node 20 has no stable built-in SQLite (`node:sqlite` is experimental
> in 22+); the Python sibling's optional SQLite backend is a documented follow-up.
> Python's *default* backend is also JSONL, so the surface parity holds.

## Cross-surface parity

The `AnalysisReport` / `Metric` / `AnalysisTable` **wire types use snake_case** (the
documented exception in `packages/typescript/CLAUDE.md`) so reports cross the JSON
wire between the Python and TypeScript surfaces without remapping.

`tests/parity/frameSummary.parity.test.ts` asserts the TS `frame.summary` report is
**byte-identical to the Python-locked `report_frame_summary.json`** on the
engine-independent metrics: `frame.row_count`, `frame.column_count`,
`frame.null_ratio_mean`, `frame.duplicate_row_ratio`, and the `per_column` columns
`column` / `null_ratio` / `n_unique`.

**Out of the parity contract** (engine-specific, emitted but not asserted):
`frame.memory_bytes` (the Python sibling uses polars `estimated_size()`) and the
`per_column` `dtype` column (polars dtype names). `tests/fixtures/report_frame_summary.json`
is a byte-identical copy of the Python fixture and must stay in sync.

## GoldenCheck vs GoldenAnalysis

GoldenAnalysis is **read-only** and **cross-cutting** — it consumes any stage's
outputs (including GoldenCheck's) and reports across them. It depends on other
packages' types, never the reverse; it does not replace GoldenCheck's ingest-time
profiling.

## Develop

```bash
npm run build      # tsup -> dist/ (ESM + CJS + .d.ts)
npm test           # vitest
npm run typecheck  # tsc --noEmit
```

## License

MIT.
