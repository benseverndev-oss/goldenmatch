/** Top-level `analyze()` — run analyzers over frame rows, assemble an AnalysisReport. */

import { availableAnalyzers, frameCompatibleAnalyzers, loadAnalyzer } from "./registry.js";
import type {
  AnalysisReport,
  AnalysisTable,
  AnalyzerInput,
  FrameRows,
  Metric,
} from "./types.js";
import { SCHEMA_VERSION } from "./types.js";

export interface AnalyzeOptions {
  readonly dataset?: string;
  readonly runId?: string;
  readonly generatedAt?: string;
}

export function analyze(
  rows: FrameRows,
  analyzers?: readonly string[],
  options: AnalyzeOptions = {},
): AnalysisReport {
  const dataset = options.dataset ?? "frame";
  const input: AnalyzerInput = { dataset, frame: rows, artifacts: {} };

  const requested = analyzers ?? frameCompatibleAnalyzers();
  const discoverable = new Set(availableAnalyzers());

  const ran: string[] = [];
  const unavailable: string[] = [];
  const metrics: Metric[] = [];
  const tables: AnalysisTable[] = [];
  for (const name of requested) {
    if (!discoverable.has(name)) {
      unavailable.push(name);
      continue;
    }
    const result = loadAnalyzer(name).run(input);
    metrics.push(...result.metrics);
    tables.push(...result.tables);
    ran.push(name);
  }

  const generatedAt = options.generatedAt ?? new Date().toISOString();
  const runId = options.runId ?? `${generatedAt}#${dataset}`;
  const source: Record<string, string> = { dataset, producer: "frame" };
  if (unavailable.length > 0) source["unavailable"] = unavailable.join(",");

  return {
    schema_version: SCHEMA_VERSION,
    run_id: runId,
    generated_at: generatedAt,
    source,
    metrics,
    tables,
    narrative: null,
    analyzers_run: ran,
  };
}
