/**
 * Scanner — orchestrates all profilers and collects findings.
 * Port of goldencheck/engine/scanner.py.
 */

import { TabularData, isNullish } from "../data.js";
import {
  type Finding,
  type ColumnProfile,
  type DatasetProfile,
  type ScanResult,
  Severity,
  makeFinding,
  makeColumnProfile,
} from "../types.js";
import { COLUMN_PROFILERS } from "../profilers/index.js";
import { RELATION_PROFILERS } from "../relations/index.js";
import { classifyColumns, loadTypeDefs } from "../semantic/classifier.js";
import { applySuppression } from "../semantic/suppression.js";
import { applyCorroborationBoost } from "./confidence.js";
import { maybeSample } from "./sampler.js";
import { generalize } from "../profilers/pattern-consistency.js";

export interface ScanOptions {
  /** Max rows to sample. Default 100,000. */
  sampleSize?: number | undefined;
  /** Domain pack name. */
  domain?: string | null | undefined;
  /** Return the sampled data along with results. */
  returnSample?: boolean | undefined;
}

export interface ScanResultWithSample extends ScanResult {
  sample: TabularData;
}

/**
 * Scan tabular data for quality issues.
 * This is the edge-safe core scan function — no file I/O.
 */
export function scanData(
  data: TabularData,
  options?: ScanOptions,
): ScanResult | ScanResultWithSample {
  const sampleSize = options?.sampleSize ?? 100_000;
  const domain = options?.domain ?? null;

  const rowCount = data.rowCount;
  const sample = maybeSample(data, sampleSize);

  let allFindings: Finding[] = [];
  const columnProfiles: ColumnProfile[] = [];
  const profilerContext: Record<string, unknown> = {};

  // Build profiles from FULL data, run profilers on SAMPLE
  for (const colName of data.columns) {
    const col = data.column(colName);
    const nonNull = col.filter((v) => !isNullish(v));
    const uniqueCount = data.nUnique(colName);

    columnProfiles.push(
      makeColumnProfile({
        name: colName,
        inferredType: data.dtype(colName),
        nullCount: data.nullCount(colName),
        nullPct: rowCount > 0 ? data.nullCount(colName) / rowCount : 0,
        uniqueCount,
        uniquePct: nonNull.length > 0 ? uniqueCount / nonNull.length : 0,
        rowCount,
      }),
    );

    // Run column profilers on sample
    for (const profiler of COLUMN_PROFILERS) {
      try {
        const findings = profiler.profile(sample, colName, profilerContext);
        allFindings.push(...findings);
      } catch (e) {
        // Profiler exceptions are caught and logged, never re-raised
        console.warn(`Profiler failed on column ${colName}:`, e);
      }
    }
  }

  // Run relation profilers on sample
  for (const profiler of RELATION_PROFILERS) {
    try {
      const findings = profiler.profile(sample);
      allFindings.push(...findings);
    } catch (e) {
      console.warn("Relation profiler failed:", e);
    }
  }

  // Classify columns
  const typeDefs = loadTypeDefs(domain);
  const columnTypes = classifyColumns(sample, typeDefs);

  // Apply suppression BEFORE corroboration boost
  allFindings = applySuppression(allFindings, columnTypes, typeDefs);

  // Post-classification checks
  allFindings = postClassificationChecks(sample, allFindings, columnTypes);

  // Apply corroboration boost
  allFindings = applyCorroborationBoost(allFindings);

  // Sort by severity descending
  allFindings.sort((a, b) => b.severity - a.severity);

  const profile: DatasetProfile = {
    filePath: "",
    rowCount,
    columnCount: data.columns.length,
    columns: columnProfiles,
  };

  if (options?.returnSample) {
    return { findings: allFindings, profile, sample };
  }
  return { findings: allFindings, profile };
}

/**
 * Post-classification checks — mirrors _post_classification_checks in Python.
 */
function postClassificationChecks(
  sample: TabularData,
  findings: Finding[],
  columnTypes: Record<string, { typeName: string | null }>,
): Finding[] {
  const newFindings = [...findings];

  // 1. Digits in person_name columns
  for (const [colName, classification] of Object.entries(columnTypes)) {
    if (classification.typeName !== "person_name") continue;
    if (!sample.columns.includes(colName)) continue;
    if (!sample.isString(colName)) continue;

    const nonNull = sample.stringValues(colName);
    if (nonNull.length === 0) continue;

    const digitRe = /\d/;
    const withDigits = nonNull.filter((v) => digitRe.test(v));
    const digitCount = withDigits.length;
    if (digitCount > 0) {
      const digitPct = digitCount / nonNull.length;
      if (digitPct > 0 && digitPct < 0.1) {
        newFindings.push(
          makeFinding({
            severity: Severity.WARNING,
            column: colName,
            check: "type_inference",
            message: `Column '${colName}' appears to be a person name but ${digitCount} row(s) (${(digitPct * 100).toFixed(1)}%) contain numeric characters — possible invalid values`,
            affectedRows: digitCount,
            sampleValues: withDigits.slice(0, 5),
            suggestion: "Check for data entry errors or encoding issues in name values",
            confidence: 0.85,
          }),
        );
      }
    }
  }

  // 2. Code-like format inconsistency for geo/identifier columns
  const existingPcCols = new Set(
    newFindings
      .filter((f) => f.check === "pattern_consistency" && (f.severity === Severity.WARNING || f.severity === Severity.ERROR))
      .map((f) => f.column),
  );

  for (const [colName, classification] of Object.entries(columnTypes)) {
    if (!classification.typeName || !["geo", "identifier"].includes(classification.typeName)) continue;
    if (existingPcCols.has(colName)) continue;
    if (!sample.columns.includes(colName)) continue;
    if (!sample.isString(colName)) continue;

    const nonNull = sample.stringValues(colName);
    if (nonNull.length === 0) continue;

    const patternCounts = new Map<string, number>();
    for (const v of nonNull) {
      const p = generalize(v);
      patternCounts.set(p, (patternCounts.get(p) ?? 0) + 1);
    }

    if (patternCounts.size < 2) continue;

    const sorted = [...patternCounts.entries()].sort((a, b) => b[1] - a[1]);
    const dominantPattern = sorted[0]![0];
    const digitRatio = countChar(dominantPattern, "D") / Math.max(dominantPattern.length, 1);
    if (digitRatio < 0.5) continue;

    for (let i = 1; i < sorted.length; i++) {
      const [minorityPattern, minorityCount] = sorted[i]!;
      if (Math.abs(dominantPattern.length - minorityPattern.length) > 1) {
        const minoritySamples = nonNull.filter((v) => generalize(v) === minorityPattern).slice(0, 5);
        newFindings.push(
          makeFinding({
            severity: Severity.WARNING,
            column: colName,
            check: "pattern_consistency",
            message: `Inconsistent pattern detected: '${minorityPattern}' appears in ${minorityCount} row(s) (${((minorityCount / nonNull.length) * 100).toFixed(1)}%) vs dominant pattern '${dominantPattern}'`,
            affectedRows: minorityCount,
            sampleValues: minoritySamples,
            suggestion: "Standardize values to a single format/pattern",
            confidence: 0.8,
            metadata: { dominant_pattern: dominantPattern, minority_pattern: minorityPattern },
          }),
        );
        break;
      }
    }
  }

  // 3. String length format check for identifier-like columns
  const ID_KEYWORDS = ["id", "number", "code", "auth", "key"];
  const ID_EXCLUDE = ["phone", "npi"];

  for (const colName of sample.columns) {
    const nameLower = colName.toLowerCase();
    if (!ID_KEYWORDS.some((kw) => nameLower.includes(kw))) continue;
    if (ID_EXCLUDE.some((exc) => nameLower.includes(exc))) continue;

    const dt = sample.dtype(colName);
    if (dt !== "string" && dt !== "integer" && dt !== "float") continue;

    const nonNull = sample.dropNulls(colName);
    if (nonNull.length === 0) continue;

    const lengths = nonNull.map((v) => String(v).length);
    const lengthCounts = new Map<number, number>();
    for (const len of lengths) {
      lengthCounts.set(len, (lengthCounts.get(len) ?? 0) + 1);
    }

    const sorted = [...lengthCounts.entries()].sort((a, b) => b[1] - a[1]);
    if (sorted.length === 0) continue;

    const dominantLength = sorted[0]![0];
    const dominantCount = sorted[0]![1];
    const dominantPct = dominantCount / nonNull.length;
    const outlierCount = nonNull.length - dominantCount;

    if (dominantPct > 0.9 && outlierCount > 0) {
      const sampleVals = nonNull
        .filter((v) => String(v).length !== dominantLength)
        .slice(0, 5)
        .map(String);

      newFindings.push(
        makeFinding({
          severity: Severity.WARNING,
          column: colName,
          check: "format_detection",
          message: `Inconsistent string length: ${Math.round(dominantPct * 100)}% of values are ${dominantLength} chars but ${outlierCount} row(s) have different lengths — possible invalid format`,
          affectedRows: outlierCount,
          sampleValues: sampleVals,
          suggestion: "Verify that all values conform to the expected length",
          confidence: 0.75,
        }),
      );
    }
  }

  return newFindings;
}

function countChar(s: string, ch: string): number {
  let count = 0;
  for (const c of s) if (c === ch) count++;
  return count;
}
