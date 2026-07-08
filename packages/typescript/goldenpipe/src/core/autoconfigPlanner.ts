/**
 * autoconfigPlanner.ts — pure-TS auto-config BRAIN (decision core), ported from
 * autoconfig_planner.py / goldenpipe-core planner.rs. Rust is the source of
 * truth; this is the SP3 TS fallback proven to reproduce the golden vectors.
 * Pure: no InferMap/profiling (that is a PlannerInput the caller supplies).
 */
export interface PipeProfile {
  nRows: number;
  nCols: number;
  columnNames: string[];
  dtypes: string[];
  inferredDomain: string | null;
  domainConfidence: number;
}
export interface ComplexityProfile {
  maxNullDensity: number;
  meanNullDensity: number;
}
export interface PlannerInput {
  runtime: PipeProfile;
  complexity: ComplexityProfile;
}
export interface PlannedStage {
  name: string;
  config: Record<string, unknown>;
}
export interface PipePlan {
  stages: PlannedStage[];
  ruleName: string;
  confidence: number;
  evidence: Record<string, unknown>;
}

const RED_NULL_DENSITY = 0.6;
const CONFIDENT_DOMAIN_THRESHOLD = 0.5;
export const SCALE_ROUTE_MIN_ROWS = 1_000_000;
const THROUGHPUT_RECALL_TARGET = 0.95;
const GREEN_THRESHOLD = 0.7;
const AMBER_THRESHOLD = 0.4;

export function bandOf(confidence: number): "green" | "amber" | "red" {
  if (confidence >= GREEN_THRESHOLD) return "green";
  if (confidence >= AMBER_THRESHOLD) return "amber";
  return "red";
}

function stage(name: string, config: Record<string, unknown> = {}): PlannedStage {
  return { name, config };
}

function defaultDedupeStages(): PlannedStage[] {
  return [stage("goldencheck.scan"), stage("goldenflow.transform"), stage("goldenmatch.dedupe")];
}

// Evidence keys are snake_case, in the exact Python/Rust order.
function defaultEvidence(inp: PlannerInput): Record<string, unknown> {
  return {
    n_rows: inp.runtime.nRows,
    n_cols: inp.runtime.nCols,
    inferred_domain: inp.runtime.inferredDomain,
    domain_confidence: inp.runtime.domainConfidence,
    max_null_density: inp.complexity.maxNullDensity,
    mean_null_density: inp.complexity.meanNullDensity,
  };
}

export function planPipeline(inp: PlannerInput): PipePlan {
  const r = inp.runtime;
  if (r.nRows <= 1) {
    return {
      stages: [stage("goldencheck.scan"), stage("goldenflow.transform")],
      ruleName: "pathological",
      confidence: 1.0,
      evidence: defaultEvidence(inp),
    };
  }
  if (r.inferredDomain !== null && r.domainConfidence >= CONFIDENT_DOMAIN_THRESHOLD) {
    return {
      stages: [
        stage("infer_schema", { domain: r.inferredDomain }),
        stage("goldencheck.scan"),
        stage("goldenflow.transform"),
        stage("goldenmatch.dedupe"),
      ],
      ruleName: "confident_schema",
      confidence: r.domainConfidence,
      evidence: defaultEvidence(inp),
    };
  }
  if (r.inferredDomain === null && inp.complexity.maxNullDensity > RED_NULL_DENSITY) {
    return {
      stages: defaultDedupeStages(),
      ruleName: "low_confidence",
      confidence: 0.3,
      evidence: defaultEvidence(inp),
    };
  }
  return {
    stages: defaultDedupeStages(),
    ruleName: "default",
    confidence: 0.7,
    evidence: defaultEvidence(inp),
  };
}

export function applyScaleHints(plan: PipePlan, runtime: PipeProfile): PipePlan {
  const hasDedupe = plan.stages.some((s) => s.name === "goldenmatch.dedupe");
  if (runtime.nRows < SCALE_ROUTE_MIN_ROWS || !hasDedupe) {
    return {
      stages: plan.stages.map((s) => ({ name: s.name, config: { ...s.config } })),
      ruleName: plan.ruleName,
      confidence: plan.confidence,
      evidence: { ...plan.evidence },
    };
  }
  const stages = plan.stages.map((s) =>
    s.name === "goldenmatch.dedupe"
      ? {
          name: s.name,
          config: {
            ...s.config,
            _dedupe_hints: { throughput: { recall_target: THROUGHPUT_RECALL_TARGET } },
          },
        }
      : { name: s.name, config: { ...s.config } },
  );
  return {
    stages,
    ruleName: plan.ruleName,
    confidence: plan.confidence,
    evidence: { ...plan.evidence, scale_hinted: true },
  };
}
