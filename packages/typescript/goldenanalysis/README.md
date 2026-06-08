# goldenanalysis (TypeScript)

Read-only cross-cutting analysis, metrics, and reporting for the Golden Suite — the
TypeScript port of the Python [`goldenanalysis`](../../python/goldenanalysis) package.

> **Phase 3a** ships the generic **frame path** with cross-surface parity. Suite
> analyzers and the cross-run layer (`ReportHistory` / regressions) land in later
> phases.

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
