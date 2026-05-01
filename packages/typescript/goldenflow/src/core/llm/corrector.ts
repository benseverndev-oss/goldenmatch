/**
 * LLM-assisted categorical correction -- edge-safe (raw fetch, no SDK imports).
 *
 * Registers `category_llm_correct` as a passthrough transform. The actual LLM
 * interaction happens via the async helper `applyLlmCorrections()`, which the
 * engine or CLI should call explicitly before/after the sync transform pipeline.
 */

import type { ColumnValue } from "../types.js";
import { registerTransform } from "../transforms/registry.js";

// ---------------------------------------------------------------------------
// Corrections cache -- keyed by column name
// ---------------------------------------------------------------------------

const _correctionsCache = new Map<string, ReadonlyMap<string, string>>();

/** Clear all cached corrections. */
export function clearLlmCache(): void {
  _correctionsCache.clear();
}

/** Inject pre-computed corrections (useful for testing or offline mode). */
export function setLlmCorrections(
  columnName: string,
  corrections: Record<string, string>,
): void {
  _correctionsCache.set(columnName, new Map(Object.entries(corrections)));
}

// ---------------------------------------------------------------------------
// Value frequency summary
// ---------------------------------------------------------------------------

/**
 * Build a frequency map of the top `max` non-null string values in `values`.
 * Counts are descending so the prompt focuses on the most common entries.
 */
export function getValueSummary(
  values: readonly ColumnValue[],
  max = 30,
): Record<string, number> {
  const counts = new Map<string, number>();
  for (const v of values) {
    if (v === null || typeof v !== "string") continue;
    const trimmed = v.trim();
    if (!trimmed) continue;
    counts.set(trimmed, (counts.get(trimmed) ?? 0) + 1);
  }
  const sorted = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, max);
  return Object.fromEntries(sorted);
}

// ---------------------------------------------------------------------------
// LLM interaction (raw fetch -- edge-safe)
// ---------------------------------------------------------------------------

interface AnthropicResponse {
  content: Array<{ text: string }>;
}

interface OpenAIResponse {
  choices: Array<{ message: { content: string } }>;
}

function buildPrompt(
  columnName: string,
  valueSummary: Record<string, number>,
): string {
  return `You are a data quality expert. Analyze this column and identify values that appear to be misspellings, abbreviations, or variants of other values in the same column.

Column name: ${columnName}
Value frequencies (value: count):
${JSON.stringify(valueSummary, null, 2)}

For each incorrect value, provide the corrected canonical form. Only include values that need correction. Return JSON object mapping incorrect values to their corrections.

Example response:
{"actve": "active", "ACTIVE": "active", "pendng": "pending"}

Return ONLY the JSON object, no other text.`;
}

/** Read an env var if `process` exists (edge runtimes may not have it). */
function envVar(key: string): string | undefined {
  if (typeof process !== "undefined" && process.env) {
    return process.env[key];
  }
  return undefined;
}

/** Validate that parsed JSON is a flat string→string map. Drops non-string entries. */
function validateCorrections(parsed: unknown): Record<string, string> {
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
  const result: Record<string, string> = {};
  for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
    if (typeof k === "string" && typeof v === "string") result[k] = v;
  }
  return result;
}

/**
 * Ask an LLM to identify misspellings/variants and return a corrections map.
 * Tries Anthropic first (if `ANTHROPIC_API_KEY` is set), then OpenAI.
 * Returns an empty object if no key is available or if the call fails.
 */
async function askLlmForCorrections(
  columnName: string,
  valueSummary: Record<string, number>,
): Promise<Record<string, string>> {
  const prompt = buildPrompt(columnName, valueSummary);
  const anthropicKey = envVar("ANTHROPIC_API_KEY");
  const openaiKey = envVar("OPENAI_API_KEY");

  try {
    if (anthropicKey) {
      const resp = await fetch("https://api.anthropic.com/v1/messages", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-api-key": anthropicKey,
          "anthropic-version": "2023-06-01",
        },
        body: JSON.stringify({
          model: "claude-sonnet-4-5-20250514",
          max_tokens: 1024,
          messages: [{ role: "user", content: prompt }],
        }),
      });
      if (!resp.ok) {
        console.warn(`[goldenflow:llm] Anthropic API error: ${resp.status} ${resp.statusText}`);
        return {};
      }
      const data = (await resp.json()) as AnthropicResponse;
      const text = data.content?.[0]?.text ?? "";
      if (!text) {
        console.warn("[goldenflow:llm] Anthropic returned empty response");
        return {};
      }
      return validateCorrections(JSON.parse(text));
    }

    if (openaiKey) {
      const resp = await fetch("https://api.openai.com/v1/chat/completions", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${openaiKey}`,
        },
        body: JSON.stringify({
          model: "gpt-4o-mini",
          messages: [{ role: "user", content: prompt }],
          response_format: { type: "json_object" },
        }),
      });
      if (!resp.ok) {
        console.warn(`[goldenflow:llm] OpenAI API error: ${resp.status} ${resp.statusText}`);
        return {};
      }
      const data = (await resp.json()) as OpenAIResponse;
      const text = data.choices?.[0]?.message?.content ?? "";
      if (!text) {
        console.warn("[goldenflow:llm] OpenAI returned empty response");
        return {};
      }
      return validateCorrections(JSON.parse(text));
    }
  } catch (e) {
    console.warn(
      `[goldenflow:llm] LLM correction failed: ${e instanceof Error ? e.message : String(e)}`,
    );
  }

  return {};
}

// ---------------------------------------------------------------------------
// Async helper -- call before the sync transform pipeline
// ---------------------------------------------------------------------------

/**
 * Fetch LLM-based corrections for a column's values and cache them.
 *
 * Usage:
 * ```ts
 * await prepareLlmCorrections("status", rows.map(r => r.status));
 * // ... then run the sync transform pipeline which includes category_llm_correct
 * ```
 *
 * Returns the corrections map (also cached internally).
 */
export async function prepareLlmCorrections(
  columnName: string,
  values: readonly ColumnValue[],
): Promise<Record<string, string>> {
  const summary = getValueSummary(values);
  if (Object.keys(summary).length === 0) return {};
  const corrections = await askLlmForCorrections(columnName, summary);
  if (Object.keys(corrections).length > 0) {
    _correctionsCache.set(columnName, new Map(Object.entries(corrections)));
  }
  return corrections;
}

/**
 * High-level async helper: fetch corrections then apply them to `values`.
 *
 * Returns corrected values array (same length as input). Skips the LLM call
 * if corrections are already cached for `columnName`.
 */
export async function applyLlmCorrections(
  columnName: string,
  values: readonly ColumnValue[],
): Promise<ColumnValue[]> {
  if (!_correctionsCache.has(columnName)) {
    await prepareLlmCorrections(columnName, values);
  }
  const map = _correctionsCache.get(columnName);
  if (!map || map.size === 0) return [...values];

  return values.map((v) => {
    if (v === null || typeof v !== "string") return v;
    const trimmed = v.trim();
    return map.get(trimmed) ?? v;
  });
}

// ---------------------------------------------------------------------------
// Sync transform (reads from cache -- passthrough if cache empty)
// ---------------------------------------------------------------------------

/**
 * Synchronous transform function. Applies cached LLM corrections if available,
 * otherwise passes values through unchanged.
 *
 * The cache is populated by calling `prepareLlmCorrections()` or
 * `applyLlmCorrections()` before running the transform pipeline.
 */
function categoryLlmCorrect(
  values: readonly ColumnValue[],
  ...params: unknown[]
): ColumnValue[] {
  // The first param (if provided) is the column name, injected by the engine.
  const columnName =
    typeof params[0] === "string" ? params[0] : "__default__";
  const map = _correctionsCache.get(columnName);
  if (!map || map.size === 0) return [...values];

  return values.map((v) => {
    if (v === null || typeof v !== "string") return v;
    const trimmed = v.trim();
    return map.get(trimmed) ?? v;
  });
}

// ---------------------------------------------------------------------------
// Registration
// ---------------------------------------------------------------------------

registerTransform(
  {
    name: "category_llm_correct",
    inputTypes: ["string"],
    autoApply: false,
    priority: 34,
    mode: "series",
  },
  categoryLlmCorrect,
);
