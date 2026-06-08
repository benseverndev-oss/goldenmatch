/**
 * `frame.summary` — generic frame metrics, parity with the Python analyzer.
 *
 * Emits the Appendix-A metric set + a `per_column` table. NOTE: `frame.memory_bytes`
 * and the `per_column` `dtype` column are ENGINE-SPECIFIC (the Python sibling uses
 * polars `estimated_size()` / polars dtype names) and are emitted but are OUT of the
 * cross-surface parity contract — see the parity test + README.
 */

import {
  columns,
  duplicateRowRatio,
  nUnique,
  nullRatioPerColumn,
} from "../aggregate.js";
import type { Analyzer, AnalyzerInfo, AnalyzerInput, AnalyzerResult, AnalysisTable, FrameRows, Metric } from "../types.js";

const PRODUCES = [
  "frame.row_count",
  "frame.column_count",
  "frame.null_ratio_mean",
  "frame.duplicate_row_ratio",
  "frame.memory_bytes",
];

function dtypeLabel(rows: FrameRows, col: string): string {
  const kinds = new Set<string>();
  for (const row of rows) {
    const v = row[col];
    if (v === null || v === undefined) continue;
    kinds.add(typeof v);
  }
  if (kinds.size === 0) return "null";
  if (kinds.size > 1) return "mixed";
  return [...kinds][0]!;
}

export class FrameSummaryAnalyzer implements Analyzer {
  readonly info: AnalyzerInfo = {
    name: "frame.summary",
    consumes: ["frame"],
    produces: PRODUCES,
  };

  run(input: AnalyzerInput): AnalyzerResult {
    const rows: FrameRows = input.frame ?? [];
    const cols = columns(rows);
    const nRows = rows.length;
    const nCols = cols.length;
    const nullRatios = nullRatioPerColumn(rows, cols);
    const nullMean = nCols > 0 ? cols.reduce((acc, c) => acc + (nullRatios[c] ?? 0), 0) / nCols : 0;
    const dupRatio = duplicateRowRatio(rows, cols);
    // Portable byte estimate (NOT parity-asserted; the Python sibling uses polars).
    const memoryBytes = new TextEncoder().encode(JSON.stringify(rows)).length;

    const metrics: Metric[] = [
      { key: "frame.row_count", value: nRows, unit: "rows", direction: "neutral" },
      { key: "frame.column_count", value: nCols, unit: "columns", direction: "neutral" },
      { key: "frame.null_ratio_mean", value: nullMean, unit: "ratio", direction: "lower_better" },
      { key: "frame.duplicate_row_ratio", value: dupRatio, unit: "ratio", direction: "lower_better" },
      { key: "frame.memory_bytes", value: memoryBytes, unit: "bytes", direction: "neutral" },
    ];

    const perColumn: AnalysisTable = {
      name: "per_column",
      columns: ["column", "dtype", "null_ratio", "n_unique"],
      rows: cols.map((col) => [col, dtypeLabel(rows, col), nullRatios[col] ?? 0, nUnique(rows, col)]),
    };

    return { metrics, tables: [perColumn] };
  }
}
