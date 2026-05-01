/**
 * Agent module — strategy selection, handoff generation, and review queue.
 * Re-exports all agent sub-modules.
 */

export {
  type StrategyDecision,
  selectStrategy,
  buildAlternatives,
  explainFinding,
  explainColumn,
  compareDomains,
  findingsToFbc,
} from "./intelligence.js";

export {
  type GenerateHandoffOptions,
  generateHandoff,
} from "./handoff.js";

export {
  type ReviewItem,
  type ReviewQueueStats,
  type ClassifyResult,
  ReviewQueue,
} from "./review-queue.js";
