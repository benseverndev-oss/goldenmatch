/**
 * LLM-powered rule generation — analyses data samples and generates
 * validation rules, then applies them to produce findings.
 * Port of goldencheck/llm/rule_generator.py.
 * Edge-safe: uses fetch() via the providers helper, no SDK dependencies.
 */

import type { TabularData } from "../data.js";
import { type Finding, Severity, makeFinding } from "../types.js";
import { buildSampleBlocks } from "./sample-block.js";
import { checkBudget, estimateCost, CostReport } from "./budget.js";

// ---------------------------------------------------------------------------
// Prompt
// ---------------------------------------------------------------------------

export const RULE_GENERATION_PROMPT = `You are a data quality analyst generating specific validation rules.

You will receive a dataset summary with representative samples for each column, along with findings from automated profilers. Your job is to generate SPECIFIC, TESTABLE validation rules that the profilers missed.

Focus on:
1. **Value validity** — specific values that are invalid for this column's domain (e.g., "XX" is not a valid country code, negative quantities are impossible)
2. **Format constraints** — expected string lengths, regex patterns (e.g., auth numbers should be exactly 10 digits)
3. **Cross-column logic** — relationships between columns (e.g., age should match date_of_birth, state should be consistent with zip prefix)
4. **Domain standards** — mixed coding standards (e.g., ICD-9 vs ICD-10), inconsistent units

For each rule, specify:
- The column(s) it applies to
- The rule type: "regex", "length", "value_list", "range", "cross_column", "custom"
- A clear description of what makes a value invalid
- The check name to use (one of: invalid_values, format_detection, cross_column, type_inference, logic_violation)

Respond with valid JSON:
{
  "rules": [
    {
      "column": "<column_name>",
      "rule_type": "regex|length|value_list|range|cross_column|custom",
      "check": "<check_name>",
      "description": "<what this rule checks>",
      "params": {
        "pattern": "<regex for regex type>",
        "min_length": <int>,
        "max_length": <int>,
        "valid_values": ["<val1>", "<val2>"],
        "invalid_values": ["<val1>"],
        "min": <number>,
        "max": <number>,
        "related_column": "<col>",
        "relationship": "<description>"
      }
    }
  ]
}

Only include rules where you are confident there is a real issue. Be specific — don't generate generic rules that the profilers already cover (null checks, uniqueness, basic range, basic format). Focus on DOMAIN-SPECIFIC rules that require semantic understanding.`;

// ---------------------------------------------------------------------------
// Rule model
// ---------------------------------------------------------------------------

export interface RuleParams {
  pattern?: string | null;
  minLength?: number | null;
  maxLength?: number | null;
  validValues?: string[] | null;
  invalidValues?: string[] | null;
  min?: number | null;
  max?: number | null;
  relatedColumn?: string | null;
  relationship?: string | null;
}

export interface GeneratedRule {
  column: string;
  ruleType: string;
  check: string;
  description: string;
  params: RuleParams;
}

// ---------------------------------------------------------------------------
// JSON (de)serialisation — wire format mirrors the Python snake_case schema
// ---------------------------------------------------------------------------

function coerceStrList(v: unknown): string[] | null {
  if (!Array.isArray(v)) return null;
  // coerce_numbers_to_str=True parity — LLMs sometimes emit ints
  return v.map((x) => String(x));
}

function parseParams(raw: unknown): RuleParams {
  const p = (raw ?? {}) as Record<string, unknown>;
  return {
    pattern: p.pattern != null ? String(p.pattern) : null,
    minLength: typeof p.min_length === "number" ? p.min_length : null,
    maxLength: typeof p.max_length === "number" ? p.max_length : null,
    validValues: coerceStrList(p.valid_values),
    invalidValues: coerceStrList(p.invalid_values),
    min: typeof p.min === "number" ? p.min : null,
    max: typeof p.max === "number" ? p.max : null,
    relatedColumn: p.related_column != null ? String(p.related_column) : null,
    relationship: p.relationship != null ? String(p.relationship) : null,
  };
}

function ruleFromJson(raw: unknown): GeneratedRule {
  const r = raw as Record<string, unknown>;
  return {
    column: String(r.column ?? ""),
    ruleType: String(r.rule_type ?? ""),
    check: String(r.check ?? ""),
    description: String(r.description ?? ""),
    params: parseParams(r.params),
  };
}

function ruleToJson(rule: GeneratedRule): Record<string, unknown> {
  const p = rule.params;
  return {
    column: rule.column,
    rule_type: rule.ruleType,
    check: rule.check,
    description: rule.description,
    params: {
      pattern: p.pattern ?? null,
      min_length: p.minLength ?? null,
      max_length: p.maxLength ?? null,
      valid_values: p.validValues ?? null,
      invalid_values: p.invalidValues ?? null,
      min: p.min ?? null,
      max: p.max ?? null,
      related_column: p.relatedColumn ?? null,
      relationship: p.relationship ?? null,
    },
  };
}

/** Serialise rules to a JSON string (matches goldencheck_rules.json format). */
export function serializeRules(rules: readonly GeneratedRule[]): string {
  return JSON.stringify(rules.map(ruleToJson), null, 2);
}

/** Parse rules from a JSON string. Returns [] on empty/invalid input. */
export function deserializeRules(text: string): GeneratedRule[] {
  if (!text || text.trim() === "") return [];
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    return [];
  }
  if (!Array.isArray(data)) return [];
  return data.map(ruleFromJson);
}

// ---------------------------------------------------------------------------
// Rule generation (LLM)
// ---------------------------------------------------------------------------

function getEnv(key: string): string | undefined {
  if (typeof globalThis !== "undefined" && (globalThis as { process?: { env?: Record<string, string | undefined> } }).process?.env) {
    return (globalThis as { process: { env: Record<string, string | undefined> } }).process.env[key];
  }
  return undefined;
}

const DEFAULT_MODELS: Record<string, string> = {
  anthropic: "claude-haiku-4-5-20251001",
  openai: "gpt-4o-mini",
};

/** Call the LLM with the rule-generation system prompt. */
async function callLlmForRules(
  provider: string,
  userPrompt: string,
): Promise<{ text: string; inputTokens: number; outputTokens: number }> {
  const model = getEnv("GOLDENCHECK_LLM_MODEL") || DEFAULT_MODELS[provider] || "";

  if (provider === "anthropic") {
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
        system: RULE_GENERATION_PROMPT,
        messages: [{ role: "user", content: userPrompt }],
      }),
    });
    if (!response.ok) {
      throw new Error(`Anthropic API error (${response.status}): ${await response.text()}`);
    }
    const data = (await response.json()) as {
      content: Array<{ text: string }>;
      usage: { input_tokens: number; output_tokens: number };
    };
    return {
      text: data.content[0]?.text ?? "",
      inputTokens: data.usage.input_tokens,
      outputTokens: data.usage.output_tokens,
    };
  }

  if (provider === "openai") {
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
          { role: "system", content: RULE_GENERATION_PROMPT },
          { role: "user", content: userPrompt },
        ],
      }),
    });
    if (!response.ok) {
      throw new Error(`OpenAI API error (${response.status}): ${await response.text()}`);
    }
    const data = (await response.json()) as {
      choices: Array<{ message: { content: string } }>;
      usage: { prompt_tokens: number; completion_tokens: number };
    };
    return {
      text: data.choices[0]?.message?.content ?? "",
      inputTokens: data.usage.prompt_tokens,
      outputTokens: data.usage.completion_tokens,
    };
  }

  throw new Error(`Unknown provider: ${provider}`);
}

/** Strip markdown code fences from an LLM response. */
function stripFences(text: string): string {
  let cleaned = text.trim().replace(/^```(?:json)?\s*\n?/, "");
  cleaned = cleaned.replace(/\n?```\s*$/, "");
  return cleaned;
}

/**
 * Send a data sample to the LLM and parse generated validation rules.
 * Returns [] on any failure (budget, network, parse) — never throws.
 */
export async function generateRules(
  data: TabularData,
  findings: readonly Finding[],
  provider: string = "anthropic",
): Promise<GeneratedRule[]> {
  const blocks = buildSampleBlocks(data, findings);
  const userPrompt = "Here is the dataset summary:\n\n" + JSON.stringify(blocks, null, 2);

  const model = getEnv("GOLDENCHECK_LLM_MODEL") || DEFAULT_MODELS[provider] || "";
  const estimated = estimateCost(3000, 1000, model);
  if (!checkBudget(estimated)) {
    return [];
  }

  const costReport = new CostReport();
  try {
    const { text, inputTokens, outputTokens } = await callLlmForRules(provider, userPrompt);
    costReport.record(inputTokens, outputTokens, model);

    const cleaned = stripFences(text);
    const parsed = JSON.parse(cleaned) as { rules?: unknown[] };
    const rules = Array.isArray(parsed.rules) ? parsed.rules.map(ruleFromJson) : [];
    return rules;
  } catch {
    return [];
  }
}

// ---------------------------------------------------------------------------
// Rule application (pure)
// ---------------------------------------------------------------------------

/** Apply generated rules to data and return findings. */
export function applyRules(
  data: TabularData,
  rules: readonly GeneratedRule[],
): Finding[] {
  const findings: Finding[] = [];
  for (const rule of rules) {
    try {
      findings.push(...applySingleRule(data, rule));
    } catch {
      // Mirror Python: a broken rule is logged-and-skipped, never fatal.
    }
  }
  return findings;
}

function applySingleRule(data: TabularData, rule: GeneratedRule): Finding[] {
  if (!data.columns.includes(rule.column)) {
    return [];
  }

  const params = rule.params;
  const findings: Finding[] = [];

  if (rule.ruleType === "regex" && params.pattern) {
    if (data.isString(rule.column)) {
      const nonNull = data.stringValues(rule.column);
      if (nonNull.length > 0) {
        const re = new RegExp(params.pattern);
        const nonMatches = nonNull.filter((v) => !re.test(v));
        const nonMatchCount = nonMatches.length;
        if (nonMatchCount > 0 && nonMatchCount < nonNull.length * 0.5) {
          findings.push(
            makeFinding({
              severity: Severity.WARNING,
              column: rule.column,
              check: rule.check,
              message: `${nonMatchCount} row(s) have invalid format — ${rule.description}`,
              affectedRows: nonMatchCount,
              sampleValues: nonMatches.slice(0, 5),
              source: "llm",
              confidence: 0.8,
            }),
          );
        }
      }
    }
  } else if (rule.ruleType === "length") {
    if (data.isString(rule.column)) {
      const nonNull = data.stringValues(rule.column);
      if (nonNull.length > 0) {
        const violations = nonNull.filter((v) => {
          const len = v.length;
          if (params.minLength != null && len < params.minLength) return true;
          if (params.maxLength != null && len > params.maxLength) return true;
          return false;
        });
        if (violations.length > 0 && violations.length < nonNull.length * 0.5) {
          findings.push(
            makeFinding({
              severity: Severity.WARNING,
              column: rule.column,
              check: rule.check,
              message: `${violations.length} row(s) have invalid length — ${rule.description}`,
              affectedRows: violations.length,
              sampleValues: violations.slice(0, 5),
              source: "llm",
              confidence: 0.8,
            }),
          );
        }
      }
    }
  } else if (rule.ruleType === "value_list" && params.invalidValues && params.invalidValues.length > 0) {
    const invalidSet = new Set(params.invalidValues);
    const nonNull = data.stringValues(rule.column);
    const violations = nonNull.filter((v) => invalidSet.has(v));
    if (violations.length > 0) {
      findings.push(
        makeFinding({
          severity: Severity.WARNING,
          column: rule.column,
          check: rule.check,
          message: `${violations.length} row(s) contain invalid values — ${rule.description}`,
          affectedRows: violations.length,
          sampleValues: violations.slice(0, 5),
          source: "llm",
          confidence: 0.8,
        }),
      );
    }
  } else if (rule.ruleType === "cross_column" && params.relatedColumn) {
    if (data.columns.includes(params.relatedColumn)) {
      findings.push(
        makeFinding({
          severity: Severity.WARNING,
          column: rule.column,
          check: rule.check,
          message: `Cross-column inconsistency — ${rule.description}`,
          affectedRows: 0,
          source: "llm",
          confidence: 0.7,
        }),
      );
    }
  }

  return findings;
}
