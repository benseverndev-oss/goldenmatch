/**
 * Semantic type classifier — classifies columns by name hints and value signals.
 * Port of goldencheck/semantic/classifier.py.
 */

import type { TabularData } from "../data.js";
import type { TypeDef, ColumnClassification } from "../types.js";
import { BASE_TYPES } from "./types.js";
import { getDomainTypes } from "./domains/index.js";

// Format-match regex patterns (matching Python's classifier)
const FORMAT_CHECKS: Record<string, { pattern: RegExp; minPct: number }> = {
  email: { pattern: /@.*\./, minPct: 0.70 },
  phone: { pattern: /\d{3}.*\d{3}.*\d{4}/, minPct: 0.70 },
  date: { pattern: /\d{4}-\d{2}-\d{2}/, minPct: 0.50 },
};

/**
 * Load type definitions with layered priority: base < domain < user.
 * Dict insertion order matters (later entries override earlier on name match).
 */
export function loadTypeDefs(
  domain?: string | null,
  userTypes?: Readonly<Record<string, TypeDef>>,
): Record<string, TypeDef> {
  const result: Record<string, TypeDef> = { ...BASE_TYPES };

  if (domain) {
    const domainTypes = getDomainTypes(domain);
    if (domainTypes) {
      Object.assign(result, domainTypes);
    }
  }

  if (userTypes) {
    Object.assign(result, userTypes);
  }

  return result;
}

/**
 * Classify all columns in a dataset.
 * Returns a map of column name → classification.
 */
export function classifyColumns(
  data: TabularData,
  typeDefs?: Record<string, TypeDef>,
  domain?: string | null,
): Record<string, ColumnClassification> {
  const defs = typeDefs ?? loadTypeDefs(domain);
  const result: Record<string, ColumnClassification> = {};

  for (const col of data.columns) {
    // Try name match first, then value match
    const nameMatch = matchByName(col, defs);
    if (nameMatch) {
      result[col] = nameMatch;
      continue;
    }

    const valueMatch = matchByValue(data, col, defs);
    if (valueMatch) {
      result[col] = valueMatch;
      continue;
    }

    result[col] = { typeName: null, source: "none" };
  }

  return result;
}

/**
 * Match column by name hints.
 * Hint ending with `_`: prefix-only match (NOT substring).
 * Hint starting with `_`: suffix-only match.
 * Otherwise: substring match.
 */
export function matchByName(
  colName: string,
  typeDefs: Readonly<Record<string, TypeDef>>,
): ColumnClassification | null {
  const lower = colName.toLowerCase();

  for (const [typeName, def] of Object.entries(typeDefs)) {
    for (const hint of def.nameHints) {
      const hintLower = hint.toLowerCase();

      if (hintLower.endsWith("_")) {
        // Prefix-only: "is_" matches "is_active" but NOT "diagnosis_desc"
        if (lower.startsWith(hintLower)) {
          return { typeName, source: "name" };
        }
      } else if (hintLower.startsWith("_")) {
        // Suffix-only: "_at" matches "created_at" but NOT "attention"
        if (lower.endsWith(hintLower)) {
          return { typeName, source: "name" };
        }
      } else {
        // Substring match
        if (lower.includes(hintLower)) {
          return { typeName, source: "name" };
        }
      }
    }
  }

  return null;
}

/**
 * Match column by value signals.
 * Returns first type where ALL signals pass.
 */
export function matchByValue(
  data: TabularData,
  colName: string,
  typeDefs: Readonly<Record<string, TypeDef>>,
): ColumnClassification | null {
  const nonNull = data.dropNulls(colName);
  if (nonNull.length === 0) return null;

  for (const [typeName, def] of Object.entries(typeDefs)) {
    if (Object.keys(def.valueSignals).length === 0) continue;
    if (checkValueSignals(data, colName, nonNull, def.valueSignals)) {
      return { typeName, source: "value" };
    }
  }

  return null;
}

function checkValueSignals(
  data: TabularData,
  colName: string,
  nonNull: unknown[],
  signals: Readonly<Record<string, unknown>>,
): boolean {
  const total = nonNull.length;

  for (const [key, value] of Object.entries(signals)) {
    switch (key) {
      case "min_unique_pct": {
        const uniquePct = data.nUnique(colName) / total;
        if (uniquePct < (value as number)) return false;
        break;
      }
      case "max_unique": {
        if (data.nUnique(colName) > (value as number)) return false;
        break;
      }
      case "format_match": {
        const fmtName = value as string;
        const fmt = FORMAT_CHECKS[fmtName];
        if (!fmt) break;
        const minPct = (signals["min_match_pct"] as number | undefined) ?? fmt.minPct;
        const strings = data.stringValues(colName);
        if (strings.length === 0) return false;
        const matchCount = strings.filter((s) => fmt.pattern.test(s)).length;
        if (matchCount / strings.length < minPct) return false;
        break;
      }
      case "min_match_pct":
        // Handled by format_match
        break;
      case "mixed_case": {
        const sample = data.stringValues(colName).slice(0, 50);
        const hasUpper = sample.some((s) => /[A-Z]/.test(s));
        const hasLower = sample.some((s) => /[a-z]/.test(s));
        if (!(hasUpper && hasLower)) return false;
        break;
      }
      case "avg_length_min": {
        const strings = data.stringValues(colName);
        if (strings.length === 0) return false;
        const avgLen = strings.reduce((sum, s) => sum + s.length, 0) / strings.length;
        if (avgLen < (value as number)) return false;
        break;
      }
      case "numeric": {
        if (!data.isNumeric(colName)) return false;
        break;
      }
      case "short_strings": {
        const strings = data.stringValues(colName);
        if (strings.length === 0) return false;
        const avgLen = strings.reduce((sum, s) => sum + s.length, 0) / strings.length;
        if (avgLen >= 5) return false;
        break;
      }
    }
  }

  return true;
}
