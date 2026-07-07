/**
 * infer_schema — GoldenPipe stage that runs InferMap to label columns.
 * TS port of goldenpipe/stages/infer_schema.py. Produces
 * `inferred_schema` (goldencheck-types.InferredSchema | null) + (on the
 * auto-detect path) `infer_schema_evidence`. Consumes nothing — meant to run
 * before stages that want typed columns. Opt-in (not in DEFAULT_STAGE_ORDER),
 * matching Python.
 *
 * Config (ctx.stageConfig): schema?: InferredSchema | no_infer?: boolean |
 * domain?: string. Precedence: schema > no_infer > domain > auto-detect;
 * the three are mutually exclusive (conflict throws in run()).
 */
import {
  SCHEMA_VERSION,
  UNMAPPED_TYPE,
  loadDomain,
  type FieldMapping,
  type InferredSchema,
} from "goldencheck-types";
import {
  detectDomainDetailed,
  map,
  DomainPackTarget,
  type MapResult,
} from "infermap";

import type { PipeContext, Stage, StageResult } from "../models.js";
import { StageStatus } from "../models.js";

/** Mirror of Python `_result_to_inferred_schema`. `confidence` is overwritten
 *  by the caller with the detection score. */
function resultToInferredSchema(result: MapResult, domain: string): InferredSchema {
  const fields: Record<string, FieldMapping> = {};
  for (const fm of result.mappings) {
    fields[fm.source] = {
      source_col: fm.source,
      canonical: fm.target, // soft mode sets target = null -> canonical null
      type: fm.target ? fm.target : UNMAPPED_TYPE,
      confidence: fm.confidence,
      evidence: { reasoning: fm.reasoning },
    };
  }
  for (const col of result.unmappedSource) {
    if (!(col in fields)) {
      fields[col] = {
        source_col: col,
        canonical: null,
        type: UNMAPPED_TYPE,
        confidence: 0.0,
        evidence: {},
      };
    }
  }
  // Loop-based min — never spread an array into Math.min/max (it throws
  // RangeError past ~65K elements; banned by ast-grep ts-no-spread-math-min-max).
  const confidence = result.mappings.length
    ? result.mappings.reduce((m, fm) => (fm.confidence < m ? fm.confidence : m), Infinity)
    : 0.0;
  // Stamp schema_version like the Python InferredSchema dataclass (default
  // SCHEMA_VERSION) so the artifact is field-identical cross-surface.
  return { domain, fields, confidence, schema_version: SCHEMA_VERSION };
}

/** Mirror of Python `_validate_flags`: at most one of schema/no_infer/domain. */
function validateFlags(cfg: Record<string, unknown>): void {
  const set =
    (cfg.schema != null ? 1 : 0) +
    (cfg.no_infer ? 1 : 0) +
    (cfg.domain != null ? 1 : 0);
  if (set > 1) {
    throw new Error(
      "conflict: 'schema', 'no_infer', and 'domain' are mutually exclusive. " +
        "Precedence: schema > no_infer > domain > auto-detect.",
    );
  }
}

export const InferSchemaStage: Stage = {
  info: {
    // Matches Python's declared produces=["inferred_schema"]. The stage ALSO sets
    // `infer_schema_evidence` at runtime (via setdefault) but, like Python, does
    // not declare it — replicated faithfully so the stage contract is identical.
    name: "infer_schema",
    produces: ["inferred_schema"],
    consumes: [],
  },

  // No precondition: consumes [] and a null df is a valid SUCCESS branch, so
  // validate() must NOT throw. The flag-conflict check lives in run() (mirrors
  // Python's _validate_flags at the top of run()).
  validate(_ctx: PipeContext): void {},

  async run(ctx: PipeContext): Promise<StageResult> {
    const cfg = ctx.stageConfig ?? {};
    validateFlags(cfg);

    if (cfg.schema != null) {
      ctx.artifacts["inferred_schema"] = cfg.schema;
      return { status: StageStatus.SUCCESS };
    }
    if (cfg.no_infer) {
      ctx.artifacts["inferred_schema"] = null;
      return { status: StageStatus.SUCCESS };
    }
    if (ctx.df === null) {
      ctx.artifacts["inferred_schema"] = null;
      return { status: StageStatus.SUCCESS };
    }

    const explicit = cfg.domain as string | undefined;
    let domain: string;
    let detectScore: number;
    let detectEvidence: Record<string, unknown>;

    // Truthy check mirrors Python `if explicit_domain:` — an empty-string domain
    // falls through to auto-detect (validateFlags counted it via `!= null`).
    if (explicit) {
      domain = explicit;
      detectScore = 1.0;
      detectEvidence = { detect_reason: "explicit" };
    } else {
      const detection = detectDomainDetailed({ records: ctx.df });
      if (detection.domain !== null) {
        domain = detection.domain;
        detectScore = detection.score;
        detectEvidence = {
          detect_reason: detection.reason,
          detect_score: detection.score,
          runner_up: detection.runner_up,
          runner_up_score: detection.runner_up_score,
        };
      } else {
        domain = "generic";
        detectScore = 0.0;
        detectEvidence = {
          detect_reason: detection.reason,
          detect_score: detection.score,
          runner_up: detection.runner_up,
          runner_up_score: detection.runner_up_score,
          fallback: true,
        };
      }
    }

    const result = map(
      { records: ctx.df },
      new DomainPackTarget(loadDomain(domain)),
      { soft: true },
    );
    const inferred: InferredSchema = {
      ...resultToInferredSchema(result, domain),
      confidence: detectScore,
    };

    ctx.artifacts["inferred_schema"] = inferred;
    // setdefault: only set when a prior stage hasn't already.
    if (!("infer_schema_evidence" in ctx.artifacts)) {
      ctx.artifacts["infer_schema_evidence"] = detectEvidence;
    }
    return { status: StageStatus.SUCCESS };
  },
};
