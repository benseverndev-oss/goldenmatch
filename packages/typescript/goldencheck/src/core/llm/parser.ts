/**
 * Parse and validate LLM JSON responses.
 * Port of goldencheck/llm/parser.py.
 * Edge-safe: no Node.js dependencies.
 */

import type { LLMResponse, LLMColumnAssessment } from "./prompts.js";

/**
 * Parse raw LLM text into a validated LLMResponse.
 * Strips markdown code fences before JSON parsing.
 * Returns null on parse or validation failure.
 */
export function parseLlmResponse(raw: string): LLMResponse | null {
  // Strip markdown code fences if present
  let cleaned = raw.trim();
  cleaned = cleaned.replace(/^```(?:json)?\s*\n?/, "");
  cleaned = cleaned.replace(/\n?```\s*$/, "");

  let data: unknown;
  try {
    data = JSON.parse(cleaned);
  } catch (e) {
    console.warn("Failed to parse LLM response as JSON:", e instanceof Error ? e.message : String(e));
    console.warn("Raw response (first 500 chars):", cleaned.slice(0, 500));
    return null;
  }

  if (!isPlainObject(data)) return null;

  // Validate and normalize top-level structure
  const obj = data as Record<string, unknown>;

  const columns: Record<string, LLMColumnAssessment> = {};
  if (obj.columns != null) {
    if (!isPlainObject(obj.columns)) return null;
    const rawCols = obj.columns as Record<string, unknown>;
    for (const [colName, rawAssessment] of Object.entries(rawCols)) {
      const assessment = validateColumnAssessment(rawAssessment);
      if (assessment === null) return null;
      columns[colName] = assessment;
    }
  }

  const relations: LLMResponse["relations"] = [];
  if (obj.relations != null) {
    if (!Array.isArray(obj.relations)) return null;
    for (const rel of obj.relations) {
      if (!isPlainObject(rel)) return null;
      const r = rel as Record<string, unknown>;
      if (typeof r.type !== "string") return null;
      if (!Array.isArray(r.columns) || !r.columns.every((c: unknown) => typeof c === "string"))
        return null;
      if (typeof r.reasoning !== "string") return null;
      relations.push({
        type: r.type,
        columns: r.columns as string[],
        reasoning: r.reasoning,
      });
    }
  }

  return { columns, relations };
}

// --- Internal helpers ---

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function validateColumnAssessment(raw: unknown): LLMColumnAssessment | null {
  if (!isPlainObject(raw)) return null;
  const obj = raw as Record<string, unknown>;

  const semanticType =
    obj.semantic_type === undefined || obj.semantic_type === null
      ? null
      : typeof obj.semantic_type === "string"
        ? obj.semantic_type
        : null;

  const issues: LLMColumnAssessment["issues"] = [];
  if (obj.issues != null) {
    if (!Array.isArray(obj.issues)) return null;
    for (const issue of obj.issues) {
      if (!isPlainObject(issue)) return null;
      const i = issue as Record<string, unknown>;
      if (typeof i.severity !== "string" || typeof i.check !== "string" || typeof i.message !== "string")
        return null;
      issues.push({
        severity: i.severity,
        check: i.check,
        message: i.message,
        affected_values: Array.isArray(i.affected_values)
          ? (i.affected_values as unknown[]).map(String)
          : [],
      });
    }
  }

  const upgrades: LLMColumnAssessment["upgrades"] = [];
  if (obj.upgrades != null) {
    if (!Array.isArray(obj.upgrades)) return null;
    for (const upg of obj.upgrades) {
      if (!isPlainObject(upg)) return null;
      const u = upg as Record<string, unknown>;
      if (
        typeof u.original_check !== "string" ||
        typeof u.original_severity !== "string" ||
        typeof u.new_severity !== "string" ||
        typeof u.reason !== "string"
      )
        return null;
      upgrades.push({
        original_check: u.original_check,
        original_severity: u.original_severity,
        new_severity: u.new_severity,
        reason: u.reason,
      });
    }
  }

  const downgrades: LLMColumnAssessment["downgrades"] = [];
  if (obj.downgrades != null) {
    if (!Array.isArray(obj.downgrades)) return null;
    for (const dg of obj.downgrades) {
      if (!isPlainObject(dg)) return null;
      const d = dg as Record<string, unknown>;
      if (
        typeof d.original_check !== "string" ||
        typeof d.original_severity !== "string" ||
        typeof d.new_severity !== "string" ||
        typeof d.reason !== "string"
      )
        return null;
      downgrades.push({
        original_check: d.original_check,
        original_severity: d.original_severity,
        new_severity: d.new_severity,
        reason: d.reason,
      });
    }
  }

  return { semantic_type: semanticType, issues, upgrades, downgrades };
}
