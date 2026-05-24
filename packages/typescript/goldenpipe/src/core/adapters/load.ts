/**
 * Built-in LoadStage — marks `df` as available. The actual data loading is
 * handled by the Pipeline (file read) or supplied by the caller (runDf).
 * Port of goldenpipe/adapters/__init__.py LoadStage.
 *
 * Edge-safe: no `node:` imports.
 */

import type { PipeContext, Stage, StageResult } from "../models.js";
import { StageStatus } from "../models.js";

export const LoadStage: Stage = {
  info: { name: "load", produces: ["df"], consumes: [] },
  validate(_ctx: PipeContext): void {
    /* no-op */
  },
  async run(_ctx: PipeContext): Promise<StageResult> {
    return { status: StageStatus.SUCCESS };
  },
  rollback: null,
};
