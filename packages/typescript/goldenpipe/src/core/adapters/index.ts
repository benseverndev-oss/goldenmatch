/**
 * Adapters index — built-in suite stages + registry wiring.
 * Replaces Python's importlib entry-point discovery with a STATIC registry.
 *
 * Edge-safe: no `node:` imports.
 */

import { StageRegistry } from "../engine/registry.js";
import { LoadStage } from "./load.js";
import { ScanStage } from "./check.js";
import { TransformStage } from "./flow.js";
import { DedupeStage } from "./match.js";

export { LoadStage } from "./load.js";
export { ScanStage } from "./check.js";
export { TransformStage } from "./flow.js";
export { DedupeStage, buildConfigFromContexts } from "./match.js";

/**
 * Build a registry with all built-in suite stages registered:
 *   - `load` (built-in)
 *   - `goldencheck.scan`
 *   - `goldenflow.transform`
 *   - `goldenmatch.dedupe`
 *
 * This is the TS analogue of Python's `StageRegistry.discover()`.
 */
export function buildDefaultRegistry(): StageRegistry {
  const registry = new StageRegistry();
  registry.register(LoadStage);
  registry.register(ScanStage);
  registry.register(TransformStage);
  registry.register(DedupeStage);
  return registry;
}
