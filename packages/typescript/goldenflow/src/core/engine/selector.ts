/**
 * Selector — pick auto-applicable transforms for a column based on its profile.
 */

import type { ColumnProfile, TransformInfo } from "../types.js";
import { listTransforms } from "../transforms/index.js";

/** GoldenCheck finding check → transform name mapping. */
export const FINDING_TRANSFORM_MAP: Readonly<Record<string, readonly string[]>> = {
  type_inference: ["strip", "to_integer"],
  nullability: ["null_standardize"],
  uniqueness: ["strip", "collapse_whitespace", "email_normalize"],
  format_detection: ["phone_e164", "email_normalize", "date_iso8601", "zip_normalize"],
  range_distribution: ["clamp"],
  cardinality: ["category_auto_correct", "category_standardize"],
  pattern_consistency: ["phone_e164", "date_iso8601", "zip_normalize", "ssn_format"],
  encoding_detection: ["normalize_unicode", "normalize_quotes", "fix_mojibake"],
  sequence_detection: ["pad_left"],
  drift_detection: [],
  temporal_order: ["date_iso8601", "date_validate"],
  null_correlation: [],
  cross_column_validation: ["clamp"],
  cross_column: ["date_validate", "age_from_dob"],
};

const STRING_LIKE_TYPES = new Set([
  "string", "email", "phone", "name", "address", "date",
]);

export function selectTransforms(
  profile: ColumnProfile,
  _confidenceThreshold = 0.8,
): TransformInfo[] {
  const all = listTransforms();
  let selected: TransformInfo[] = [];

  for (const t of all) {
    if (!t.autoApply) continue;
    if (t.inputTypes.includes(profile.inferredType)) {
      selected.push(t);
    } else if (
      t.inputTypes.includes("string") &&
      STRING_LIKE_TYPES.has(profile.inferredType)
    ) {
      selected.push(t);
    }
  }

  // Filter out category_auto_correct for high-cardinality columns
  if (profile.uniquePct > 0.1) {
    selected = selected.filter((t) => t.name !== "category_auto_correct");
  }

  selected.sort((a, b) => b.priority - a.priority);
  return selected;
}

export function selectFromFindings(
  findings: readonly Record<string, unknown>[],
): Record<string, string[]> {
  const columnTransforms: Record<string, string[]> = {};

  for (const finding of findings) {
    const check = String(finding["check"] ?? "");
    const column = String(finding["column"] ?? "");
    if (!column) continue;
    const transformNames = FINDING_TRANSFORM_MAP[check] ?? [];
    if (transformNames.length > 0) {
      if (!columnTransforms[column]) columnTransforms[column] = [];
      columnTransforms[column]!.push(...transformNames);
    }
  }

  // Deduplicate
  for (const col of Object.keys(columnTransforms)) {
    columnTransforms[col] = [...new Set(columnTransforms[col])];
  }
  return columnTransforms;
}
