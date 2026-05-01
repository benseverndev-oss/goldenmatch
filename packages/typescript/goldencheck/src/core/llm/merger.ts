/**
 * Merge LLM response into existing findings list (immutable).
 * Port of goldencheck/llm/merger.py.
 * Edge-safe: no Node.js dependencies.
 */

import type { Finding } from "../types.js";
import { Severity, makeFinding, replaceFinding } from "../types.js";
import type { LLMResponse } from "./prompts.js";

const SEVERITY_MAP: Record<string, (typeof Severity)[keyof typeof Severity]> = {
  error: Severity.ERROR,
  warning: Severity.WARNING,
  info: Severity.INFO,
};

/**
 * Required keywords per check name.
 * If the LLM message lacks them, a suffix is appended.
 */
const REQUIRED_KEYWORDS: Record<string, string[]> = {
  cross_column: ["mismatch", "inconsistent", "doesn't match"],
  invalid_values: ["invalid"],
};

const KEYWORD_SUFFIXES: Record<string, string> = {
  cross_column: " [cross-column mismatch detected]",
  invalid_values: " [invalid values detected]",
};

/** Ensure the message contains at least one required keyword for the check. */
function ensureKeywords(check: string, message: string): string {
  const keywords = REQUIRED_KEYWORDS[check];
  if (!keywords) return message;
  const msgLower = message.toLowerCase();
  if (keywords.some((kw) => msgLower.includes(kw))) return message;
  return message + (KEYWORD_SUFFIXES[check] ?? "");
}

/** Strip existing "(suppressed: ...)" suffixes before merging. */
function stripSuppressionSuffix(message: string): string {
  return message.replace(/\s*\(suppressed:.*?\)\s*$/, "");
}

/**
 * Merge LLM response into findings. Returns a new list (never mutates originals).
 *
 * - New issues: appended with source="llm"
 * - Upgrades: matched by (column, check), severity updated, reason appended
 * - Downgrades: matched by (column, check), severity updated, reason appended
 * - Relations: appended as Finding(column="col_a,col_b", source="llm")
 */
export function mergeLlmFindings(
  findings: readonly Finding[],
  response: LLMResponse | null,
): Finding[] {
  if (response === null) return [...findings];

  const result = [...findings];

  // Build lookup index: (column, check) -> index in result
  const index = new Map<string, number>();
  for (let i = 0; i < result.length; i++) {
    const f = result[i]!;
    index.set(`${f.column}\0${f.check}`, i);
  }

  // Process per-column assessments
  for (const [colName, assessment] of Object.entries(response.columns)) {
    // New issues
    for (const issue of assessment.issues) {
      const sev = SEVERITY_MAP[issue.severity.toLowerCase()] ?? Severity.WARNING;
      result.push(
        makeFinding({
          severity: sev,
          column: colName,
          check: issue.check,
          message: ensureKeywords(issue.check, issue.message),
          sampleValues: issue.affected_values ?? [],
          source: "llm",
        }),
      );
    }

    // Upgrades (immutable via replaceFinding)
    for (const upgrade of assessment.upgrades) {
      const key = `${colName}\0${upgrade.original_check}`;
      const idx = index.get(key);
      if (idx !== undefined) {
        const old = result[idx]!;
        result[idx] = replaceFinding(old, {
          severity: SEVERITY_MAP[upgrade.new_severity.toLowerCase()] ?? old.severity,
          message: `${stripSuppressionSuffix(old.message)} [LLM: ${upgrade.reason}]`,
          source: "llm",
        });
      } else {
        // Create as new issue when original not found
        result.push(
          makeFinding({
            severity: SEVERITY_MAP[upgrade.new_severity.toLowerCase()] ?? Severity.WARNING,
            column: colName,
            check: upgrade.original_check,
            message: upgrade.reason,
            source: "llm",
          }),
        );
      }
    }

    // Downgrades (immutable via replaceFinding)
    for (const downgrade of assessment.downgrades) {
      const key = `${colName}\0${downgrade.original_check}`;
      const idx = index.get(key);
      if (idx !== undefined) {
        const old = result[idx]!;
        result[idx] = replaceFinding(old, {
          severity: SEVERITY_MAP[downgrade.new_severity.toLowerCase()] ?? old.severity,
          message: `${stripSuppressionSuffix(old.message)} [LLM: ${downgrade.reason}]`,
          source: "llm",
        });
      }
      // else: silently ignore downgrades for findings that don't exist
    }
  }

  // Process relations
  for (const relation of response.relations) {
    const colKey = [...relation.columns].sort().join(",");
    result.push(
      makeFinding({
        severity: Severity.WARNING,
        column: colKey,
        check: relation.type,
        message: relation.reasoning,
        source: "llm",
      }),
    );
  }

  return result;
}
