/**
 * strategy.ts — Agent decision keystone: profile -> strategy -> config.
 * Edge-safe: no `node:` imports. Ported from goldenmatch/core/agent.py.
 *
 * Task-0 confirmations (pinned so later tasks don't re-derive them):
 *   - `DedupeOptions.config`: a GoldenMatchConfig is passed as
 *     `dedupe(rows, { config })` (option key `config`); see api.ts:33-52.
 *   - `confidence_distribution`: FOUR keys — `auto_merged` / `review` /
 *     `auto_rejected` / `total_pairs` (= result.scoredPairs.length).
 *   - `DomainProfile` (domain.ts:16): exposes `name` + `confidence`
 *     (confidence already = min(1, score/10)). Used directly as
 *     `domain_confidence` for the `> 0.5` branch. NOTE: this differs from
 *     Python's `hits/len(signals)` formula, so only clear-cut domain datasets
 *     are guaranteed to agree (documented Wave-1 caveat).
 *   - `autoConfigureRowsIterate` is async (autoconfig.ts:583) -> the
 *     AgentSession `autoconfigure`/`deduplicate` methods are async.
 */

import type { Row } from "../types.js";
import type { DataProfile, FieldProfile } from "./types.js";

// Column-name patterns that indicate sensitive PII (Python _SENSITIVE_PATTERNS).
const SENSITIVE_PATTERNS = new Set([
  "ssn",
  "social_security",
  "dob",
  "date_of_birth",
  "birth_date",
  "drivers_license",
  "dl_number",
]);

/**
 * Profile rows for strategy selection (port of `profile_for_agent`).
 *
 * For each column computes uniqueness (distinct/row_count), null rate, and
 * average UTF-8 byte length (string columns only). Detects sensitive fields by
 * column-name pattern matching.
 */
export function profileForAgent(rows: readonly Row[]): DataProfile {
  const height = rows.length;
  const cols = height > 0 ? Object.keys(rows[0]!) : [];
  let hasSensitive = false;
  const fields: FieldProfile[] = [];
  const encoder = new TextEncoder();

  for (const col of cols) {
    const colLower = col.toLowerCase().replace(/ /g, "_");
    if (SENSITIVE_PATTERNS.has(colLower)) hasSensitive = true;

    const values = rows.map((r) => r[col]);
    // Polars `null_count` counts true nulls. We also treat undefined / "" as
    // null so empty-string cells don't masquerade as present values.
    const nonNull = values.filter(
      (v) => v !== null && v !== undefined && v !== "",
    );
    const nullCount = height - nonNull.length;
    const distinct = new Set(nonNull.map((v) => String(v))).size;
    const uniqueness = height > 0 ? distinct / height : 0;
    const nullRate = height > 0 ? nullCount / height : 0;

    // Type category: numeric only when every non-null value parses as a number.
    const allNumeric =
      nonNull.length > 0 &&
      nonNull.every(
        (v) => typeof v === "number" || !Number.isNaN(Number(v)),
      );
    const type: FieldProfile["type"] =
      nonNull.length === 0 ? "string" : allNumeric ? "numeric" : "string";

    // Average UTF-8 byte length over non-null string values (Python uses
    // `str.len_bytes()`; TextEncoder().encode(...).length is the edge-safe
    // equivalent — the node-only byte API is intentionally avoided here).
    let avgLength = 0;
    if (type === "string" && nonNull.length > 0) {
      const total = nonNull.reduce(
        (acc, v) => acc + encoder.encode(String(v)).length,
        0,
      );
      avgLength = total / nonNull.length;
    }

    fields.push({
      name: col,
      type,
      uniqueness,
      null_rate: nullRate,
      avg_length: avgLength,
    });
  }

  return { row_count: height, fields, has_sensitive: hasSensitive };
}
