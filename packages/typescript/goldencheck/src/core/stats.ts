/**
 * Pure-JS statistics utilities — edge-safe, no external dependencies.
 * Replaces scipy/numpy for the baseline/drift system.
 */

/** Arithmetic mean. Returns null for empty arrays. */
export function mean(values: readonly number[]): number | null {
  if (values.length === 0) return null;
  let sum = 0;
  for (const v of values) sum += v;
  return sum / values.length;
}

/** Population standard deviation. Returns null for empty arrays. */
export function std(values: readonly number[]): number | null {
  const m = mean(values);
  if (m === null) return null;
  let sumSq = 0;
  for (const v of values) sumSq += (v - m) ** 2;
  return Math.sqrt(sumSq / values.length);
}

/** Sample standard deviation. Returns null for < 2 values. */
export function sampleStd(values: readonly number[]): number | null {
  if (values.length < 2) return null;
  const m = mean(values)!;
  let sumSq = 0;
  for (const v of values) sumSq += (v - m) ** 2;
  return Math.sqrt(sumSq / (values.length - 1));
}

/** Percentile using linear interpolation (matches numpy default). */
export function percentile(sorted: readonly number[], p: number): number {
  if (sorted.length === 0) throw new Error("percentile: empty array");
  if (sorted.length === 1) return sorted[0]!;
  const idx = (p / 100) * (sorted.length - 1);
  const lo = Math.floor(idx);
  const hi = Math.ceil(idx);
  if (lo === hi) return sorted[lo]!;
  return sorted[lo]! + (sorted[hi]! - sorted[lo]!) * (idx - lo);
}

/** Interquartile range on a pre-sorted array. */
export function iqr(sorted: readonly number[]): number {
  return percentile(sorted, 75) - percentile(sorted, 25);
}

/** Median of a pre-sorted array. */
export function median(sorted: readonly number[]): number {
  return percentile(sorted, 50);
}

/** Shannon entropy from a frequency map. */
export function entropy(counts: ReadonlyMap<unknown, number>): number {
  let total = 0;
  for (const c of counts.values()) total += c;
  if (total === 0) return 0;
  let h = 0;
  for (const c of counts.values()) {
    if (c === 0) continue;
    const p = c / total;
    h -= p * Math.log2(p);
  }
  return h;
}

/** Pearson correlation coefficient. Returns null if not enough data or zero variance. */
export function pearson(x: readonly number[], y: readonly number[]): number | null {
  const n = Math.min(x.length, y.length);
  if (n < 3) return null;
  const mx = mean(x.slice(0, n))!;
  const my = mean(y.slice(0, n))!;
  let sumXY = 0;
  let sumX2 = 0;
  let sumY2 = 0;
  for (let i = 0; i < n; i++) {
    const dx = x[i]! - mx;
    const dy = y[i]! - my;
    sumXY += dx * dy;
    sumX2 += dx * dx;
    sumY2 += dy * dy;
  }
  const denom = Math.sqrt(sumX2 * sumY2);
  if (denom === 0) return null;
  return sumXY / denom;
}

/** Cramér's V from a contingency table (Map of Map). */
export function cramersV(
  contingency: ReadonlyMap<string, ReadonlyMap<string, number>>,
): number | null {
  // Build row/col totals
  const rowKeys = [...contingency.keys()];
  const colTotals = new Map<string, number>();
  const rowTotals = new Map<string, number>();
  let grand = 0;

  for (const [rk, row] of contingency) {
    let rt = 0;
    for (const [ck, count] of row) {
      rt += count;
      colTotals.set(ck, (colTotals.get(ck) ?? 0) + count);
    }
    rowTotals.set(rk, rt);
    grand += rt;
  }

  if (grand === 0) return null;
  const r = rowKeys.length;
  const k = colTotals.size;
  if (r < 2 || k < 2) return null;

  // Chi-squared
  let chi2 = 0;
  for (const [rk, row] of contingency) {
    const rt = rowTotals.get(rk)!;
    for (const [ck, observed] of row) {
      const ct = colTotals.get(ck)!;
      const expected = (rt * ct) / grand;
      if (expected > 0) {
        chi2 += (observed - expected) ** 2 / expected;
      }
    }
  }

  const minDim = Math.min(r, k) - 1;
  if (minDim === 0) return null;
  return Math.sqrt(chi2 / (grand * minDim));
}

/**
 * Two-sample Kolmogorov-Smirnov test statistic.
 * Both inputs must be pre-sorted ascending.
 * Returns { statistic, pValue } where pValue is an approximation.
 */
export function ksTwoSample(
  sorted1: readonly number[],
  sorted2: readonly number[],
): { statistic: number; pValue: number } {
  const n1 = sorted1.length;
  const n2 = sorted2.length;
  if (n1 === 0 || n2 === 0) return { statistic: 0, pValue: 1 };

  let i1 = 0;
  let i2 = 0;
  let d1 = 0;
  let d2 = 0;
  let maxD = 0;

  while (i1 < n1 && i2 < n2) {
    const v1 = sorted1[i1]!;
    const v2 = sorted2[i2]!;
    if (v1 <= v2) {
      d1 = (i1 + 1) / n1;
      i1++;
    }
    if (v2 <= v1) {
      d2 = (i2 + 1) / n2;
      i2++;
    }
    const diff = Math.abs(d1 - d2);
    if (diff > maxD) maxD = diff;
  }

  // Short-circuit: identical distributions → p-value = 1
  if (maxD === 0) return { statistic: 0, pValue: 1 };

  // Approximate p-value using asymptotic formula
  const en = Math.sqrt((n1 * n2) / (n1 + n2));
  const lambda = (en + 0.12 + 0.11 / en) * maxD;
  // Kolmogorov distribution approximation
  let pValue = 0;
  for (let j = 1; j <= 100; j++) {
    pValue += 2 * (j % 2 === 0 ? -1 : 1) * Math.exp(-2 * j * j * lambda * lambda);
  }
  pValue = Math.max(0, Math.min(1, pValue));

  return { statistic: maxD, pValue };
}

/**
 * Chi-squared goodness of fit test.
 * observed and expected arrays must have the same length.
 */
export function chiSquaredTest(
  observed: readonly number[],
  expected: readonly number[],
): { statistic: number; pValue: number } {
  let chi2 = 0;
  const df = observed.length - 1;

  for (let i = 0; i < observed.length; i++) {
    const e = expected[i]!;
    if (e > 0) {
      chi2 += (observed[i]! - e) ** 2 / e;
    }
  }

  // Approximate p-value using Wilson-Hilferty approximation
  if (df <= 0) return { statistic: chi2, pValue: 1 };
  const z = Math.cbrt(chi2 / df) - (1 - 2 / (9 * df));
  const denom = Math.sqrt(2 / (9 * df));
  const pValue = 1 - normalCdf(z / denom);

  return { statistic: chi2, pValue: Math.max(0, Math.min(1, pValue)) };
}

/** Standard normal CDF approximation (Abramowitz & Stegun). */
export function normalCdf(x: number): number {
  const a1 = 0.254829592;
  const a2 = -0.284496736;
  const a3 = 1.421413741;
  const a4 = -1.453152027;
  const a5 = 1.061405429;
  const p = 0.3275911;

  const sign = x < 0 ? -1 : 1;
  x = Math.abs(x) / Math.SQRT2;
  const t = 1.0 / (1.0 + p * x);
  const y = 1.0 - ((((a5 * t + a4) * t + a3) * t + a2) * t + a1) * t * Math.exp(-x * x);
  return 0.5 * (1.0 + sign * y);
}

/**
 * Benford's law expected distribution for first digits 1-9.
 */
export function benfordExpected(): readonly number[] {
  return [
    Math.log10(1 + 1 / 1), // ~0.301
    Math.log10(1 + 1 / 2), // ~0.176
    Math.log10(1 + 1 / 3), // ~0.125
    Math.log10(1 + 1 / 4), // ~0.097
    Math.log10(1 + 1 / 5), // ~0.079
    Math.log10(1 + 1 / 6), // ~0.067
    Math.log10(1 + 1 / 7), // ~0.058
    Math.log10(1 + 1 / 8), // ~0.051
    Math.log10(1 + 1 / 9), // ~0.046
  ];
}

/**
 * Seedable pseudo-random number generator (Mulberry32).
 * NOTE: This uses a different algorithm than Python's Mersenne Twister,
 * so sampling results will differ from the Python version. Both are
 * deterministic with the same seed within their respective implementations.
 */
export function createRng(seed: number): () => number {
  let state = seed | 0;
  return () => {
    state = (state + 0x6d2b79f5) | 0;
    let t = Math.imul(state ^ (state >>> 15), 1 | state);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
