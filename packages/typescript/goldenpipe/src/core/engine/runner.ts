/**
 * Pipeline runner — execute stages with error handling and routing.
 * Port of goldenpipe/engine/runner.py.
 *
 * ASYNC: stage execution awaits each stage's `run`, because the GoldenMatch
 * `dedupe` adapter is async.
 *
 * Edge-safe: no `node:` imports.
 */

import type { PipeContext, StageResult } from "../models.js";
import { StageStatus } from "../models.js";
import type { StageRegistry } from "./registry.js";
import type { ExecutionPlan } from "./resolver.js";
import { Router } from "./router.js";

function isFalsy(value: unknown): boolean {
  if (value === null || value === undefined) return true;
  if (value === false || value === 0 || value === "") return true;
  if (Array.isArray(value)) return value.length === 0;
  if (value instanceof Map || value instanceof Set) return value.size === 0;
  if (typeof value === "object") return Object.keys(value).length === 0;
  return false;
}

export class Runner {
  constructor(private readonly registry: StageRegistry) {}

  /** Execute an ExecutionPlan against a PipeContext, returning per-stage results. */
  async run(plan: ExecutionPlan, ctx: PipeContext): Promise<Record<string, StageResult>> {
    const results: Record<string, StageResult> = {};
    let remaining = [...plan.stages];

    while (remaining.length > 0) {
      const planned = remaining.shift()!;

      if (planned.spec.skipIf) {
        const artifact = ctx.artifacts[planned.spec.skipIf];
        if (isFalsy(artifact)) {
          results[planned.name] = { status: StageStatus.SKIPPED };
          ctx.reasoning[planned.name] =
            `Skipped: artifact '${planned.spec.skipIf}' is missing/falsy`;
          continue;
        }
      }

      const start = performance.now();
      try {
        // Make stage-level config available to the adapter via context.
        ctx.stageConfig = planned.config;

        await planned.stage.validate(ctx);
        const result = await planned.stage.run(ctx);
        ctx.timing[planned.name] = (performance.now() - start) / 1000;
        results[planned.name] = result;

        if (result.decision != null) {
          remaining = Router.apply(result.decision, remaining, ctx, this.registry);
        }
      } catch (e) {
        ctx.timing[planned.name] = (performance.now() - start) / 1000;
        const message = e instanceof Error ? e.message : String(e);
        results[planned.name] = { status: StageStatus.FAILED, error: message };
        ctx.reasoning[planned.name] = `Failed: ${message}`;

        if (planned.spec.onError === "abort") {
          break;
        }
      }
    }

    return results;
  }
}
