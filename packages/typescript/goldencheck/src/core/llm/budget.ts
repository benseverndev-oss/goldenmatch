/**
 * LLM cost tracking and budget enforcement.
 * Port of goldencheck/llm/budget.py.
 * Edge-safe: reads env via globalThis.process when available.
 */

/** (input_per_1k_tokens, output_per_1k_tokens) */
const MODEL_COSTS: Record<string, [number, number]> = {
  "claude-haiku-4-5-20251001": [0.0008, 0.004],
  "claude-sonnet-4-20250514": [0.003, 0.015],
  "gpt-4o-mini": [0.00015, 0.0006],
  "gpt-4o": [0.0025, 0.01],
};
const DEFAULT_COST: [number, number] = [0.001, 0.004];

/** Estimate cost in USD for a given token count and model. */
export function estimateCost(inputTokens: number, outputTokens: number, model: string): number {
  const [inputRate, outputRate] = MODEL_COSTS[model] ?? DEFAULT_COST;
  return (inputTokens / 1000) * inputRate + (outputTokens / 1000) * outputRate;
}

/** Get budget limit from GOLDENCHECK_LLM_BUDGET env var. Returns null if uncapped. */
export function getBudgetLimit(): number | null {
  const val = getEnv("GOLDENCHECK_LLM_BUDGET");
  if (val === undefined || val === null || val === "") return null;
  const parsed = parseFloat(val);
  if (isNaN(parsed)) return null;
  return parsed;
}

/**
 * Check if estimated cost is within budget.
 * Returns true if OK to proceed (budget not exceeded or no budget set).
 */
export function checkBudget(estimatedCost: number): boolean {
  const limit = getBudgetLimit();
  if (limit === null) return true;
  return estimatedCost <= limit;
}

/** Tracks actual cost from a single LLM call. */
export class CostReport {
  inputTokens: number = 0;
  outputTokens: number = 0;
  model: string = "";
  costUsd: number = 0.0;

  record(inputTokens: number, outputTokens: number, model: string): void {
    this.inputTokens = inputTokens;
    this.outputTokens = outputTokens;
    this.model = model;
    this.costUsd = estimateCost(inputTokens, outputTokens, model);
  }

  summary(): { model: string; inputTokens: number; outputTokens: number; costUsd: number } {
    return {
      model: this.model,
      inputTokens: this.inputTokens,
      outputTokens: this.outputTokens,
      costUsd: Math.round(this.costUsd * 1_000_000) / 1_000_000,
    };
  }
}

// --- Env helper (edge-safe) ---

function getEnv(key: string): string | undefined {
  if (typeof globalThis !== "undefined" && (globalThis as any).process?.env) {
    return (globalThis as any).process.env[key];
  }
  return undefined;
}
