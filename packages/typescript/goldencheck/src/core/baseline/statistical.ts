/**
 * Statistical profiler — distributions, Benford's law, entropy, percentile bounds.
 * TypeScript port of goldencheck/baseline/statistical.py.
 * Edge-safe: uses only stats.ts utilities, no external dependencies.
 */

import type { TabularData } from "../data.js";
import type { StatProfile } from "./models.js";
import {
  mean,
  std,
  percentile,
  entropy as shannonEntropy,
  ksTwoSample,
  chiSquaredTest,
  benfordExpected,
  normalCdf,
} from "../stats.js";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Minimum non-null rows required to profile a column. */
const MIN_ROWS = 30;

/** Column-name keywords that make a column eligible for Benford's law. */
const BENFORD_KEYWORDS = new Set([
  "amount", "total", "revenue", "population", "count",
  "price", "salary", "income", "cost", "fee",
]);

/** Keywords that mark a column as an identifier/code — Benford skipped. */
const ID_KEYWORDS = ["_id", "id_", " id", "id ", "code", "key", "uuid", "guid", "hash", "ref"];

/** Keywords that mark a column as a percentage — Benford skipped. */
const PCT_KEYWORDS = ["pct", "percent", "ratio", "rate", "share", "proportion"];

/** Minimum KS-test p-value to accept a distribution fit. */
const KS_MIN_PVALUE = 0.01;

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Compute a StatProfile for each column in the data.
 * Columns with fewer than MIN_ROWS non-null values are omitted.
 */
export function profileStatistical(data: TabularData): Record<string, StatProfile> {
  const profiles: Record<string, StatProfile> = {};

  for (const col of data.columns) {
    const dt = data.dtype(col);
    const isNumeric = dt === "integer" || dt === "float";

    if (isNumeric) {
      const values = data.numericValues(col);
      if (values.length < MIN_ROWS) continue;
      profiles[col] = profileNumeric(col, values);
    } else {
      const values = data.stringValues(col);
      if (values.length < MIN_ROWS) continue;
      profiles[col] = profileCategorical(values);
    }
  }

  return profiles;
}

// ---------------------------------------------------------------------------
// Numeric profiling
// ---------------------------------------------------------------------------

function profileNumeric(col: string, values: number[]): StatProfile {
  const sorted = [...values].sort((a, b) => a - b);
  const distribution = fitDistribution(sorted);
  const ent = histogramEntropy(values);
  const bounds = numericBounds(sorted);
  const benford = maybeBenford(col, values);

  return {
    distribution: distribution?.name ?? null,
    params: distribution?.params ?? {},
    entropy: ent,
    benford,
    bounds,
  };
}

// ---------------------------------------------------------------------------
// Distribution fitting
// ---------------------------------------------------------------------------

interface DistFit {
  name: string;
  params: Record<string, number>;
}

/**
 * Fit candidate distributions (normal, log_normal, exponential, uniform)
 * using a KS two-sample test approach. Returns the best fit with p-value >= KS_MIN_PVALUE.
 *
 * Since we don't have scipy, we generate synthetic samples from the fitted
 * parameters and use the two-sample KS test from stats.ts.
 */
function fitDistribution(sorted: number[]): DistFit | null {
  const n = sorted.length;
  if (n < MIN_ROWS) return null;

  const m = mean(sorted)!;
  const s = std(sorted)!;
  const minVal = sorted[0]!;
  const maxVal = sorted[n - 1]!;

  interface Candidate {
    name: string;
    params: Record<string, number>;
    pValue: number;
  }

  const candidates: Candidate[] = [];

  // --- Normal ---
  if (s > 0) {
    const synthetic = generateNormalSorted(n, m, s);
    const ks = ksTwoSample(sorted, synthetic);
    if (ks.pValue >= KS_MIN_PVALUE) {
      candidates.push({ name: "normal", params: { loc: m, scale: s }, pValue: ks.pValue });
    }
  }

  // --- Log-normal (requires all values > 0) ---
  if (sorted[0]! > 0) {
    const logValues = sorted.map((v) => Math.log(v));
    const logMean = mean(logValues)!;
    const logStd = std(logValues)!;
    if (logStd > 0) {
      const synthetic = generateLogNormalSorted(n, logMean, logStd);
      const ks = ksTwoSample(sorted, synthetic);
      if (ks.pValue >= KS_MIN_PVALUE) {
        candidates.push({
          name: "log_normal",
          params: { s: logStd, loc: 0, scale: Math.exp(logMean) },
          pValue: ks.pValue,
        });
      }
    }
  }

  // --- Exponential (requires all values >= 0) ---
  if (sorted[0]! >= 0) {
    const loc = minVal;
    const scale = m - loc;
    if (scale > 0) {
      const synthetic = generateExponentialSorted(n, loc, scale);
      const ks = ksTwoSample(sorted, synthetic);
      if (ks.pValue >= KS_MIN_PVALUE) {
        candidates.push({
          name: "exponential",
          params: { loc, scale },
          pValue: ks.pValue,
        });
      }
    }
  }

  // --- Uniform ---
  {
    const loc = minVal;
    const scale = maxVal - minVal;
    if (scale > 0) {
      const synthetic = generateUniformSorted(n, loc, scale);
      const ks = ksTwoSample(sorted, synthetic);
      if (ks.pValue >= KS_MIN_PVALUE) {
        candidates.push({
          name: "uniform",
          params: { loc, scale },
          pValue: ks.pValue,
        });
      }
    }
  }

  if (candidates.length === 0) return null;

  // Pick the candidate with the highest p-value (best fit)
  candidates.sort((a, b) => b.pValue - a.pValue);
  const best = candidates[0]!;

  // Round params
  const params: Record<string, number> = {};
  for (const [k, v] of Object.entries(best.params)) {
    params[k] = Math.round(v * 1e6) / 1e6;
  }

  return { name: best.name, params };
}

// ---------------------------------------------------------------------------
// Synthetic sorted sample generators (inverse CDF / quantile approach)
// ---------------------------------------------------------------------------

/** Generate n sorted samples from Normal(loc, scale) via inverse CDF. */
function generateNormalSorted(n: number, loc: number, scale: number): number[] {
  const result: number[] = [];
  for (let i = 0; i < n; i++) {
    const p = (i + 0.5) / n;
    result.push(loc + scale * normalQuantile(p));
  }
  return result;
}

/** Generate n sorted samples from LogNormal(logMean, logStd) via inverse CDF. */
function generateLogNormalSorted(n: number, logMean: number, logStd: number): number[] {
  const result: number[] = [];
  for (let i = 0; i < n; i++) {
    const p = (i + 0.5) / n;
    result.push(Math.exp(logMean + logStd * normalQuantile(p)));
  }
  return result;
}

/** Generate n sorted samples from Exponential(loc, scale) via inverse CDF. */
function generateExponentialSorted(n: number, loc: number, scale: number): number[] {
  const result: number[] = [];
  for (let i = 0; i < n; i++) {
    const p = (i + 0.5) / n;
    result.push(loc - scale * Math.log(1 - p));
  }
  return result;
}

/** Generate n sorted samples from Uniform(loc, scale) via inverse CDF. */
function generateUniformSorted(n: number, loc: number, scale: number): number[] {
  const result: number[] = [];
  for (let i = 0; i < n; i++) {
    const p = (i + 0.5) / n;
    result.push(loc + scale * p);
  }
  return result;
}

/**
 * Approximate inverse normal CDF (quantile function).
 * Rational approximation from Peter Acklam.
 */
function normalQuantile(p: number): number {
  if (p <= 0) return -Infinity;
  if (p >= 1) return Infinity;
  if (p === 0.5) return 0;

  // Coefficients for rational approximation
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

  let q: number;
  let r: number;

  if (p < pLow) {
    // Rational approximation for lower region
    q = Math.sqrt(-2 * Math.log(p));
    return (((((c1 * q + c2) * q + c3) * q + c4) * q + c5) * q + c6) /
           ((((d1 * q + d2) * q + d3) * q + d4) * q + 1);
  } else if (p <= pHigh) {
    // Rational approximation for central region
    q = p - 0.5;
    r = q * q;
    return (((((a1 * r + a2) * r + a3) * r + a4) * r + a5) * r + a6) * q /
           (((((b1 * r + b2) * r + b3) * r + b4) * r + b5) * r + 1);
  } else {
    // Rational approximation for upper region
    q = Math.sqrt(-2 * Math.log(1 - p));
    return -(((((c1 * q + c2) * q + c3) * q + c4) * q + c5) * q + c6) /
            ((((d1 * q + d2) * q + d3) * q + d4) * q + 1);
  }
}

// ---------------------------------------------------------------------------
// Histogram entropy
// ---------------------------------------------------------------------------

/** Compute approximate Shannon entropy using a histogram (Sturges' rule bins). */
function histogramEntropy(values: number[]): number {
  const n = values.length;
  if (n === 0) return 0;

  // Sturges' rule, capped between 10 and 100
  const nBins = Math.max(10, Math.min(100, Math.ceil(Math.log2(n) + 1)));

  let min = values[0]!;
  let max = values[0]!;
  for (let i = 1; i < values.length; i++) {
    if (values[i]! < min) min = values[i]!;
    if (values[i]! > max) max = values[i]!;
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

// ---------------------------------------------------------------------------
// Numeric bounds
// ---------------------------------------------------------------------------

function numericBounds(sorted: number[]): { min: number; max: number; p01: number; p99: number } {
  return {
    min: sorted[0]!,
    max: sorted[sorted.length - 1]!,
    p01: percentile(sorted, 1),
    p99: percentile(sorted, 99),
  };
}

// ---------------------------------------------------------------------------
// Categorical profiling
// ---------------------------------------------------------------------------

function profileCategorical(values: string[]): StatProfile {
  // Shannon entropy from frequency counts
  const counts = new Map<string, number>();
  for (const v of values) {
    counts.set(v, (counts.get(v) ?? 0) + 1);
  }
  const ent = shannonEntropy(counts);
  const nUnique = counts.size;

  return {
    distribution: null,
    params: {},
    entropy: ent,
    benford: null,
    bounds: { min: nUnique, max: nUnique, p01: nUnique, p99: nUnique },
  };
}

// ---------------------------------------------------------------------------
// Benford's law
// ---------------------------------------------------------------------------

function maybeBenford(
  col: string,
  values: number[],
): { conforming: boolean; chi2_pvalue: number } | null {
  const colLower = col.toLowerCase();

  // Skip identifier/code columns
  if (ID_KEYWORDS.some((kw) => colLower.includes(kw))) return null;
  // Skip percentage columns
  if (PCT_KEYWORDS.some((kw) => colLower.includes(kw))) return null;

  // Check keyword eligibility
  const nameEligible = [...BENFORD_KEYWORDS].some((kw) => colLower.includes(kw));
  if (!nameEligible) return null;

  // Require positive values
  const positives = values.filter((v) => v > 0 && Number.isFinite(v));
  if (positives.length < MIN_ROWS) return null;

  // Require 2+ orders of magnitude span
  let minPos = positives[0]!;
  let maxPos = positives[0]!;
  for (let i = 1; i < positives.length; i++) {
    if (positives[i]! < minPos) minPos = positives[i]!;
    if (positives[i]! > maxPos) maxPos = positives[i]!;
  }
  if (minPos <= 0) return null;
  const span = Math.log10(maxPos) - Math.log10(minPos);
  if (span < 2.0) return null;

  return computeBenford(positives);
}

function computeBenford(values: number[]): { conforming: boolean; chi2_pvalue: number } {
  const leadingDigits = extractLeadingDigits(values);
  const total = leadingDigits.length;

  if (total === 0) {
    return { conforming: false, chi2_pvalue: 0 };
  }

  // Count observed digits 1-9
  const digitCounts = new Array<number>(9).fill(0);
  for (const d of leadingDigits) {
    digitCounts[d - 1]!++;
  }

  // Expected counts from Benford's law
  const expectedProps = benfordExpected();
  const expectedCounts = expectedProps.map((p) => p * total);

  // Chi-squared goodness of fit test
  const { pValue } = chiSquaredTest(digitCounts, expectedCounts);

  return {
    conforming: pValue >= 0.05,
    chi2_pvalue: Math.round(pValue * 1e6) / 1e6,
  };
}

/** Extract the leading significant digit (1-9) from each value. */
function extractLeadingDigits(values: number[]): number[] {
  const digits: number[] = [];
  for (const v of values) {
    if (v <= 0 || !Number.isFinite(v)) continue;
    const exp = Math.floor(Math.log10(v));
    const normalised = v / 10 ** exp;
    const d = Math.floor(normalised);
    if (d >= 1 && d <= 9) {
      digits.push(d);
    }
  }
  return digits;
}
