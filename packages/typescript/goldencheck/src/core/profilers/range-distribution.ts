/**
 * Range and distribution profiler — detects outliers and reports min/max.
 * Port of goldencheck/profilers/range_distribution.py.
 */

import type { TabularData } from "../data.js";
import { mean as statMean, std as statStd } from "../stats.js";
import { type Finding, Severity, makeFinding } from "../types.js";
import type { Profiler } from "./base.js";

export class RangeDistributionProfiler implements Profiler {
  profile(
    data: TabularData,
    column: string,
    context?: Record<string, unknown>,
  ): Finding[] {
    const findings: Finding[] = [];

    let isNumeric = data.isNumeric(column);

    // Chain: if type inference flagged as mostly numeric, treat as numeric
    if (
      !isNumeric &&
      context &&
      (context[column] as Record<string, unknown> | undefined)?.["mostly_numeric"]
    ) {
      isNumeric = true;
    }

    if (!isNumeric) return findings;

    const nums = data.numericValues(column);
    if (nums.length < 2) return findings;

    const m = statMean(nums)!;
    const s = statStd(nums);
    let colMin = nums[0]!;
    let colMax = nums[0]!;
    for (let i = 1; i < nums.length; i++) {
      if (nums[i]! < colMin) colMin = nums[i]!;
      if (nums[i]! > colMax) colMax = nums[i]!;
    }

    findings.push(
      makeFinding({
        severity: Severity.INFO,
        column,
        check: "range_distribution",
        message: `Range: min=${colMin}, max=${colMax}, mean=${m.toFixed(2)}`,
      }),
    );

    if (s !== null && s > 0) {
      const lower = m - 3 * s;
      const upper = m + 3 * s;
      const outliers = nums.filter((v) => v < lower || v > upper);
      const outlierCount = outliers.length;

      if (outlierCount > 0) {
        const sample = outliers.slice(0, 5).map(String);
        const maxDev = Math.max(
          Math.abs(colMax - m) / s,
          Math.abs(colMin - m) / s,
        );
        const confidence = maxDev > 5 ? 0.9 : 0.7;

        findings.push(
          makeFinding({
            severity: Severity.WARNING,
            column,
            check: "range_distribution",
            message: `${outlierCount} outlier(s) detected beyond 3 standard deviations`,
            affectedRows: outlierCount,
            sampleValues: sample,
            suggestion: "Investigate outlier values for data entry errors or anomalies",
            confidence,
          }),
        );
      }
    }

    return findings;
  }
}
