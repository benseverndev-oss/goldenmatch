/**
 * Pipeline resolver — build an ExecutionPlan and validate wiring.
 * Port of goldenpipe/engine/resolver.py.
 *
 * Edge-safe: no `node:` imports.
 */

import type { PipelineConfig, Stage, StageSpec } from "../models.js";
import { makeStageSpec } from "../models.js";
import type { StageRegistry } from "./registry.js";

/** Raised when a stage's `consumes` can't be satisfied by prior `produces`. */
export class WiringError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "WiringError";
  }
}

export interface PlannedStage {
  name: string;
  stage: Stage;
  spec: StageSpec;
  config: Record<string, unknown>;
}

export interface ExecutionPlan {
  stages: PlannedStage[];
}

export const Resolver = {
  /**
   * Resolve a config + registry into an ordered ExecutionPlan. Auto-prepends
   * the built-in `load` stage when available and validates that every stage's
   * `consumes` is produced by an earlier stage.
   */
  resolve(config: PipelineConfig, registry: StageRegistry): ExecutionPlan {
    const plan: ExecutionPlan = { stages: [] };
    const availableArtifacts = new Set<string>();

    // Auto-prepend load stage if available.
    if (registry.has("load")) {
      const load = registry.get("load");
      plan.stages.push({
        name: "load",
        stage: load,
        spec: makeStageSpec("load"),
        config: {},
      });
      for (const p of load.info.produces) availableArtifacts.add(p);
    } else {
      availableArtifacts.add("df");
    }

    for (const rawSpec of config.stages) {
      const spec = makeStageSpec(rawSpec);
      const stageObj = registry.get(spec.use);
      const name = spec.name ?? stageObj.info.name;

      for (const dep of stageObj.info.consumes) {
        if (!availableArtifacts.has(dep)) {
          throw new WiringError(
            `Stage '${name}' consumes '${dep}' but no prior stage produces it. ` +
              `Available: ${[...availableArtifacts].sort().join(", ")}`,
          );
        }
      }

      plan.stages.push({ name, stage: stageObj, spec, config: spec.config });
      for (const p of stageObj.info.produces) availableArtifacts.add(p);
    }

    return plan;
  },
};
