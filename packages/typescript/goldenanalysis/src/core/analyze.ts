/**
 * Top-level analyze entry points — resolve analyzers, run them over an artifact,
 * assemble one `AnalysisReport`.
 *
 * - `analyze(rows, ...)` — the generic frame path.
 * - `analyzeMatch(result, ...)` / `analyzePipeline(result)` — suite paths over a
 *   GoldenMatch `DedupeResult`-like / GoldenPipe `PipeResult`-like object.
 *
 * Parity with `packages/python/goldenanalysis/goldenanalysis/_api.py`.
 */

import { matchArtifacts } from "./adapters/match.js";
import { pipeArtifacts } from "./adapters/pipe.js";
import { availableAnalyzers, frameCompatibleAnalyzers, loadAnalyzer } from "./registry.js";
import type { AnalysisReport, AnalysisTable, AnalyzerInput, FrameRows, Metric } from "./types.js";
import { SCHEMA_VERSION } from "./types.js";

export interface AnalyzeOptions {
  readonly dataset?: string;
  readonly runId?: string;
  readonly generatedAt?: string;
}

export interface AnalyzeMatchOptions extends AnalyzeOptions {
  readonly certificate?: unknown;
}

/**
 * Run `names` over `input` and assemble one `AnalysisReport`. Shared by every entry
 * point. Names requested but not discoverable are recorded in `source.unavailable`
 * rather than raising — the report says what it could and couldn't compute. The
 * producer is read off `artifacts.__producer__` (the frame path has none → "frame").
 */
function assembleReport(
  input: AnalyzerInput,
  names: readonly string[],
  options: AnalyzeOptions = {},
): AnalysisReport {
  const dataset = input.dataset;
  const discoverable = new Set(availableAnalyzers());

  const ran: string[] = [];
  const unavailable: string[] = [];
  const metrics: Metric[] = [];
  const tables: AnalysisTable[] = [];
  for (const name of names) {
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
  const producerRaw = input.artifacts["__producer__"];
  const producer = typeof producerRaw === "string" ? producerRaw : "frame";
  const source: Record<string, string> = { dataset, producer };
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

/**
 * Run `analyzers` over `rows` and return a single `AnalysisReport`. `analyzers`
 * omitted defaults to every frame-compatible analyzer. Names requested but not
 * discoverable land in `source.unavailable`.
 */
export function analyze(
  rows: FrameRows,
  analyzers?: readonly string[],
  options: AnalyzeOptions = {},
): AnalysisReport {
  const dataset = options.dataset ?? "frame";
  const input: AnalyzerInput = { dataset, frame: rows, artifacts: {} };
  const requested = analyzers ?? frameCompatibleAnalyzers();
  return assembleReport(input, requested, options);
}

/** Discoverable analyzers at least one of whose `consumes` keys is present in
 * `input.artifacts` — the fan-out selector for `analyzePipeline` (sorted order). */
export function artifactCompatibleAnalyzers(input: AnalyzerInput): string[] {
  const present = new Set(Object.keys(input.artifacts));
  return availableAnalyzers().filter((name) =>
    loadAnalyzer(name).info.consumes.some((key) => present.has(key)),
  );
}

/**
 * Analyze a GoldenMatch `DedupeResult`-like object: `match.rates` + `cluster.distribution`.
 * `certificate` (optional) is a recall certificate (`{estimate, safe_bound}` or a
 * `{recall, recall_lower}` shape); absent → the recall metrics are omitted.
 */
export function analyzeMatch(result: unknown, options: AnalyzeMatchOptions = {}): AnalysisReport {
  const input = matchArtifacts(result, options);
  return assembleReport(input, ["match.rates", "cluster.distribution"], options);
}

/**
 * Analyze a GoldenPipe `PipeResult`-like object, fanning out to every analyzer whose
 * consumed artifacts are present in `result.artifacts`.
 */
export function analyzePipeline(result: unknown, options: AnalyzeOptions = {}): AnalysisReport {
  const input = pipeArtifacts(result, options);
  return assembleReport(input, artifactCompatibleAnalyzers(input), options);
}
