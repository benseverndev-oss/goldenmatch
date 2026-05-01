/**
 * Sequence gap detection profiler — detects gaps in sequential integer columns.
 * Port of goldencheck/profilers/sequence_detection.py.
 */

import type { TabularData } from "../data.js";
import { type Finding, Severity, makeFinding } from "../types.js";
import type { Profiler } from "./base.js";

const SEQUENTIAL_THRESHOLD = 0.9;

export class SequenceDetectionProfiler implements Profiler {
  profile(data: TabularData, column: string): Finding[] {
    const findings: Finding[] = [];

    const dt = data.dtype(column);
    if (dt !== "integer") return findings;

    const nums = data.numericValues(column).filter(Number.isInteger);
    if (nums.length < 2) return findings;

    // Compute consecutive differences
    const diffs: number[] = [];
    for (let i = 1; i < nums.length; i++) {
      diffs.push(nums[i]! - nums[i - 1]!);
    }

    if (diffs.length === 0) return findings;

    const unitDiffs = diffs.filter((d) => d === 1).length;
    const positiveDiffs = diffs.filter((d) => d > 0).length;
    const sequentialRatio = unitDiffs / diffs.length;
    const positiveRatio = positiveDiffs / diffs.length;

    // Check if sorted ascending
    const isSorted = nums.every((v, i) => i === 0 || v >= nums[i - 1]!);

    const isTightSequential = sequentialRatio >= SEQUENTIAL_THRESHOLD;
    const isSortedSequential = positiveRatio >= SEQUENTIAL_THRESHOLD && isSorted;

    if (!isTightSequential && !isSortedSequential) return findings;

    // Find gaps
    let colMin = nums[0]!;
    let colMax = nums[0]!;
    for (let i = 1; i < nums.length; i++) {
      if (nums[i]! < colMin) colMin = nums[i]!;
      if (nums[i]! > colMax) colMax = nums[i]!;
    }
    const expectedCount = colMax - colMin + 1;

    if (expectedCount <= nums.length) return findings;

    const present = new Set(nums);
    const gaps: number[] = [];
    for (let v = colMin; v <= colMax; v++) {
      if (!present.has(v)) gaps.push(v);
    }

    const sampleGaps = gaps.slice(0, 10);
    findings.push(
      makeFinding({
        severity: Severity.WARNING,
        column,
        check: "sequence_detection",
        message: `Sequence gap detected in column '${column}': ${gaps.length} missing value(s) in range [${colMin}, ${colMax}]. Gap positions (sample): ${JSON.stringify(sampleGaps)}`,
        affectedRows: gaps.length,
        sampleValues: sampleGaps.map(String),
        suggestion: "Investigate whether the missing sequence numbers indicate deleted or skipped records",
        confidence: 0.7,
      }),
    );

    return findings;
  }
}
