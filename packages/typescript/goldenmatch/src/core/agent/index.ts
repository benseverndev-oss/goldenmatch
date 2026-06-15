/**
 * index.ts — Public surface for the edge-safe agent decision core.
 * Edge-safe: no `node:` imports.
 *
 * Re-exports the AgentSession orchestrator, the pure decision functions, the
 * shared skill registry + dispatcher, and all agent types. Wired into
 * `src/core/index.ts` via `export * from "./agent/index.js"`.
 */

export { AgentSession } from "./session.js";
export {
  profileForAgent,
  selectStrategy,
  buildAlternatives,
  decisionToConfig,
} from "./strategy.js";
export { AGENT_SKILLS, dispatchSkill } from "./skills.js";

export type {
  FieldProfile,
  StrategyDecision,
  Alternative,
  Telemetry,
  AnalyzeFieldProfile,
  AnalyzeResult,
  ConfidenceDistribution,
  AutoconfigResult,
  DeduplicateResult,
  MatchSourcesResult,
  StrategyMetrics,
  CompareResult,
  JSONSchema,
  SkillResult,
  SkillDef,
  SkillContext,
} from "./types.js";

// `DataProfile` is aliased to avoid colliding with complexityProfile's
// `DataProfile`, which `src/core/index.ts` already re-exports. The agent
// profile shape ({row_count, fields, has_sensitive}) is distinct.
export type { DataProfile as AgentDataProfile } from "./types.js";
