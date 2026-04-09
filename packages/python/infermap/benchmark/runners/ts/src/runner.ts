// Orchestrate per-case benchmark execution.
// Mirrors infermap_bench/runner.py.
import { MapEngine } from "infermap";
import type { CaseData } from "./types.js";
import type { CaseResult } from "./report.js";
import {
  extractPredictions,
  f1PerCase,
  meanReciprocalRank,
  topOneAccuracy,
  type MetricInput,
} from "./metrics.js";

export interface RunOptions {
  minConfidence?: number;
  sampleSize?: number;
  failureBudget?: number;
}

export class FailureBudgetExceededError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "FailureBudgetExceededError";
  }
}

const DEFAULTS: Required<RunOptions> = {
  minConfidence: 0.3,
  sampleSize: 500,
  failureBudget: 0.10,
};

/** Factory for the engine. Exported so tests can inject a fake. */
export function makeEngine(opts: Required<RunOptions>): MapEngine {
  return new MapEngine({
    minConfidence: opts.minConfidence,
    returnScoreMatrix: true,
  });
}

/** Run the engine on one case and score against expected mappings.
 *
 * The try block intentionally wraps ONLY the engine call — mirrors the Python
 * runner. Metric computation bugs would otherwise be silently swallowed and
 * scored as engine failures, masking real defects.
 */
export function scoreCase(case_: CaseData, engine: MapEngine): CaseResult {
  let result;
  try {
    result = engine.mapSchemas(case_.sourceSchema, case_.targetSchema);
  } catch (err) {
    const name = err instanceof Error ? err.name : "Error";
    const message = err instanceof Error ? err.message : String(err);
    return {
      caseId: case_.id,
      category: case_.category,
      subcategory: case_.subcategory,
      difficulty: case_.expectedDifficulty,
      tags: [...case_.tags],
      top1: 0,
      f1: 0,
      mrr: 0,
      tp: 0,
      fp: 0,
      fn: case_.expected.mappings.length,
      predictions: [],
      failed: true,
      failureReason: `${name}: ${message}`,
    };
  }

  const inp: MetricInput = {
    sourceFields: case_.sourceSchema.fields.map((f) => f.name),
    targetFields: case_.targetSchema.fields.map((f) => f.name),
    expectedMappings: case_.expected.mappings.map((m) => ({ source: m.source, target: m.target })),
    expectedUnmappedSource: [...case_.expected.unmappedSource],
    expectedUnmappedTarget: [...case_.expected.unmappedTarget],
    actualMappings: result.mappings.map((m) => ({
      source: m.source,
      target: m.target,
      confidence: m.confidence,
    })),
    scoreMatrix: result.scoreMatrix ?? {},
    minConfidence: engine.minConfidence,
  };

  const { tp, fp, fn } = f1PerCase(inp);
  const denom = 2 * tp + fp + fn;
  const caseF1 = denom > 0 ? (2 * tp) / denom : 1;

  return {
    caseId: case_.id,
    category: case_.category,
    subcategory: case_.subcategory,
    difficulty: case_.expectedDifficulty,
    tags: [...case_.tags],
    top1: topOneAccuracy(inp),
    f1: caseF1,
    mrr: meanReciprocalRank(inp),
    tp,
    fp,
    fn,
    predictions: extractPredictions(inp),
    failed: false,
    failureReason: null,
  };
}

export function abortIfOverBudget(failed: number, total: number, budget: number): void {
  if (total === 0) return;
  if (failed / total > budget) {
    throw new FailureBudgetExceededError(
      `${failed}/${total} cases failed ` +
        `(${(failed / total * 100).toFixed(1)}%), exceeds budget of ${(budget * 100).toFixed(0)}%. ` +
        `The engine is likely broken.`,
    );
  }
}

export function runCases(
  cases: CaseData[],
  options: RunOptions = {},
  engineFactory: (opts: Required<RunOptions>) => MapEngine = makeEngine,
): CaseResult[] {
  const opts: Required<RunOptions> = { ...DEFAULTS, ...options };
  const engine = engineFactory(opts);

  const results: CaseResult[] = [];
  for (const case_ of cases) {
    results.push(scoreCase(case_, engine));
  }

  const failedCount = results.filter((r) => r.failed).length;
  abortIfOverBudget(failedCount, results.length, opts.failureBudget);

  return results;
}
