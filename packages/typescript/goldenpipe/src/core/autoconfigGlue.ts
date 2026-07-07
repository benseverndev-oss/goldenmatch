/**
 * autoconfigGlue.ts — impure host bracket for the auto-config brain
 * (InferMap detect + Row[] profiling in; PipelineConfig out; refuse-on-RED).
 * TS analogue of goldenpipe/autoconfig_glue.py. The pure decision core is
 * autoconfigPlanner.ts.
 */
import type { Row } from "./index.js";
import {
  bandOf,
  type PipeProfile,
  type ComplexityProfile,
  type PlannerInput,
  type PipePlan,
} from "./autoconfigPlanner.js";
import { detectDomainDetailed } from "infermap";
import { PipeNotConfidentError } from "./errors.js";
import { makePipelineConfig, makeStageSpec, type PipelineConfig } from "./models.js";

export const REFUSE_ROW_THRESHOLD = 100_000;

export function profileContext(rows: readonly Row[]): PipeProfile {
  if (rows.length === 0) {
    return {
      nRows: 0, nCols: 0, columnNames: [], dtypes: [],
      inferredDomain: null, domainConfidence: 0,
    };
  }
  const columnNames = Object.keys(rows[0]!);
  const det = detectDomainDetailed({ columns: columnNames });
  return {
    nRows: rows.length,
    nCols: columnNames.length,
    columnNames,
    dtypes: [],
    inferredDomain: det.domain,
    domainConfidence: det.domain !== null ? det.score : 0,
  };
}

export function profileComplexity(rows: readonly Row[]): ComplexityProfile {
  const nRows = rows.length;
  if (nRows === 0) return { maxNullDensity: 0, meanNullDensity: 0 };
  const columnNames = Object.keys(rows[0]!);
  if (columnNames.length === 0) return { maxNullDensity: 0, meanNullDensity: 0 };
  const fractions = columnNames.map((c) => {
    let nulls = 0;
    for (const r of rows) {
      const v = r[c];
      if (v === null || v === undefined) nulls++;
    }
    return nulls / nRows;
  });
  // Loop-max, NOT Math.max(...spread) (ts-no-spread-math-min-max is error-severity).
  let maxNullDensity = 0;
  for (const f of fractions) if (f > maxNullDensity) maxNullDensity = f;
  return {
    maxNullDensity,
    meanNullDensity: fractions.reduce((a, b) => a + b, 0) / fractions.length,
  };
}

export function buildPlannerInput(rows: readonly Row[]): PlannerInput {
  return { runtime: profileContext(rows), complexity: profileComplexity(rows) };
}

export function enforceConfidence(plan: PipePlan, runtime: PipeProfile): void {
  if (bandOf(plan.confidence) !== "red") return;
  if (runtime.nRows >= REFUSE_ROW_THRESHOLD) {
    throw new PipeNotConfidentError(
      `auto-config not confident (rule=${plan.ruleName}, confidence=${plan.confidence}) ` +
        `on ${runtime.nRows} rows; supply an explicit pipeline config or reduce the input size. ` +
        `evidence=${JSON.stringify(plan.evidence)}`,
    );
  }
}

export function planToConfig(
  plan: PipePlan,
  available: Record<string, unknown>,
  identityOpts: Record<string, unknown>,
): PipelineConfig {
  const stages = plan.stages
    .filter((s) => s.name in available)
    .map((s) => makeStageSpec({ use: s.name, config: s.config }));
  const IDENTITY = "goldenmatch.identity_resolve";
  if (Object.keys(identityOpts).length > 0 && IDENTITY in available) {
    stages.push(makeStageSpec({ use: IDENTITY, config: identityOpts }));
  }
  return makePipelineConfig({ pipeline: "auto", stages });
}
