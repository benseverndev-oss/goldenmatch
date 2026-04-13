/**
 * LLM module — re-exports.
 * Port of goldencheck/llm/__init__.py.
 */

export { SYSTEM_PROMPT } from "./prompts.js";
export type {
  LLMIssue,
  LLMUpgrade,
  LLMDowngrade,
  LLMColumnAssessment,
  LLMRelation,
  LLMResponse,
} from "./prompts.js";

export { parseLlmResponse } from "./parser.js";

export { estimateCost, getBudgetLimit, checkBudget, CostReport } from "./budget.js";

export { checkLlmAvailable, callLlm } from "./providers.js";
export type { LlmCallResult } from "./providers.js";

export { buildSampleBlocks } from "./sample-block.js";
export type { SampleBlock, ValueCount, ExistingFinding } from "./sample-block.js";

export { mergeLlmFindings } from "./merger.js";
