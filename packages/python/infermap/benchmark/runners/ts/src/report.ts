// Build and validate report.json from case results.
// Mirrors infermap_bench/report.py.

import { readFileSync, writeFileSync } from "node:fs";
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import {
  expectedCalibrationError,
  macroMean,
  microF1,
  type Prediction,
} from "./metrics.js";
import { REPORT_VERSION } from "./index.js";

export interface CaseResult {
  caseId: string;
  category: string;
  subcategory: string;
  difficulty: string;
  tags: string[];
  top1: number;
  f1: number;
  mrr: number;
  tp: number;
  fp: number;
  fn: number;
  predictions: Prediction[];
  failed: boolean;
  failureReason: string | null;
}

interface MetricSet {
  f1: number;
  top1: number;
  mrr: number;
  ece: number;
  n: number;
}

export interface Report {
  version: 1;
  language: "python" | "typescript";
  infermap_version: string;
  runner_version: string;
  ran_at: string;
  duration_seconds: number;
  scorecard: {
    overall: MetricSet;
    by_difficulty: Record<string, MetricSet>;
    by_category: Record<string, MetricSet>;
    by_tag: Record<string, MetricSet>;
  };
  per_case: Array<{
    id: string;
    f1: number;
    top1: number;
    mrr: number;
    expected_n: number;
    predicted_n: number;
    true_positives: number;
    false_positives: number;
    false_negatives: number;
    failed: boolean;
    failure_reason: string | null;
  }>;
  failed_cases: string[];
}

export interface BuildReportOptions {
  language: "python" | "typescript";
  infermapVersion: string;
  runnerVersion: string;
  durationSeconds: number;
}

const round6 = (n: number): number => Math.round(n * 1e6) / 1e6;

function metricSet(results: CaseResult[]): MetricSet {
  const counts = results.map((r) => ({ tp: r.tp, fp: r.fp, fn: r.fn }));
  const allPredictions: Prediction[] = [];
  for (const r of results) allPredictions.push(...r.predictions);
  return {
    f1: round6(microF1(counts)),
    top1: round6(macroMean(results.map((r) => r.top1))),
    mrr: round6(macroMean(results.map((r) => r.mrr))),
    ece: round6(expectedCalibrationError(allPredictions)),
    n: results.length,
  };
}

function bySingleKey(
  results: CaseResult[],
  key: (r: CaseResult) => string,
): Record<string, MetricSet> {
  const buckets = new Map<string, CaseResult[]>();
  for (const r of results) {
    const k = key(r);
    const arr = buckets.get(k) ?? [];
    arr.push(r);
    buckets.set(k, arr);
  }
  const sortedKeys = [...buckets.keys()].sort();
  const out: Record<string, MetricSet> = {};
  for (const k of sortedKeys) out[k] = metricSet(buckets.get(k)!);
  return out;
}

function byTag(results: CaseResult[]): Record<string, MetricSet> {
  const buckets = new Map<string, CaseResult[]>();
  for (const r of results) {
    for (const tag of r.tags) {
      const arr = buckets.get(tag) ?? [];
      arr.push(r);
      buckets.set(tag, arr);
    }
  }
  const sortedKeys = [...buckets.keys()].sort();
  const out: Record<string, MetricSet> = {};
  for (const k of sortedKeys) out[k] = metricSet(buckets.get(k)!);
  return out;
}

export function buildReport(results: CaseResult[], opts: BuildReportOptions): Report {
  return {
    version: REPORT_VERSION as 1,
    language: opts.language,
    infermap_version: opts.infermapVersion,
    runner_version: opts.runnerVersion,
    ran_at: new Date().toISOString(),
    duration_seconds: Math.round(opts.durationSeconds * 1e4) / 1e4,
    scorecard: {
      overall: metricSet(results),
      by_difficulty: bySingleKey(results, (r) => r.difficulty),
      by_category: bySingleKey(results, (r) => r.category),
      by_tag: byTag(results),
    },
    per_case: results.map((r) => ({
      id: r.caseId,
      f1: round6(r.f1),
      top1: round6(r.top1),
      mrr: round6(r.mrr),
      expected_n: r.tp + r.fn,
      predicted_n: r.tp + r.fp,
      true_positives: r.tp,
      false_positives: r.fp,
      false_negatives: r.fn,
      failed: r.failed,
      failure_reason: r.failureReason,
    })),
    failed_cases: results.filter((r) => r.failed).map((r) => r.caseId),
  };
}

export function validateReport(report: unknown, schemaPath: string): void {
  const schema = JSON.parse(readFileSync(schemaPath, "utf8"));
  const AjvCtor = (Ajv2020 as unknown as { default?: typeof Ajv2020 }).default ?? Ajv2020;
  const ajv = new (AjvCtor as any)({ allErrors: true });
  const addFormatsFn =
    (addFormats as unknown as { default?: typeof addFormats }).default ?? addFormats;
  (addFormatsFn as any)(ajv);
  const validate = ajv.compile(schema);
  if (!validate(report)) {
    const errors = (validate.errors ?? [])
      .map((e: any) => `${e.instancePath ?? ""} ${e.message ?? ""}`.trim())
      .join(", ");
    throw new Error(`Report failed schema validation: ${errors}`);
  }
}

export function writeReport(report: Report, outputPath: string, schemaPath: string): void {
  validateReport(report, schemaPath);
  writeFileSync(
    outputPath,
    JSON.stringify(report, null, 2) + "\n",
    "utf8",
  );
}
