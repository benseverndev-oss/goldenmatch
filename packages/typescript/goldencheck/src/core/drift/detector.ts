/**
 * Drift detector — compare current data against a saved BaselineProfile.
 * TypeScript port of goldencheck/drift/detector.py.
 * Edge-safe: uses only stats.ts and baseline utilities, no external dependencies.
 *
 * Implements 13 drift checks:
 *  1. distribution_drift (KS-test)
 *  2. entropy_drift (numeric)
 *  3. entropy_drift (categorical)
 *  4. bound_violation
 *  5. benford_drift
 *  6. fd_violation
 *  7. key_uniqueness_loss
 *  8. temporal_order_drift
 *  9. pattern_drift
 * 10. new_pattern
 * 11. correlation_break
 * 12. new_correlation
 * 13. type_drift
 */

import type { TabularData } from "../data.js";
import { isNullish } from "../data.js";
import type { Finding } from "../types.js";
import { Severity, makeFinding } from "../types.js";
import type {
  BaselineProfile,
  StatProfile,
  CorrelationEntry,
} from "../baseline/models.js";
import {
  ksTwoSample,
  entropy as shannonEntropy,
  chiSquaredTest,
  benfordExpected,
  pearson,
  cramersV,
} from "../stats.js";
import { induceColumnGrammars } from "../baseline/patterns.js";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const SOURCE = "baseline_drift";

// KS-test thresholds
const KS_ERROR_PVALUE = 0.01;
const KS_WARN_PVALUE = 0.05;

// Entropy drift
const ENTROPY_DELTA_WARN = 0.5;

// Bound violation
const BOUND_VIOLATION_RATE = 0.05; // 5%

// FD violation
const FD_VIOLATION_RATE = 0.05;
const FD_VIOLATION_MULTIPLIER = 2.0;

// Temporal order
const TEMPORAL_VIOLATION_RATE = 0.05;
const TEMPORAL_VIOLATION_MULTIPLIER = 2.0;

// Pattern drift
const PATTERN_COVERAGE_DROP = 0.05; // 5pp
const PATTERN_NEW_COVERAGE = 0.05;

// Correlation
const CORR_STRONG_THRESHOLD = 0.7;
const CORR_DROP_THRESHOLD = 0.1;

// Minimum rows for checks
const MIN_ROWS = 30;

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Compare data against a baseline and return all drift findings.
 * All findings have source="baseline_drift".
 */
export function runDriftChecks(data: TabularData, baseline: BaselineProfile): Finding[] {
  const findings: Finding[] = [];
  findings.push(...checkStatistical(data, baseline));
  findings.push(...checkConstraints(data, baseline));
  findings.push(...checkPatterns(data, baseline));
  findings.push(...checkCorrelations(data, baseline));
  findings.push(...checkSemantic(data, baseline));
  return findings;
}

// ---------------------------------------------------------------------------
// Helper: make a drift finding
// ---------------------------------------------------------------------------

function drift(
  overrides: Partial<Finding> & Pick<Finding, "severity" | "column" | "check" | "message">,
): Finding {
  return makeFinding({ ...overrides, source: SOURCE });
}

// ---------------------------------------------------------------------------
// Statistical checks
// ---------------------------------------------------------------------------

function checkStatistical(data: TabularData, baseline: BaselineProfile): Finding[] {
  const findings: Finding[] = [];

  for (const [col, sp] of Object.entries(baseline.stats)) {
    if (!data.columns.includes(col)) continue;

    const dt = data.dtype(col);
    const isNumeric = dt === "integer" || dt === "float";

    if (isNumeric) {
      const values = data.numericValues(col);
      if (values.length < MIN_ROWS) continue;
      const sorted = [...values].sort((a, b) => a - b);

      findings.push(...checkDistributionDrift(col, sorted, sp));
      findings.push(...checkEntropyDriftNumeric(col, values, sp));
      findings.push(...checkBoundViolation(col, values, sp));
      findings.push(...checkBenfordDrift(col, values, sp));
    } else {
      const values = data.stringValues(col);
      if (values.length < MIN_ROWS) continue;
      findings.push(...checkEntropyDriftCategorical(col, values, sp));
    }
  }

  return findings;
}

// --- 1. Distribution drift (KS-test) ---

function checkDistributionDrift(
  col: string,
  sorted: number[],
  sp: StatProfile,
): Finding[] {
  if (sp.distribution === null || Object.keys(sp.params).length === 0) return [];

  const n = sorted.length;
  let synthetic: number[];

  switch (sp.distribution) {
    case "normal": {
      const loc = sp.params.loc ?? 0;
      const scale = sp.params.scale ?? 1;
      if (scale <= 0) return [];
      synthetic = generateNormalSorted(n, loc, scale);
      break;
    }
    case "log_normal": {
      const s = sp.params.s ?? 1;
      const scale = sp.params.scale ?? 1;
      if (s <= 0 || scale <= 0) return [];
      const logMean = Math.log(scale);
      const logStd = s;
      synthetic = generateLogNormalSorted(n, logMean, logStd);
      break;
    }
    case "exponential": {
      const loc = sp.params.loc ?? 0;
      const scale = sp.params.scale ?? 1;
      if (scale <= 0) return [];
      synthetic = generateExponentialSorted(n, loc, scale);
      break;
    }
    case "uniform": {
      const loc = sp.params.loc ?? 0;
      const scale = sp.params.scale ?? 1;
      if (scale <= 0) return [];
      synthetic = generateUniformSorted(n, loc, scale);
      break;
    }
    default:
      return [];
  }

  const ks = ksTwoSample(sorted, synthetic);
  const pvalue = ks.pValue;

  if (pvalue >= KS_WARN_PVALUE) return [];

  const severity = pvalue < KS_ERROR_PVALUE ? Severity.ERROR : Severity.WARNING;
  const prefix =
    pvalue < KS_ERROR_PVALUE ? "Distribution drift" : "Possible distribution drift";

  return [drift({
    severity,
    column: col,
    check: "distribution_drift",
    message:
      `${prefix} detected on '${col}': KS-test p=${pvalue.toFixed(4)} ` +
      `(baseline dist='${sp.distribution}'). Data no longer fits baseline distribution.`,
    confidence: 0.9,
    metadata: {
      technique: "statistical",
      drift_type: "distribution_drift",
      ks_pvalue: pvalue,
      baseline_distribution: sp.distribution,
    },
  })];
}

// --- 2. Entropy drift (numeric) ---

function checkEntropyDriftNumeric(
  col: string,
  values: number[],
  sp: StatProfile,
): Finding[] {
  if (sp.entropy === null) return [];

  const currentEntropy = histogramEntropy(values);
  const baselineEntropy = sp.entropy;
  const delta = Math.abs(currentEntropy - baselineEntropy);

  if (delta <= ENTROPY_DELTA_WARN) return [];

  return [drift({
    severity: Severity.WARNING,
    column: col,
    check: "entropy_drift",
    message:
      `Entropy drift on '${col}': baseline=${baselineEntropy.toFixed(3)}, ` +
      `current=${currentEntropy.toFixed(3)}, delta=${delta.toFixed(3)}.`,
    confidence: 0.8,
    metadata: {
      technique: "statistical",
      drift_type: "entropy_drift",
      baseline_entropy: baselineEntropy,
      current_entropy: currentEntropy,
      delta,
    },
  })];
}

// --- 3. Entropy drift (categorical) ---

function checkEntropyDriftCategorical(
  col: string,
  values: string[],
  sp: StatProfile,
): Finding[] {
  if (sp.entropy === null) return [];

  const counts = new Map<string, number>();
  for (const v of values) {
    counts.set(v, (counts.get(v) ?? 0) + 1);
  }
  const currentEntropy = shannonEntropy(counts);
  const baselineEntropy = sp.entropy;
  const delta = Math.abs(currentEntropy - baselineEntropy);

  if (delta <= ENTROPY_DELTA_WARN) return [];

  return [drift({
    severity: Severity.WARNING,
    column: col,
    check: "entropy_drift",
    message:
      `Entropy drift on '${col}': baseline=${baselineEntropy.toFixed(3)}, ` +
      `current=${currentEntropy.toFixed(3)}, delta=${delta.toFixed(3)}.`,
    confidence: 0.8,
    metadata: {
      technique: "statistical",
      drift_type: "entropy_drift",
      baseline_entropy: baselineEntropy,
      current_entropy: currentEntropy,
      delta,
    },
  })];
}

// --- 4. Bound violation ---

function checkBoundViolation(
  col: string,
  values: number[],
  sp: StatProfile,
): Finding[] {
  if (sp.bounds === null) return [];
  const { p01, p99 } = sp.bounds;

  const n = values.length;
  if (n === 0) return [];

  let violations = 0;
  for (const v of values) {
    if (v < p01 || v > p99) violations++;
  }

  const rate = violations / n;
  if (rate <= BOUND_VIOLATION_RATE) return [];

  return [drift({
    severity: Severity.ERROR,
    column: col,
    check: "bound_violation",
    message:
      `Bound violation on '${col}': ${violations}/${n} values ` +
      `(${(rate * 100).toFixed(1)}%) outside baseline ` +
      `p01=${p01.toPrecision(4)} / p99=${p99.toPrecision(4)}.`,
    affectedRows: violations,
    confidence: 0.95,
    metadata: {
      technique: "statistical",
      drift_type: "bound_violation",
      violation_rate: rate,
      p01,
      p99,
    },
  })];
}

// --- 5. Benford drift ---

function checkBenfordDrift(
  col: string,
  values: number[],
  sp: StatProfile,
): Finding[] {
  if (sp.benford === null) return [];

  const baselinePvalue = sp.benford.chi2_pvalue;
  const baselineConformed = baselinePvalue >= 0.05;

  const positives = values.filter((v) => v > 0 && Number.isFinite(v));
  if (positives.length < MIN_ROWS) return [];

  // Check 2+ orders of magnitude
  let minPos = positives[0]!;
  let maxPos = positives[0]!;
  for (let i = 1; i < positives.length; i++) {
    if (positives[i]! < minPos) minPos = positives[i]!;
    if (positives[i]! > maxPos) maxPos = positives[i]!;
  }
  if (minPos <= 0) return [];
  const span = Math.log10(maxPos) - Math.log10(minPos);
  if (span < 2.0) return [];

  const currentPvalue = computeBenfordPvalue(positives);
  if (currentPvalue === null) return [];
  const currentConformed = currentPvalue >= 0.05;

  if (baselineConformed === currentConformed) return [];

  const direction = baselineConformed
    ? "no longer conforms"
    : "now conforms (unexpected)";

  return [drift({
    severity: Severity.WARNING,
    column: col,
    check: "benford_drift",
    message:
      `Benford's law conformance flip on '${col}': baseline p=${baselinePvalue.toFixed(4)}, ` +
      `current p=${currentPvalue.toFixed(4)} — ${direction}.`,
    confidence: 0.75,
    metadata: {
      technique: "statistical",
      drift_type: "benford_drift",
      baseline_pvalue: baselinePvalue,
      current_pvalue: currentPvalue,
    },
  })];
}

// ---------------------------------------------------------------------------
// Constraint checks
// ---------------------------------------------------------------------------

function checkConstraints(data: TabularData, baseline: BaselineProfile): Finding[] {
  const findings: Finding[] = [];
  findings.push(...checkFdViolations(data, baseline));
  findings.push(...checkKeyUniqueness(data, baseline));
  findings.push(...checkTemporalOrderDrift(data, baseline));
  return findings;
}

// --- 6. FD violations ---

function checkFdViolations(data: TabularData, baseline: BaselineProfile): Finding[] {
  const findings: Finding[] = [];
  const nRows = data.rowCount;
  if (nRows === 0) return [];

  for (const fd of baseline.functionalDeps) {
    const det = fd.determinant;
    const dep = fd.dependent;

    if (!data.columns.includes(det) || !data.columns.includes(dep)) continue;

    // Group by determinant, check consistency of dependent
    const groups = new Map<string, Map<string, number>>();
    for (const row of data.rows) {
      const detVal = String(row[det] ?? "__null__");
      const depVal = String(row[dep] ?? "__null__");

      let depMap = groups.get(detVal);
      if (!depMap) {
        depMap = new Map<string, number>();
        groups.set(detVal, depMap);
      }
      depMap.set(depVal, (depMap.get(depVal) ?? 0) + 1);
    }

    let consistentCount = 0;
    for (const depMap of groups.values()) {
      let maxCount = 0;
      for (const count of depMap.values()) {
        if (count > maxCount) maxCount = count;
      }
      consistentCount += maxCount;
    }

    const currentConfidence = consistentCount / nRows;
    const currentViolationRate = 1.0 - currentConfidence;
    const baselineViolationRate = 1.0 - fd.confidence;

    const triggered =
      currentViolationRate > FD_VIOLATION_RATE ||
      (baselineViolationRate > 0 &&
        currentViolationRate > FD_VIOLATION_MULTIPLIER * baselineViolationRate);

    if (!triggered) continue;

    const affected = nRows - consistentCount;
    findings.push(drift({
      severity: Severity.ERROR,
      column: det,
      check: "fd_violation",
      message:
        `Functional dependency [${det}] -> [${dep}] violated: ` +
        `violation rate ${(currentViolationRate * 100).toFixed(1)}% ` +
        `(baseline ${(baselineViolationRate * 100).toFixed(1)}%).`,
      affectedRows: affected,
      confidence: 0.9,
      metadata: {
        technique: "constraints",
        drift_type: "fd_violation",
        determinant: det,
        dependent: dep,
        baseline_violation_rate: baselineViolationRate,
        current_violation_rate: currentViolationRate,
      },
    }));
  }

  return findings;
}

// --- 7. Key uniqueness loss ---

function checkKeyUniqueness(data: TabularData, baseline: BaselineProfile): Finding[] {
  const findings: Finding[] = [];
  const nRows = data.rowCount;
  if (nRows === 0) return [];

  for (const keyCol of baseline.candidateKeys) {
    if (!data.columns.includes(keyCol)) continue;

    const nullCount = data.nullCount(keyCol);
    const nUnique = data.nUnique(keyCol);

    if (nUnique === nRows && nullCount === 0) continue; // Still a valid key

    const duplicates = nRows - nUnique;
    findings.push(drift({
      severity: Severity.ERROR,
      column: keyCol,
      check: "key_uniqueness_loss",
      message:
        `Candidate key [${keyCol}] has lost uniqueness: ` +
        `${duplicates} duplicate(s) found in ${nRows} rows.`,
      affectedRows: duplicates,
      confidence: 0.95,
      metadata: {
        technique: "constraints",
        drift_type: "key_uniqueness_loss",
        key_column: keyCol,
        duplicate_count: duplicates,
      },
    }));
  }

  return findings;
}

// --- 8. Temporal order drift ---

function checkTemporalOrderDrift(data: TabularData, baseline: BaselineProfile): Finding[] {
  const findings: Finding[] = [];
  if (data.rowCount === 0) return [];

  for (const to of baseline.temporalOrders) {
    const colBefore = to.startCol;
    const colAfter = to.endCol;

    if (!data.columns.includes(colBefore) || !data.columns.includes(colAfter)) continue;

    let validPairs = 0;
    let violations = 0;

    for (const row of data.rows) {
      const aVal = row[colBefore];
      const bVal = row[colAfter];
      if (isNullish(aVal) || isNullish(bVal)) continue;

      const aDate = new Date(String(aVal));
      const bDate = new Date(String(bVal));
      if (isNaN(aDate.getTime()) || isNaN(bDate.getTime())) continue;

      validPairs++;
      if (aDate > bDate) violations++;
    }

    if (validPairs === 0) continue;

    const currentViolationRate = violations / validPairs;
    const baselineViolationRate = to.violationRate;

    const triggered =
      currentViolationRate > TEMPORAL_VIOLATION_RATE ||
      (baselineViolationRate > 0 &&
        currentViolationRate > TEMPORAL_VIOLATION_MULTIPLIER * baselineViolationRate);

    if (!triggered) continue;

    findings.push(drift({
      severity: Severity.WARNING,
      column: colBefore,
      check: "temporal_order_drift",
      message:
        `Temporal order drift: '${colBefore}' should be before '${colAfter}', ` +
        `but violation rate is ${(currentViolationRate * 100).toFixed(1)}% ` +
        `(baseline ${(baselineViolationRate * 100).toFixed(1)}%).`,
      affectedRows: violations,
      confidence: 0.85,
      metadata: {
        technique: "constraints",
        drift_type: "temporal_order_drift",
        before: colBefore,
        after: colAfter,
        baseline_violation_rate: baselineViolationRate,
        current_violation_rate: currentViolationRate,
      },
    }));
  }

  return findings;
}

// ---------------------------------------------------------------------------
// Pattern checks
// ---------------------------------------------------------------------------

// --- 9. Pattern drift + 10. New pattern ---

function checkPatterns(data: TabularData, baseline: BaselineProfile): Finding[] {
  const findings: Finding[] = [];

  for (const [col, baseGrammars] of Object.entries(baseline.patterns)) {
    if (!data.columns.includes(col)) continue;
    if (!data.isString(col)) continue;

    const values = data.stringValues(col);
    if (values.length < MIN_ROWS) continue;

    const currentGrammars = induceColumnGrammars(col, values);
    const currentPatternMap = new Map<string, number>();
    for (const g of currentGrammars) {
      currentPatternMap.set(g.regex, g.coverage);
    }

    if (baseGrammars.length === 0) continue;
    const baseGrammar = baseGrammars[0]!;
    const baselinePattern = baseGrammar.regex;
    const baselineCoverage = baseGrammar.coverage;

    // 9. pattern_drift: check if baseline pattern's coverage has dropped
    const currentCoverage = currentPatternMap.get(baselinePattern) ?? 0;
    const coverageDrop = baselineCoverage - currentCoverage;
    if (coverageDrop > PATTERN_COVERAGE_DROP) {
      findings.push(drift({
        severity: Severity.WARNING,
        column: col,
        check: "pattern_drift",
        message:
          `Pattern coverage drop on '${col}': baseline pattern ` +
          `'${baselinePattern}' covered ${(baselineCoverage * 100).toFixed(1)}%, ` +
          `now ${(currentCoverage * 100).toFixed(1)}% (drop=${(coverageDrop * 100).toFixed(1)}%).`,
        confidence: 0.8,
        metadata: {
          technique: "patterns",
          drift_type: "pattern_drift",
          pattern: baselinePattern,
          baseline_coverage: baselineCoverage,
          current_coverage: currentCoverage,
          drop: coverageDrop,
        },
      }));
    }

    // 10. new_pattern: INFO for new format variants with > 5% coverage not in baseline
    const baselinePatterns = new Set(baseGrammars.map((g) => g.regex));
    for (const g of currentGrammars) {
      if (!baselinePatterns.has(g.regex) && g.coverage > PATTERN_NEW_COVERAGE) {
        findings.push(drift({
          severity: Severity.INFO,
          column: col,
          check: "new_pattern",
          message:
            `New format variant on '${col}': pattern '${g.regex}' covers ` +
            `${(g.coverage * 100).toFixed(1)}% of current data (not in baseline).`,
          confidence: 0.7,
          metadata: {
            technique: "patterns",
            drift_type: "new_pattern",
            pattern: g.regex,
            coverage: g.coverage,
          },
        }));
      }
    }
  }

  return findings;
}

// ---------------------------------------------------------------------------
// Correlation checks
// ---------------------------------------------------------------------------

// --- 11. Correlation break + 12. New correlation ---

function checkCorrelations(data: TabularData, baseline: BaselineProfile): Finding[] {
  const findings: Finding[] = [];

  // Build lookup of baseline correlations
  const baselineLookup = new Map<string, CorrelationEntry>();
  for (const entry of baseline.correlations) {
    const key = `${entry.col1}\0${entry.col2}`;
    baselineLookup.set(key, entry);
  }

  // 11. Check existing baseline correlations for breaks
  for (const [key, baseEntry] of baselineLookup) {
    const [colA, colB] = key.split("\0") as [string, string];
    if (!data.columns.includes(colA) || !data.columns.includes(colB)) continue;

    const currentValue = computeCorrelation(data, colA, colB, baseEntry.method);
    if (currentValue === null) continue;

    // correlation_break: WARNING if strong correlation drops > 0.1
    if (
      baseEntry.strength === "strong" &&
      baseEntry.value - currentValue > CORR_DROP_THRESHOLD
    ) {
      const drop = baseEntry.value - currentValue;
      findings.push(drift({
        severity: Severity.WARNING,
        column: colA,
        check: "correlation_break",
        message:
          `Correlation break between '${colA}' and '${colB}': ` +
          `baseline=${baseEntry.value.toFixed(3)}, current=${currentValue.toFixed(3)} ` +
          `(drop=${drop.toFixed(3)}, measure=${baseEntry.method}).`,
        confidence: 0.8,
        metadata: {
          technique: "correlations",
          drift_type: "correlation_break",
          columns: [colA, colB],
          measure: baseEntry.method,
          baseline_value: baseEntry.value,
          current_value: currentValue,
          drop,
        },
      }));
    }
  }

  // 12. new_correlation: INFO for newly emerged strong correlations not in baseline
  const numericCols = data.columns.filter((c) => {
    const dt = data.dtype(c);
    return dt === "integer" || dt === "float";
  });

  let checked = 0;
  for (let i = 0; i < numericCols.length && checked < 200; i++) {
    for (let j = i + 1; j < numericCols.length && checked < 200; j++) {
      const colA = numericCols[i]!;
      const colB = numericCols[j]!;
      const sortedKey =
        colA < colB ? `${colA}\0${colB}` : `${colB}\0${colA}`;

      if (baselineLookup.has(sortedKey)) continue;
      checked++;

      const currentValue = computeCorrelation(data, colA, colB, "pearson");
      if (currentValue !== null && Math.abs(currentValue) >= CORR_STRONG_THRESHOLD) {
        findings.push(drift({
          severity: Severity.INFO,
          column: colA,
          check: "new_correlation",
          message:
            `New strong correlation emerged between '${colA}' and '${colB}': ` +
            `r=${currentValue.toFixed(3)} (not present in baseline).`,
          confidence: 0.7,
          metadata: {
            technique: "correlations",
            drift_type: "new_correlation",
            columns: [colA, colB],
            measure: "pearson",
            current_value: currentValue,
          },
        }));
      }
    }
  }

  return findings;
}

function computeCorrelation(
  data: TabularData,
  colA: string,
  colB: string,
  method: "pearson" | "cramers_v",
): number | null {
  if (method === "pearson") {
    const aValues: number[] = [];
    const bValues: number[] = [];
    for (const row of data.rows) {
      const a = row[colA];
      const b = row[colB];
      if (a === null || a === undefined || b === null || b === undefined) continue;
      const an = Number(a);
      const bn = Number(b);
      if (!Number.isFinite(an) || !Number.isFinite(bn)) continue;
      aValues.push(an);
      bValues.push(bn);
    }
    if (aValues.length < MIN_ROWS) return null;
    return pearson(aValues, bValues);
  }

  if (method === "cramers_v") {
    const contingency = new Map<string, Map<string, number>>();
    let validCount = 0;
    for (const row of data.rows) {
      const a = row[colA];
      const b = row[colB];
      if (a === null || a === undefined || b === null || b === undefined) continue;
      const aStr = String(a);
      const bStr = String(b);
      let bMap = contingency.get(aStr);
      if (!bMap) {
        bMap = new Map<string, number>();
        contingency.set(aStr, bMap);
      }
      bMap.set(bStr, (bMap.get(bStr) ?? 0) + 1);
      validCount++;
    }
    if (validCount < MIN_ROWS) return null;
    return cramersV(contingency);
  }

  return null;
}

// ---------------------------------------------------------------------------
// Semantic checks
// ---------------------------------------------------------------------------

// --- 13. Type drift ---

function checkSemantic(data: TabularData, baseline: BaselineProfile): Finding[] {
  if (!baseline.semanticTypes || Object.keys(baseline.semanticTypes).length === 0) {
    return [];
  }

  const findings: Finding[] = [];

  for (const [col, baselineType] of Object.entries(baseline.semanticTypes)) {
    if (!data.columns.includes(col)) continue;

    const currentType = data.dtype(col);
    // Map dtype to a comparable semantic type string
    const currentSemanticType = dtypeToSemanticType(currentType);

    if (currentSemanticType === null || currentSemanticType === baselineType) continue;

    findings.push(drift({
      severity: Severity.WARNING,
      column: col,
      check: "type_drift",
      message:
        `Semantic type drift on '${col}': baseline type was '${baselineType}', ` +
        `now inferred as '${currentSemanticType}'.`,
      confidence: 0.75,
      metadata: {
        technique: "semantic",
        drift_type: "type_drift",
        baseline_type: baselineType,
        current_type: currentSemanticType,
      },
    }));
  }

  return findings;
}

function dtypeToSemanticType(dtype: string): string | null {
  switch (dtype) {
    case "integer":
    case "float":
      return "numeric";
    case "string":
      return "string";
    case "boolean":
      return "boolean";
    case "date":
      return "date";
    case "datetime":
      return "datetime";
    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// Helpers — synthetic sorted sample generators (inverse CDF / quantile)
// ---------------------------------------------------------------------------

function generateNormalSorted(n: number, loc: number, scale: number): number[] {
  const result: number[] = [];
  for (let i = 0; i < n; i++) {
    const p = (i + 0.5) / n;
    result.push(loc + scale * normalQuantile(p));
  }
  return result;
}

function generateLogNormalSorted(n: number, logMean: number, logStd: number): number[] {
  const result: number[] = [];
  for (let i = 0; i < n; i++) {
    const p = (i + 0.5) / n;
    result.push(Math.exp(logMean + logStd * normalQuantile(p)));
  }
  return result;
}

function generateExponentialSorted(n: number, loc: number, scale: number): number[] {
  const result: number[] = [];
  for (let i = 0; i < n; i++) {
    const p = (i + 0.5) / n;
    result.push(loc - scale * Math.log(1 - p));
  }
  return result;
}

function generateUniformSorted(n: number, loc: number, scale: number): number[] {
  const result: number[] = [];
  for (let i = 0; i < n; i++) {
    const p = (i + 0.5) / n;
    result.push(loc + scale * p);
  }
  return result;
}

/** Approximate inverse normal CDF (Acklam's rational approximation). */
function normalQuantile(p: number): number {
  if (p <= 0) return -Infinity;
  if (p >= 1) return Infinity;
  if (p === 0.5) return 0;

  const a1 = -3.969683028665376e+01;
  const a2 = 2.209460984245205e+02;
  const a3 = -2.759285104469687e+02;
  const a4 = 1.383577518672690e+02;
  const a5 = -3.066479806614716e+01;
  const a6 = 2.506628277459239e+00;

  const b1 = -5.447609879822406e+01;
  const b2 = 1.615858368580409e+02;
  const b3 = -1.556989798598866e+02;
  const b4 = 6.680131188771972e+01;
  const b5 = -1.328068155288572e+01;

  const c1 = -7.784894002430293e-03;
  const c2 = -3.223964580411365e-01;
  const c3 = -2.400758277161838e+00;
  const c4 = -2.549732539343734e+00;
  const c5 = 4.374664141464968e+00;
  const c6 = 2.938163982698783e+00;

  const d1 = 7.784695709041462e-03;
  const d2 = 3.224671290700398e-01;
  const d3 = 2.445134137142996e+00;
  const d4 = 3.754408661907416e+00;

  const pLow = 0.02425;
  const pHigh = 1 - pLow;

  if (p < pLow) {
    const q = Math.sqrt(-2 * Math.log(p));
    return (((((c1 * q + c2) * q + c3) * q + c4) * q + c5) * q + c6) /
           ((((d1 * q + d2) * q + d3) * q + d4) * q + 1);
  } else if (p <= pHigh) {
    const q = p - 0.5;
    const r = q * q;
    return (((((a1 * r + a2) * r + a3) * r + a4) * r + a5) * r + a6) * q /
           (((((b1 * r + b2) * r + b3) * r + b4) * r + b5) * r + 1);
  } else {
    const q = Math.sqrt(-2 * Math.log(1 - p));
    return -(((((c1 * q + c2) * q + c3) * q + c4) * q + c5) * q + c6) /
            ((((d1 * q + d2) * q + d3) * q + d4) * q + 1);
  }
}

/** Compute approximate Shannon entropy using a histogram (Sturges' rule bins). */
function histogramEntropy(values: number[]): number {
  const n = values.length;
  if (n === 0) return 0;

  const nBins = Math.max(10, Math.min(100, Math.ceil(Math.log2(n) + 1)));

  let min = Infinity;
  let max = -Infinity;
  for (const v of values) {
    if (v < min) min = v;
    if (v > max) max = v;
  }
  if (min === max) return 0;

  const binWidth = (max - min) / nBins;
  const counts = new Array<number>(nBins).fill(0);

  for (const v of values) {
    let bin = Math.floor((v - min) / binWidth);
    if (bin >= nBins) bin = nBins - 1;
    counts[bin]!++;
  }

  let ent = 0;
  for (const c of counts) {
    if (c > 0) {
      const p = c / n;
      ent -= p * Math.log2(p);
    }
  }
  return ent;
}

/** Compute Benford's law chi-squared p-value. Returns null on failure. */
function computeBenfordPvalue(values: number[]): number | null {
  const leadingDigits: number[] = [];
  for (const v of values) {
    if (v <= 0 || !Number.isFinite(v)) continue;
    const exp = Math.floor(Math.log10(v));
    const normalised = v / 10 ** exp;
    const d = Math.floor(normalised);
    if (d >= 1 && d <= 9) leadingDigits.push(d);
  }

  if (leadingDigits.length === 0) return null;

  const total = leadingDigits.length;
  const digitCounts = new Array<number>(9).fill(0);
  for (const d of leadingDigits) {
    digitCounts[d - 1]!++;
  }

  const expectedProps = benfordExpected();
  const expectedCounts = expectedProps.map((p) => p * total);

  const { pValue } = chiSquaredTest(digitCounts, expectedCounts);
  return pValue;
}
