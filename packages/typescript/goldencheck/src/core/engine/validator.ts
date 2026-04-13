/**
 * Validator — checks TabularData against pinned rules in GoldenCheckConfig.
 * Port of goldencheck/engine/validator.py.
 */

import type { TabularData } from "../data.js";
import {
  type Finding,
  type GoldenCheckConfig,
  type ColumnRule,
  Severity,
  makeFinding,
} from "../types.js";
import { isNullish } from "../data.js";

/**
 * Validate data against a GoldenCheckConfig.
 * Checks: existence, required, unique, enum, range.
 * Filters out findings that match config.ignore entries.
 * Returns findings sorted by severity descending (ERROR first).
 */
export function validateData(
  data: TabularData,
  config: GoldenCheckConfig,
): Finding[] {
  const findings: Finding[] = [];
  const dataColumns = new Set(data.columns);

  for (const [colName, rule] of Object.entries(config.columns)) {
    if (!dataColumns.has(colName)) {
      findings.push(
        makeFinding({
          severity: Severity.WARNING,
          column: colName,
          check: "existence",
          message: `Column '${colName}' defined in rules but not found in data`,
        }),
      );
      continue;
    }

    findings.push(...checkColumn(data, colName, rule));
  }

  // Filter out ignored findings
  const ignored = new Set<string>(
    config.ignore.map((i) => `${i.column}\0${i.check}`),
  );
  const filtered = findings.filter(
    (f) => !ignored.has(`${f.column}\0${f.check}`),
  );

  // Sort by severity descending (ERROR=3 first)
  filtered.sort((a, b) => b.severity - a.severity);
  return filtered;
}

// ---------------------------------------------------------------------------
// Per-column checks
// ---------------------------------------------------------------------------

function checkColumn(
  data: TabularData,
  name: string,
  rule: ColumnRule,
): Finding[] {
  const findings: Finding[] = [];

  // Required check
  if (rule.required) {
    const nullCount = data.nullCount(name);
    if (nullCount > 0) {
      findings.push(
        makeFinding({
          severity: Severity.ERROR,
          column: name,
          check: "required",
          message: `Required column has ${nullCount} null values`,
          affectedRows: nullCount,
        }),
      );
    }
  }

  // Unique check
  if (rule.unique) {
    const nonNullValues = data.dropNulls(name);
    const uniqueSet = new Set(nonNullValues);
    const dups = nonNullValues.length - uniqueSet.size;
    if (dups > 0) {
      findings.push(
        makeFinding({
          severity: Severity.ERROR,
          column: name,
          check: "unique",
          message: `Column should be unique but has ${dups} duplicates`,
          affectedRows: dups,
        }),
      );
    }
  }

  // Enum check
  if (rule.enum && rule.enum.length > 0) {
    const allowedSet = new Set(rule.enum);
    const invalid: string[] = [];
    for (const v of data.column(name)) {
      if (isNullish(v)) continue;
      const s = String(v);
      if (!allowedSet.has(s)) {
        invalid.push(s);
      }
    }
    if (invalid.length > 0) {
      const samples = invalid.slice(0, 5);
      findings.push(
        makeFinding({
          severity: Severity.ERROR,
          column: name,
          check: "enum",
          message: `${invalid.length} values not in allowed enum [${rule.enum.join(", ")}]`,
          affectedRows: invalid.length,
          sampleValues: samples,
        }),
      );
    }
  }

  // Range check — no try/catch; isNullish + Number.isFinite guards handle non-numeric safely
  if (rule.range && rule.range.length === 2) {
    const [lo, hi] = rule.range;
    const outOfRange: string[] = [];
    for (const v of data.column(name)) {
      if (isNullish(v)) continue;
      const n = typeof v === "number" ? v : Number(v);
      if (!Number.isFinite(n)) continue;
      if (n < lo || n > hi) {
        outOfRange.push(String(v));
      }
    }
    if (outOfRange.length > 0) {
      const samples = outOfRange.slice(0, 5);
      findings.push(
        makeFinding({
          severity: Severity.ERROR,
          column: name,
          check: "range",
          message: `${outOfRange.length} values outside range [${lo}, ${hi}]`,
          affectedRows: outOfRange.length,
          sampleValues: samples,
        }),
      );
    }
  }

  return findings;
}
