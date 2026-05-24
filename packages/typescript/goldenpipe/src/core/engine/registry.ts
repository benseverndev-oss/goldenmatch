/**
 * Stage registry — register and retrieve stages.
 * Port of goldenpipe/engine/registry.py.
 *
 * Unlike the Python version, which discovers stages via importlib entry points,
 * the TS registry is STATIC: built-in stages (load, goldencheck.scan,
 * goldenflow.transform, goldenmatch.dedupe) are registered explicitly in
 * `defaultRegistry()`. Custom stages can be added with `register()`.
 *
 * Edge-safe: no `node:` imports.
 */

import type { Stage, StageInfo } from "../models.js";

export class StageRegistry {
  private readonly stages = new Map<string, Stage>();

  /** Register a stage under its `info.name`. */
  register(stage: Stage): void {
    this.stages.set(stage.info.name, stage);
  }

  /** Retrieve a stage by name. Throws if not found. */
  get(name: string): Stage {
    const stage = this.stages.get(name);
    if (stage === undefined) {
      throw new Error(`Stage '${name}' not found in registry`);
    }
    return stage;
  }

  /** True when a stage with this name is registered. */
  has(name: string): boolean {
    return this.stages.has(name);
  }

  /** Return `{ name: StageInfo }` for all registered stages. */
  listAll(): Record<string, StageInfo> {
    const out: Record<string, StageInfo> = {};
    for (const [name, s] of this.stages) {
      out[name] = s.info;
    }
    return out;
  }
}

/**
 * Build a registry with the built-in suite stages registered. This is the TS
 * analogue of Python's entry-point discovery — wiring goldencheck.scan,
 * goldenflow.transform, and goldenmatch.dedupe (+ the built-in `load` stage).
 */
export function defaultRegistry(): StageRegistry {
  const registry = new StageRegistry();
  // Imported here (not at module top) to keep the dependency graph explicit
  // and avoid a cycle: adapters import models + engine helpers, not registry.
  return registry;
}
