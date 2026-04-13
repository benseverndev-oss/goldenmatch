/**
 * LLM provider wrappers for Anthropic and OpenAI.
 * Port of goldencheck/llm/providers.py.
 * Edge-safe: uses fetch() directly, no SDK dependencies.
 */

import { SYSTEM_PROMPT } from "./prompts.js";

export interface LlmCallResult {
  text: string;
  inputTokens: number;
  outputTokens: number;
}

const DEFAULT_MODELS: Record<string, string> = {
  anthropic: "claude-haiku-4-5-20251001",
  openai: "gpt-4o-mini",
};

/**
 * Check that the API key env var exists for the given provider.
 * Throws an Error on failure (not SystemExit like Python — use try/catch).
 */
export function checkLlmAvailable(provider: string): void {
  if (provider === "anthropic") {
    if (!getEnv("ANTHROPIC_API_KEY")) {
      throw new Error("LLM boost requires ANTHROPIC_API_KEY environment variable.");
    }
  } else if (provider === "openai") {
    if (!getEnv("OPENAI_API_KEY")) {
      throw new Error("LLM boost requires OPENAI_API_KEY environment variable.");
    }
  } else {
    throw new Error(`Unknown LLM provider: ${provider}. Use 'anthropic' or 'openai'.`);
  }
}

/**
 * Send prompt to LLM and return text + token counts.
 * Uses fetch() for edge compatibility — no SDK imports.
 */
export async function callLlm(provider: string, userPrompt: string): Promise<LlmCallResult> {
  const model = getEnv("GOLDENCHECK_LLM_MODEL") || DEFAULT_MODELS[provider] || "";

  if (provider === "anthropic") {
    return callAnthropic(model, userPrompt);
  } else if (provider === "openai") {
    return callOpenai(model, userPrompt);
  }

  throw new Error(`Unknown provider: ${provider}`);
}

// --- Anthropic (Messages API) ---

async function callAnthropic(model: string, userPrompt: string): Promise<LlmCallResult> {
  const apiKey = getEnv("ANTHROPIC_API_KEY");
  if (!apiKey) throw new Error("ANTHROPIC_API_KEY not set.");

  const response = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-api-key": apiKey,
      "anthropic-version": "2023-06-01",
    },
    body: JSON.stringify({
      model,
      max_tokens: 4096,
      system: SYSTEM_PROMPT,
      messages: [{ role: "user", content: userPrompt }],
    }),
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(`Anthropic API error (${response.status}): ${body}`);
  }

  const data = (await response.json()) as {
    content: Array<{ text: string }>;
    usage: { input_tokens: number; output_tokens: number };
  };

  const text = data.content[0]?.text;
  if (!text) {
    throw new Error("Anthropic returned empty response content — the request may have been filtered or the model produced no output");
  }

  return {
    text,
    inputTokens: data.usage.input_tokens,
    outputTokens: data.usage.output_tokens,
  };
}

// --- OpenAI (Chat Completions API) ---

async function callOpenai(model: string, userPrompt: string): Promise<LlmCallResult> {
  const apiKey = getEnv("OPENAI_API_KEY");
  if (!apiKey) throw new Error("OPENAI_API_KEY not set.");

  const response = await fetch("https://api.openai.com/v1/chat/completions", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify({
      model,
      max_tokens: 4096,
      response_format: { type: "json_object" },
      messages: [
        { role: "system", content: SYSTEM_PROMPT },
        { role: "user", content: userPrompt },
      ],
    }),
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(`OpenAI API error (${response.status}): ${body}`);
  }

  const data = (await response.json()) as {
    choices: Array<{ message: { content: string } }>;
    usage: { prompt_tokens: number; completion_tokens: number };
  };

  const text = data.choices[0]?.message?.content;
  if (!text) {
    throw new Error("OpenAI returned empty response content — the request may have been filtered or the model produced no output");
  }

  return {
    text,
    inputTokens: data.usage.prompt_tokens,
    outputTokens: data.usage.completion_tokens,
  };
}

// --- Env helper (edge-safe) ---

function getEnv(key: string): string | undefined {
  if (typeof globalThis !== "undefined" && (globalThis as any).process?.env) {
    return (globalThis as any).process.env[key];
  }
  return undefined;
}
