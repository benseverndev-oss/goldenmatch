/**
 * Age vs DOB cross-validation profiler.
 * Port of goldencheck/relations/age_validation.py.
 */

import type { TabularData } from "../data.js";
import { isNullish } from "../data.js";
import { type Finding, Severity, makeFinding } from "../types.js";
import type { RelationProfiler } from "../profilers/base.js";

const AGE_EXCLUSIONS = ["stage", "page", "usage", "mileage", "dosage", "voltage"];

function isAgeColumn(name: string): boolean {
  const lower = name.toLowerCase();
  if (!lower.includes("age")) return false;
  return !AGE_EXCLUSIONS.some((exc) => lower.includes(exc));
}

function isDobColumn(name: string): boolean {
  const lower = name.toLowerCase();
  return ["birth", "dob", "born"].some((kw) => lower.includes(kw));
}

function tryParseDate(v: unknown): Date | null {
  if (isNullish(v)) return null;
  const d = new Date(String(v));
  if (isNaN(d.getTime())) return null;
  return d;
}

export class AgeValidationProfiler implements RelationProfiler {
  profile(data: TabularData): Finding[] {
    const findings: Finding[] = [];

    const ageCols = data.columns.filter(isAgeColumn);
    const dobCols = data.columns.filter(isDobColumn);

    if (ageCols.length === 0 || dobCols.length === 0) return findings;

    const today = new Date();
    const referenceDate = today;

    for (const ageCol of ageCols) {
      if (!data.isNumeric(ageCol) && data.dtype(ageCol) !== "string") continue;

      for (const dobCol of dobCols) {
        const ageVals = data.column(ageCol);
        const dobVals = data.column(dobCol);
        let mismatches = 0;
        const sampleAges: string[] = [];

        for (let i = 0; i < ageVals.length; i++) {
          const ageVal = ageVals[i];
          const dobVal = dobVals[i];
          if (isNullish(ageVal) || isNullish(dobVal)) continue;

          const actualAge = Number(ageVal);
          if (!Number.isFinite(actualAge)) continue;

          const dob = tryParseDate(dobVal);
          if (!dob) continue;

          const diffMs = referenceDate.getTime() - dob.getTime();
          const expectedAge = diffMs / (365.25 * 24 * 60 * 60 * 1000);
          const diff = Math.abs(actualAge - expectedAge);

          if (diff > 2.0) {
            mismatches++;
            if (sampleAges.length < 5) {
              sampleAges.push(String(ageVal));
            }
          }
        }

        if (mismatches > 0) {
          findings.push(
            makeFinding({
              severity: Severity.ERROR,
              column: ageCol,
              check: "cross_column",
              message: `${mismatches} row(s) where ${ageCol} doesn't match calculated age from ${dobCol} — values mismatch by more than 2 years`,
              affectedRows: mismatches,
              sampleValues: sampleAges,
              suggestion: `Verify ${ageCol} values against ${dobCol}`,
              confidence: 0.9,
            }),
          );
        }
      }
    }

    return findings;
  }
}
