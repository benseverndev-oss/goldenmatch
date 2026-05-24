/**
 * Decision router — apply routing decisions to the remaining execution plan.
 * Port of goldenpipe/engine/router.py.
 *
 * Edge-safe: no `node:` imports.
 */

import type { Decision, PipeContext } from "../models.js";
import { makeStageSpec } from "../models.js";
import type { StageRegistry } from "./registry.js";
import type { PlannedStage } from "./resolver.js";

export const Router = {
  /**
   * Apply a Decision (skip / abort / insert) to the remaining stages and
   * return the new remaining list. Records `decision.reason` in
   * `ctx.reasoning._router`.
   */
  apply(
    decision: Decision,
    remaining: PlannedStage[],
    ctx: PipeContext,
    registry: StageRegistry,
  ): PlannedStage[] {
    if (decision.reason) {
      ctx.reasoning["_router"] = decision.reason;
    }

    if (decision.abort) {
      ctx.reasoning["_router"] = `ABORT: ${decision.reason}`;
      return [];
    }

    let next = remaining;
    if (decision.skip.length > 0) {
      const skipSet = new Set(decision.skip);
      next = next.filter((s) => !skipSet.has(s.name));
    }

    if (decision.insert.length > 0) {
      const inserted: PlannedStage[] = [];
      for (const name of decision.insert) {
        const stageObj = registry.get(name);
        inserted.push({
          name,
          stage: stageObj,
          spec: makeStageSpec(name),
          config: {},
        });
      }
      next = [...inserted, ...next];
    }

    return next;
  },
};
