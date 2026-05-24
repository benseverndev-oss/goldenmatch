/**
 * Identity-safe PK preflight profiler (closes goldenmatch issue #207).
 * Port of goldencheck/relations/identity_safe_pk.py.
 *
 * Warns when the input table has NO viable stable unique identifier
 * column. Without one, downstream consumers like goldenmatch's Identity
 * Graph fall back to a payload-hash record_id which silently collides on
 * physically-different rows that happen to have identical column values.
 *
 * Heuristic for a "PK candidate":
 * - All values non-null (no NULLs in the column)
 * - Fully unique (n_unique == n_rows)
 * - Not blocked by name heuristics that mean "this is value data, not ID"
 *   (email, phone, name, address, ...)
 * - Reasonable type for an identifier (int / string — not float / bool)
 */

import type { TabularData } from "../data.js";
import { type Finding, Severity, makeFinding } from "../types.js";
import type { RelationProfiler } from "../profilers/base.js";

// Column-name prefixes/substrings that imply "this is data the user could
// plausibly edit later", not a stable PK.
const VALUE_COLUMN_PATTERNS: readonly string[] = [
  "email",
  "phone",
  "fax",
  "address",
  "street",
  "city",
  "name",
  "first_name",
  "last_name",
  "company",
  "title",
  "description",
  "notes",
  "comment",
  "url",
  "website",
  "ssn",
];

// Column-name patterns that strongly suggest a PK / surrogate-key.
const PK_NAME_PATTERNS: readonly string[] = [
  "id",
  "uuid",
  "guid",
  "key",
  "pk",
  "primary_key",
  "row_id",
  "record_id",
  "ext_id",
  "external_id",
];

function looksLikeValueColumn(name: string): boolean {
  const lower = name.toLowerCase();
  return VALUE_COLUMN_PATTERNS.some((p) => lower.includes(p));
}

function looksLikePkColumn(name: string): boolean {
  const lower = name.toLowerCase();
  // Exact match or `<thing>_id` / `id_<thing>` style; substring `id` would
  // FP on `paid`, `said`, etc., so anchor on word-boundary-ish edges.
  for (const p of PK_NAME_PATTERNS) {
    if (lower === p || lower.endsWith(`_${p}`) || lower.startsWith(`${p}_`)) {
      return true;
    }
  }
  return false;
}

/** Return [qualifies, why_or_disqualifier]. */
function columnQualifiesAsPk(data: TabularData, column: string): [boolean, string] {
  if (looksLikeValueColumn(column)) {
    return [false, "value-shaped name (email/name/address/etc.)"];
  }
  const dtype = data.dtype(column);
  if (dtype === "float" || dtype === "boolean") {
    return [false, `unsuitable dtype (${dtype})`];
  }
  const values = data.column(column);
  const nRows = values.length;
  if (nRows === 0) {
    return [false, "empty sample"];
  }
  const nNulls = data.nullCount(column);
  if (nNulls > 0) {
    return [false, `${nNulls} null value(s)`];
  }
  if (data.nUnique(column) !== nRows) {
    return [false, "non-unique values"];
  }
  return [true, "stable unique non-null"];
}

export class IdentitySafePkProfiler implements RelationProfiler {
  profile(data: TabularData): Finding[] {
    if (data.columns.length === 0) {
      return [];
    }

    const candidates: string[] = [];
    const namedPkDisqualifiers = new Map<string, string>();

    for (const column of data.columns) {
      const [qualifies, reason] = columnQualifiesAsPk(data, column);
      if (qualifies) {
        candidates.push(column);
      } else if (looksLikePkColumn(column)) {
        namedPkDisqualifiers.set(column, reason);
      }
    }

    if (candidates.length > 0) {
      // At least one viable PK; no preflight warning.
      return [];
    }

    // No qualifying PK column. If a column LOOKS like a PK but failed the
    // uniqueness/null test, call it out specifically.
    if (namedPkDisqualifiers.size > 0) {
      const [target, why] = [...namedPkDisqualifiers.entries()][0]!;
      return [
        makeFinding({
          severity: Severity.WARNING,
          column: target,
          check: "identity_safe_pk",
          message:
            `Column '${target}' looks like a PK by name but ` +
            `isn't stable (${why}). Identity-graph downstreams ` +
            `will fall back to payload-hash record_ids, which ` +
            `silently collide on duplicate raw rows.`,
          affectedRows: data.rowCount,
          sampleValues: [],
          suggestion:
            `Either fix the column ('${target}' should be ` +
            `non-null + unique), OR pass an explicit ` +
            `source_pk_column to the Identity Graph resolver, ` +
            `OR add a stable surrogate key (UUID / ` +
            `autoincrement) to the dataset.`,
          confidence: 0.9,
        }),
      ];
    }

    // No named-PK column either. Generic dataset-level warning.
    let sampleCols = data.columns.slice(0, 5).join(", ");
    if (data.columns.length > 5) {
      sampleCols += ", ...";
    }
    return [
      makeFinding({
        severity: Severity.WARNING,
        column: "__dataset__",
        check: "identity_safe_pk",
        message:
          "No viable stable PK column detected. Columns " +
          `(${sampleCols}) have nulls, duplicates, or look ` +
          "like editable value columns (email/name/address).",
        affectedRows: data.rowCount,
        sampleValues: [],
        suggestion:
          "If feeding this dataset to goldenmatch's Identity " +
          "Graph, pass an explicit source_pk_column on " +
          "IdentityConfig OR add a stable surrogate key " +
          "(UUID / autoincrement). Without one, record_ids fall " +
          "back to a payload-hash that collides on duplicate " +
          "raw rows.",
        confidence: 0.8,
      }),
    ];
  }
}
