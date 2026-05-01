/**
 * Drift detection profiler — detects distribution drift between halves of data.
 * Port of goldencheck/profilers/drift_detection.py.
 */

import type { TabularData } from "../data.js";
import { isNullish } from "../data.js";
import { mean as statMean, std as statStd } from "../stats.js";
import { type Finding, Severity, makeFinding } from "../types.js";
import type { Profiler } from "./base.js";

const MIN_ROWS = 1000;
const DRIFT_STDDEV_THRESHOLD = 3.0;
const DRIFT_STDDEV_EXTREME = 5.0;
const CATEGORICAL_DRIFT_THRESHOLD = 0.20;
const CATEGORICAL_DRIFT_EXTREME = 0.50;

export class DriftDetectionProfiler implements Profiler {
  profile(data: TabularData, column: string): Finding[] {
    const findings: Finding[] = [];
    const total = data.rowCount;

    if (total < MIN_ROWS) return findings;

    const col = data.column(column);
    const mid = Math.floor(total / 2);
    const firstHalf = col.slice(0, mid).filter((v) => !isNullish(v));
    const secondHalf = col.slice(mid).filter((v) => !isNullish(v));

    if (firstHalf.length === 0 || secondHalf.length === 0) return findings;

    // Skip high-cardinality string columns
    const isNumeric = data.isNumeric(column);
    if (!isNumeric) {
      const nonNull = data.dropNulls(column);
      const uniquePct = data.nUnique(column) / nonNull.length;
      if (uniquePct > 0.90) return findings;
    }

    if (isNumeric) {
      const nums1 = firstHalf.map(Number).filter(Number.isFinite);
      const nums2 = secondHalf.map(Number).filter(Number.isFinite);

      const mean1 = statMean(nums1);
      const mean2 = statMean(nums2);
      const std1 = statStd(nums1);

      if (mean1 === null || mean2 === null || std1 === null || std1 === 0) return findings;

      const deviation = Math.abs(mean2 - mean1) / std1;
      if (deviation > DRIFT_STDDEV_THRESHOLD) {
        const severity = deviation > DRIFT_STDDEV_EXTREME ? Severity.WARNING : Severity.INFO;
        findings.push(
          makeFinding({
            severity,
            column,
            check: "drift_detection",
            message: `Distribution shift detected in '${column}': mean changed from ${mean1.toPrecision(4)} (first half) to ${mean2.toPrecision(4)} (second half), a shift of ${deviation.toFixed(1)} standard deviations — possible temporal drift`,
            affectedRows: secondHalf.length,
            suggestion: "Investigate whether the data order is temporal and whether the shift is expected",
            confidence: 0.6,
          }),
        );
      }
    } else {
      // Categorical drift
      const catsFirst = new Set(firstHalf.map(String));
      const catsSecond = new Set(secondHalf.map(String));
      const newCats = [...catsSecond].filter((c) => !catsFirst.has(c));

      if (newCats.length > 0) {
        const newCatPct = catsSecond.size > 0 ? newCats.length / catsSecond.size : 0;
        if (newCatPct > CATEGORICAL_DRIFT_THRESHOLD) {
          const sampleNew = newCats.sort().slice(0, 10);
          const newCatSet = new Set(newCats);
          const affected = secondHalf.filter((v) => newCatSet.has(String(v))).length;
          const severity = newCatPct > CATEGORICAL_DRIFT_EXTREME ? Severity.WARNING : Severity.INFO;

          findings.push(
            makeFinding({
              severity,
              column,
              check: "drift_detection",
              message: `Categorical drift detected in '${column}': ${newCats.length} new categor(y/ies) appear in the second half of the data that are absent from the first half: ${JSON.stringify(sampleNew)}`,
              affectedRows: affected,
              sampleValues: sampleNew.map(String),
              suggestion: "Verify whether new categories are expected or indicate schema/labelling drift",
              confidence: 0.6,
            }),
          );
        }
      }
    }

    return findings;
  }
}
