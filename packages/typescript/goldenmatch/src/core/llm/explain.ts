/**
 * llm/explain.ts -- one-line "why" explanations for review items.
 * Edge-safe: no `node:` imports.
 *
 * `whyForCorrection` produces a deterministic, template-based summary by
 * default ("matched on name / zip with score 0.92"). When `useLlm` is true
 * AND an OPENAI_API_KEY / ANTHROPIC_API_KEY is set in the environment,
 * `llmExplainPair` upgrades the explanation by sending the pair to a small
 * model. Falls back to the deterministic phrase silently on any LLM error.
 *
 * The LLM client itself is loaded via dynamic `import("...")` so this module
 * stays edge-safe and doesn't pin a concrete SDK at build time. Tests mock
 * the dynamic import.
 */

import type { Row, MatchkeyField } from "../types.js";
import { explainPair } from "../explain.js";
import { makeMatchkeyConfig } from "../types.js";

export interface WhyOptions {
  readonly useLlm?: boolean;
}

/**
 * Minimal correction-shaped input. Accepts either a stored Correction or a
 * pair-of-ids + score from a fresh review item.
 */
export interface WhyInput {
  readonly idA: number;
  readonly idB: number;
  readonly originalScore?: number;
  readonly score?: number;
}

function getEnv(name: string): string | undefined {
  if (typeof process !== "undefined" && process.env) {
    return process.env[name];
  }
  return undefined;
}

/**
 * Produce a one-sentence explanation of a pair. Default deterministic;
 * upgrades to LLM output when `opts.useLlm` is true and an API key is set.
 *
 * Always returns a non-empty string (falls back to a basic template on any
 * error path).
 */
export async function whyForCorrection(
  pair: WhyInput,
  df: ReadonlyArray<Row>,
  matchkeyFields: ReadonlyArray<MatchkeyField>,
  opts?: WhyOptions,
): Promise<string> {
  const deterministic = deterministicWhy(pair, df, matchkeyFields);
  const wantLlm = opts?.useLlm === true;
  if (!wantLlm) return deterministic;
  const hasKey =
    Boolean(getEnv("OPENAI_API_KEY")) || Boolean(getEnv("ANTHROPIC_API_KEY"));
  if (!hasKey) return deterministic;
  try {
    const llm = await llmExplainPair(pair, df, matchkeyFields);
    return llm || deterministic;
  } catch {
    return deterministic;
  }
}

function deterministicWhy(
  pair: WhyInput,
  df: ReadonlyArray<Row>,
  matchkeyFields: ReadonlyArray<MatchkeyField>,
): string {
  const score = pair.originalScore ?? pair.score ?? 0;
  const fieldNames = matchkeyFields.map((f) => f.field);
  if (fieldNames.length === 0 || df.length === 0) {
    return `pair (${pair.idA}, ${pair.idB}) scored ${score.toFixed(2)}`;
  }
  // Try to compute a richer explanation using the existing explainPair when
  // we can locate both rows by `__row_id__`.
  const rowById = new Map<number, Row>();
  for (const r of df) {
    const rid = r["__row_id__"];
    if (typeof rid === "number") rowById.set(rid, r);
  }
  const rowA = rowById.get(pair.idA);
  const rowB = rowById.get(pair.idB);
  if (rowA && rowB) {
    try {
      const mk = makeMatchkeyConfig({
        name: "review",
        type: "weighted",
        fields: matchkeyFields as MatchkeyField[],
      });
      const exp = explainPair(rowA, rowB, mk);
      // Take the first reasoning line if available; else fall back.
      if (exp.reasoning.length > 0) {
        return `matched on ${fieldNames.join(" / ")} with score ${score.toFixed(2)} (${exp.reasoning[0]})`;
      }
    } catch {
      /* fall through */
    }
  }
  return `matched on ${fieldNames.join(" / ")} with score ${score.toFixed(2)}`;
}

/**
 * LLM-upgrade path. Sends a compact prompt to OpenAI (preferred) or Anthropic.
 * Returns an empty string if the dynamic import or API call fails. Module
 * imports are dynamic and `as string`-cast so tsup doesn't resolve them.
 */
export async function llmExplainPair(
  pair: WhyInput,
  df: ReadonlyArray<Row>,
  matchkeyFields: ReadonlyArray<MatchkeyField>,
): Promise<string> {
  const rowById = new Map<number, Row>();
  for (const r of df) {
    const rid = r["__row_id__"];
    if (typeof rid === "number") rowById.set(rid, r);
  }
  const rowA = rowById.get(pair.idA);
  const rowB = rowById.get(pair.idB);
  if (!rowA || !rowB) return "";

  const fields = matchkeyFields.map((f) => f.field);
  const projA: Record<string, unknown> = {};
  const projB: Record<string, unknown> = {};
  for (const f of fields) {
    projA[f] = rowA[f];
    projB[f] = rowB[f];
  }
  const score = pair.originalScore ?? pair.score ?? 0;
  const userPrompt =
    `Two records were flagged as a possible match (score ${score.toFixed(2)}). ` +
    `Explain in ONE short sentence (max 25 words) why they likely match or don't.\n` +
    `A: ${JSON.stringify(projA)}\nB: ${JSON.stringify(projB)}`;

  const openaiKey = getEnv("OPENAI_API_KEY");
  if (openaiKey) {
    try {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const mod: any = await import("openai" as string);
      const OpenAI = mod.default ?? mod.OpenAI ?? mod;
      const client = new OpenAI({ apiKey: openaiKey });
      const resp = await client.chat.completions.create({
        model: "gpt-4o-mini",
        messages: [{ role: "user", content: userPrompt }],
        max_tokens: 80,
      });
      const text =
        resp?.choices?.[0]?.message?.content ?? resp?.choices?.[0]?.text ?? "";
      return String(text).trim();
    } catch {
      return "";
    }
  }
  const anthropicKey = getEnv("ANTHROPIC_API_KEY");
  if (anthropicKey) {
    try {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const mod: any = await import("@anthropic-ai/sdk" as string);
      const Anthropic = mod.default ?? mod.Anthropic ?? mod;
      const client = new Anthropic({ apiKey: anthropicKey });
      const resp = await client.messages.create({
        model: "claude-haiku-4-5",
        max_tokens: 80,
        messages: [{ role: "user", content: userPrompt }],
      });
      const block = resp?.content?.[0];
      const text =
        block && typeof block === "object" && "text" in block
          ? String((block as { text: string }).text)
          : "";
      return text.trim();
    } catch {
      return "";
    }
  }
  return "";
}
