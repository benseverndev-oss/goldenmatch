/**
 * GoldenAnalysis wire types.
 *
 * Field naming is **snake_case** (not the workspace's usual camelCase) because the
 * `AnalysisReport` / `Metric` / `AnalysisTable` structures cross the JSON wire
 * between the Python and TypeScript surfaces without remapping. The Python sibling
 * is `packages/python/goldenanalysis/goldenanalysis/models/report.py`. Cross-language
 * parity here is more valuable than idiomatic case style — see
 * `packages/typescript/CLAUDE.md`.
 */

/** Cross-surface schema contract anchor. Bumping requires a parity-test update. */
export const SCHEMA_VERSION = 1 as const;

export type Direction = "higher_better" | "lower_better" | "neutral";

/** A single named measurement over an artifact. */
export interface Metric {
  readonly key: string; // dotted, stable: "frame.row_count"
  readonly value: number | string;
  readonly unit?: string | null;
  readonly direction: Direction;
}

/** A small, report-embeddable table. */
export interface AnalysisTable {
  readonly name: string;
  readonly columns: readonly string[];
  readonly rows: ReadonlyArray<ReadonlyArray<unknown>>;
}

/** The unified, exportable output of one analysis run. */
export interface AnalysisReport {
  readonly schema_version: number;
  readonly run_id: string;
  readonly generated_at: string; // ISO 8601
  readonly source: Record<string, string>;
  readonly metrics: readonly Metric[];
  readonly tables: readonly AnalysisTable[];
  readonly narrative: string | null;
  readonly analyzers_run: readonly string[];
}

// --- Analyzer I/O (internal; camelCase is fine — not wire types) ----------

export interface AnalyzerInfo {
  readonly name: string;
  readonly consumes: readonly string[];
  readonly produces: readonly string[];
}

/** Generic frame input: an array of row objects (edge-safe; no polars). */
export type FrameRows = ReadonlyArray<Record<string, unknown>>;

export interface AnalyzerInput {
  readonly dataset: string;
  readonly frame?: FrameRows;
  readonly artifacts: Record<string, unknown>;
}

export interface AnalyzerResult {
  readonly metrics: readonly Metric[];
  readonly tables: readonly AnalysisTable[];
}

export interface Analyzer {
  readonly info: AnalyzerInfo;
  run(input: AnalyzerInput): AnalyzerResult;
}
