/**
 * Reporter — build a PipeResult from a PipeContext after execution.
 * Port of goldenpipe/engine/reporter.py.
 *
 * Edge-safe: no `node:` imports.
 */

import type { PipeContext, PipeResult, StageResult } from "../models.js";
import { PipeStatus, StageStatus } from "../models.js";

export const Reporter = {
  build(ctx: PipeContext, stages: Record<string, StageResult>): PipeResult {
    const entries = Object.entries(stages);

    const errors = entries
      .filter(([, r]) => r.status === StageStatus.FAILED && r.error)
      .map(([name, r]) => `${name}: ${r.error}`);

    const skipped = entries
      .filter(([, r]) => r.status === StageStatus.SKIPPED)
      .map(([name]) => name);

    const nonSkip = entries
      .map(([, r]) => r.status)
      .filter((s) => s !== StageStatus.SKIPPED);

    let status: PipeStatus;
    if (nonSkip.length === 0) {
      status = PipeStatus.SUCCESS;
    } else if (nonSkip.every((s) => s === StageStatus.FAILED)) {
      status = PipeStatus.FAILED;
    } else if (nonSkip.every((s) => s === StageStatus.SUCCESS)) {
      status = PipeStatus.SUCCESS;
    } else {
      status = PipeStatus.PARTIAL;
    }

    return {
      status,
      source: typeof ctx.metadata["source"] === "string" ? (ctx.metadata["source"] as string) : "",
      inputRows: typeof ctx.metadata["input_rows"] === "number" ? (ctx.metadata["input_rows"] as number) : 0,
      stages,
      artifacts: { ...ctx.artifacts },
      skipped,
      errors,
      reasoning: { ...ctx.reasoning },
      timing: { ...ctx.timing },
    };
  },
};
