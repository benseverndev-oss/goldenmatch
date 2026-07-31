/**
 * Ground-truth g1 validation for candidate denial constraints. Parity port of
 * `packages/python/goldencheck/goldencheck/denial/validate.py`.
 *
 * Discovery works on a sample; this re-measures each candidate's g1 on the real
 * frame so a sample artefact never ships as a finding.
 */

import { TabularData } from "../data.js";
import { VALIDATION_SAMPLE } from "./constants.js";
import type { Predicate } from "./models.js";
import { encodeColumns, predicateHolds } from "./predicates.js";

/** True iff no predicate has kind "cross" (the DC scopes to one tuple). */
export function isSingleTuple(preds: readonly Predicate[]): boolean {
  return preds.every((p) => p.kind !== "cross");
}

/** Exact O(n) validation. Returns `[g1, violatingRowIndices]`. */
export function validateSingleTuple(preds: readonly Predicate[], df: TabularData): [number, number[]] {
  const n = df.rowCount;
  if (n === 0) return [0.0, []];
  const enc = encodeColumns(df);
  const violating: number[] = [];
  for (let r = 0; r < n; r++) {
    if (preds.every((p) => predicateHolds(p, enc, r, null))) violating.push(r);
  }
  return [violating.length / n, violating];
}

/** Build a sub-frame from selected row indices. */
function subFrame(df: TabularData, rows: readonly number[]): TabularData {
  return new TabularData(rows.map((r) => df.rows[r]!));
}

/**
 * Estimated g1 over a bounded row sample. Returns `[g1Est, pairs]`.
 *
 * DIVERGENCE: when `n > sample` Python draws a seeded `random.Random(seed).sample`
 * whose Mersenne-Twister sequence TS cannot reproduce; TS falls back to the first
 * `sample` rows. This only fires above `VALIDATION_SAMPLE` (20000) rows — the
 * parity fixture stays well under it, so `rows = range(n)` and both are identical.
 */
export function validateCrossTuple(
  preds: readonly Predicate[],
  df: TabularData,
  opts: { sample?: number; maxPairs?: number } = {},
): [number, Array<[number, number]>] {
  const sample = opts.sample ?? VALIDATION_SAMPLE;
  const maxPairs = opts.maxPairs ?? 5;
  const n = df.rowCount;
  const m = Math.min(n, sample);
  if (m < 2) return [0.0, []];

  const rows = n <= sample ? Array.from({ length: n }, (_, i) => i) : Array.from({ length: m }, (_, i) => i);
  const sub = subFrame(df, rows);
  const enc = encodeColumns(sub);

  const single = preds.filter((p) => p.kind !== "cross");
  const cross = preds.filter((p) => p.kind === "cross");

  let violations = 0;
  const examples: Array<[number, number]> = [];
  for (let a = 0; a < m; a++) {
    if (!single.every((p) => predicateHolds(p, enc, a, null))) continue;
    for (let b = 0; b < m; b++) {
      if (a === b) continue;
      if (cross.every((p) => predicateHolds(p, enc, a, b))) {
        violations++;
        if (examples.length < maxPairs) examples.push([a, b]);
      }
    }
  }

  const g1Est = violations / (m * (m - 1));
  return [g1Est, examples];
}
