/**
 * Pattern grammar inducer — derive regex grammars from string columns.
 * TypeScript port of goldencheck/baseline/patterns.py.
 * Edge-safe: no Node.js dependencies.
 */

import type { TabularData } from "../data.js";
import type { PatternGrammar } from "./models.js";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Minimum number of rows required to run pattern induction. */
const MIN_ROWS = 30;

/** Minimum fractional coverage for a grammar to be reported. */
const MIN_COVERAGE = 0.03;

/** Regex special characters that must be escaped when used as literals. */
const REGEX_SPECIAL = new Set([
  "\\", ".", "^", "$", "*", "+", "?", "{", "}", "[", "]", "|", "(", ")",
]);

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/**
 * Convert a string value to its character-class skeleton.
 * Uppercase letters -> 'A', lowercase letters -> 'a', digits -> '0',
 * all other characters kept as-is.
 *
 * Examples:
 *   "ABC-1234" -> "AAA-0000"
 *   "Hello World" -> "Aaaaa Aaaaa"
 */
function toSkeleton(value: string): string {
  const result: string[] = [];
  for (const ch of value) {
    if (ch >= "A" && ch <= "Z") {
      result.push("A");
    } else if (ch >= "a" && ch <= "z") {
      result.push("a");
    } else if (ch >= "0" && ch <= "9") {
      result.push("0");
    } else {
      result.push(ch);
    }
  }
  return result.join("");
}

/** Return a regex-safe version of a literal (non-class) character. */
function escapeLiteral(ch: string): string {
  if (REGEX_SPECIAL.has(ch)) {
    return "\\" + ch;
  }
  return ch;
}

/**
 * Convert a character-class skeleton to a compact regex pattern.
 * Consecutive identical skeleton characters are merged into {N} quantifiers.
 *
 * Examples:
 *   "AAA-0000" -> "[A-Z]{3}-[0-9]{4}"
 *   "aa0"      -> "[a-z]{2}[0-9]{1}"
 */
function skeletonToRegex(skeleton: string): string {
  if (!skeleton) return "";

  // Group consecutive identical characters
  const groups: Array<[string, number]> = [];
  let current = skeleton[0]!;
  let count = 1;
  for (let i = 1; i < skeleton.length; i++) {
    const ch = skeleton[i]!;
    if (ch === current) {
      count++;
    } else {
      groups.push([current, count]);
      current = ch;
      count = 1;
    }
  }
  groups.push([current, count]);

  const parts: string[] = [];
  for (const [ch, n] of groups) {
    if (ch === "A") {
      parts.push(`[A-Z]{${n}}`);
    } else if (ch === "a") {
      parts.push(`[a-z]{${n}}`);
    } else if (ch === "0") {
      parts.push(`[0-9]{${n}}`);
    } else {
      // Literal character — escape if needed
      const escaped = escapeLiteral(ch);
      if (n === 1) {
        parts.push(escaped);
      } else {
        parts.push(`${escaped}{${n}}`);
      }
    }
  }

  return parts.join("");
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Induce pattern grammars from a list of string values (single column).
 *
 * Returns PatternGrammar objects for patterns whose coverage meets or exceeds
 * MIN_COVERAGE (3%). Patterns are sorted descending by coverage. Identical
 * regex patterns are merged (coverage summed).
 */
export function induceColumnGrammars(column: string, values: string[]): PatternGrammar[] {
  if (!values.length) return [];

  const total = values.length;
  const skeletonCounts = new Map<string, number>();
  const skeletonRegex = new Map<string, string>();

  for (const val of values) {
    const skel = toSkeleton(val);
    skeletonCounts.set(skel, (skeletonCounts.get(skel) ?? 0) + 1);
    if (!skeletonRegex.has(skel)) {
      skeletonRegex.set(skel, skeletonToRegex(skel));
    }
  }

  // Build grammars, merging identical regex patterns
  const patternCoverage = new Map<string, number>();
  for (const [skel, cnt] of skeletonCounts) {
    const coverage = cnt / total;
    if (coverage < MIN_COVERAGE) continue;
    const regex = skeletonRegex.get(skel)!;
    patternCoverage.set(regex, (patternCoverage.get(regex) ?? 0) + coverage);
  }

  // Sort descending by coverage
  const entries = [...patternCoverage.entries()].sort((a, b) => b[1] - a[1]);

  return entries.map(([regex, cov]) => ({
    column,
    regex,
    coverage: Math.round(cov * 1e6) / 1e6,
  }));
}

/**
 * Induce pattern grammars for all string columns in the data.
 *
 * Only processes string-dtype columns. Requires at least MIN_ROWS rows.
 * Columns with no grammar meeting the 3% threshold are omitted.
 */
export function inducePatterns(data: TabularData): Record<string, PatternGrammar[]> {
  if (data.rowCount < MIN_ROWS) return {};

  const result: Record<string, PatternGrammar[]> = {};

  for (const col of data.columns) {
    if (!data.isString(col)) continue;

    const values = data.stringValues(col);
    if (values.length < MIN_ROWS) continue;

    const grammars = induceColumnGrammars(col, values);
    if (grammars.length > 0) {
      result[col] = grammars;
    }
  }

  return result;
}
