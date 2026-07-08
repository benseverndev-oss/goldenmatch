/**
 * GoldenFlow adapter — wraps GoldenFlow-JS `TransformEngine.transformDf`.
 * Port of goldenpipe/adapters/flow.py.
 *
 * Shape note: GoldenFlow-JS exposes `new TransformEngine(config).transformDf(rows)`
 * which returns `{ rows, columns, manifest }` (the Python sibling's
 * `transform_df(df)` returns an object with `.df` + `.manifest`). We read
 * `.rows` back into `ctx.df` and surface `.manifest` as an artifact.
 *
 * Edge-safe: no `node:` imports (GoldenFlow-JS core is edge-safe).
 */

import { TransformEngine } from "goldenflow/core";
import type { GoldenFlowConfig, TransformSpec } from "goldenflow/core";
import type { PipeContext, Stage, StageResult, Row } from "../models.js";
import { StageStatus } from "../models.js";
import { enrichContextsFromFlow, type ColumnContext, type ManifestRecordLike } from "../columnContext.js";
import { repairTransformSpecs, mergeTransforms } from "../repairHost.js";

export const TransformStage: Stage = {
  info: { name: "goldenflow.transform", produces: ["df", "manifest"], consumes: ["df"] },

  validate(ctx: PipeContext): void {
    if (ctx.df === null) {
      throw new Error("TransformStage: no df in context");
    }
  },

  async run(ctx: PipeContext): Promise<StageResult> {
    const rows = (ctx.df ?? []) as Row[];
    const rawCfg: Record<string, unknown> = { ...(ctx.stageConfig ?? {}) };
    const apply = rawCfg["apply_repairs"] === true;
    delete rawCfg["apply_repairs"];

    let config: Partial<GoldenFlowConfig> | undefined;
    if (apply) {
      const plan = ctx.artifacts["repair_plan"] as Parameters<typeof repairTransformSpecs>[0];
      const { specs, skipped } = repairTransformSpecs(plan);
      const userTransforms = (rawCfg["transforms"] as TransformSpec[] | undefined) ?? [];
      if (skipped.length > 0) {
        ctx.reasoning["repair_skipped"] = skipped.map((s) => `${s.column}:${s.op}`).join("; ");
      }
      if (specs.length > 0 || userTransforms.length > 0) {
        config = { ...rawCfg, transforms: mergeTransforms(userTransforms, specs) } as Partial<GoldenFlowConfig>;
      } else {
        config = Object.keys(rawCfg).length > 0 ? (rawCfg as Partial<GoldenFlowConfig>) : undefined;
      }
    } else {
      config = Object.keys(rawCfg).length > 0 ? (rawCfg as Partial<GoldenFlowConfig>) : undefined;
    }

    const engine = new TransformEngine(config);
    const result = engine.transformDf(rows);

    ctx.df = [...result.rows] as Row[];
    ctx.artifacts["manifest"] = result.manifest;

    // Enrich column contexts with transform information (best-effort).
    const contexts = ctx.artifacts["column_contexts"];
    if (Array.isArray(contexts)) {
      try {
        const records: ManifestRecordLike[] = result.manifest.records.map((r) => ({
          column: r.column,
          transform: r.transform,
          affectedRows: r.affectedRows,
        }));
        enrichContextsFromFlow(contexts as ColumnContext[], records);
      } catch {
        /* best-effort: never break the pipeline on enrichment failure */
      }
    }

    return { status: StageStatus.SUCCESS };
  },

  rollback: null,
};
