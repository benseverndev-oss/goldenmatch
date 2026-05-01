/**
 * Smart sampling for large datasets.
 * Port of goldencheck/engine/sampler.py.
 */

import type { TabularData } from "../data.js";

/** Return data unchanged if ≤ maxRows, otherwise deterministic sample. */
export function maybeSample(data: TabularData, maxRows: number = 100_000): TabularData {
  if (data.rowCount <= maxRows) return data;
  return data.sample(maxRows, 42);
}
