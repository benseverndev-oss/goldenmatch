/**
 * Auto-correct transform — owned FUZZY kernel (Wave D autocorrect), the
 * data-dependent one. Ported byte-for-byte from goldenflow-core::autocorrect
 * (the Rust reference) + the host-side value_counts + apply spec in
 * goldenflow/transforms/auto_correct.py.
 *
 * `category_auto_correct` is `mode="series"` (whole column): the host computes
 * `value_counts` over the RAW non-null string values, passes (values[],
 * counts[]) to the kernel's `build_canonical_map`, gets a variant->canonical
 * corrections map, then applies `corrections.get(v.strip(), v.strip())` per
 * element (STRIPS every value — a documented side effect). value_counts + apply
 * are orchestration (stay host); the correction-map algorithm is the kernel.
 *
 * The algorithm is a faithful port of the Rust kernel:
 *   - `fuzzRatioTs` = the rapidfuzz `fuzz.ratio` Indel/LCS similarity
 *     (`100*(1 - indel/(la+lb))`, `indel = la+lb-2*LCS`, `("","")->100`), over
 *     code points. This REPLACES the previous WRONG Levenshtein-based ratio.
 *   - `buildCanonicalMapTs` = the insertion-ordered frequency->canonical->fuzzy
 *     map builder, order-deterministic (insertion-ordered `Map`s + first-max on
 *     ties, `c > best` / `score > bestScore` strictly) to match Python's
 *     `Counter`/dict + `value_counts(sort=True)` order. The apply now STRIPS
 *     (the prior TS did not) — both fixes unify TS with the Python reference.
 *
 * Dispatches native-first through the opt-in WASM backend (`FlowWasmBackend`)
 * when `enableWasm()` has succeeded; otherwise runs the pure-TS port below.
 * Pure-TS is the default + fallback.
 */

import type { ColumnValue } from "../types.js";
import { registerTransform } from "./registry.js";
import { getFlowWasmBackend } from "../wasm/backend.js";

// ---------------------------------------------------------------------------
// Pure-TS kernel references (byte-identical to goldenflow-core::autocorrect).
// ---------------------------------------------------------------------------

/** Length of the longest common subsequence of two code-point arrays
 * (single-row DP), matching the Rust `lcs_len`. */
function lcsLen(a: readonly string[], b: readonly string[]): number {
  const n = b.length;
  if (a.length === 0 || n === 0) return 0;
  let prev = new Array<number>(n + 1).fill(0);
  for (const ca of a) {
    const row = new Array<number>(n + 1).fill(0);
    let prevDiag = 0;
    for (let j = 1; j <= n; j++) {
      row[j] = ca === b[j - 1] ? prevDiag + 1 : Math.max(prev[j]!, row[j - 1]!);
      prevDiag = prev[j]!;
    }
    prev = row;
  }
  return prev[n]!;
}

/**
 * rapidfuzz `fuzz.ratio`: `100 * (1 - indel/(len_a+len_b))` where
 * `indel = len_a + len_b - 2*LCS`. Two empty strings -> 100. Operates on code
 * points (matching rapidfuzz on Python `str` / the Rust `chars()` kernel).
 */
export function fuzzRatioTs(a: string, b: string): number {
  const ca = [...a];
  const cb = [...b];
  const la = ca.length;
  const lb = cb.length;
  if (la === 0 && lb === 0) return 100;
  const lcs = lcsLen(ca, cb);
  const total = la + lb;
  const indel = la + lb - 2 * lcs;
  return 100 * (1 - indel / total);
}

/**
 * Build the variant->canonical correction map from `(value, count)` pairs
 * (typically a `value_counts(sort=True)` result). Byte-identical to the Rust
 * `autocorrect::build_canonical_map` / Python `_build_canonical_map`. Returns
 * the corrections keyed by the STRIPPED original casing.
 *
 * Order-deterministic: values are processed in the input order (= value_counts
 * order); `most_common`/best-score ties resolve to the FIRST (insertion order),
 * exactly like Python's `Counter.most_common` + `score > best`.
 */
export function buildCanonicalMapTs(
  values: readonly (string | null)[],
  counts: readonly number[],
  freqThreshold: number,
  matchThreshold: number,
): Map<string, string> {
  // lowercase -> total count (insertion-ordered), and lowercase -> ordered list
  // of [original casing, count]. `Map` preserves insertion order.
  const lowerCounts = new Map<string, number>();
  const caseMap = new Map<string, Array<[string, number]>>();

  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    if (v === null || v === undefined) continue;
    const count = counts[i]!;
    const vStripped = v.trim();
    if (vStripped === "") continue;
    const low = vStripped.toLowerCase();
    lowerCounts.set(low, (lowerCounts.get(low) ?? 0) + count);
    let casings = caseMap.get(low);
    if (!casings) {
      casings = [];
      caseMap.set(low, casings);
    }
    const entry = casings.find(([c]) => c === vStripped);
    if (entry) entry[1] += count;
    else casings.push([vStripped, count]);
  }

  const corrections = new Map<string, string>();
  let total = 0;
  for (const c of lowerCounts.values()) total += c;
  if (total === 0) return corrections;

  // Canonical determination (in insertion order).
  const canonical = new Map<string, string>(); // low -> best casing
  const canonicalOrder: string[] = [];
  const lowFreq: string[] = [];

  for (const [low, count] of lowerCounts) {
    if (count / total >= freqThreshold) {
      // most_common(1): highest count; tie -> first (insertion order).
      const casings = caseMap.get(low)!;
      let bestCasing = casings[0]![0];
      let bestCount = casings[0]![1];
      for (let k = 1; k < casings.length; k++) {
        const [casing, c] = casings[k]!;
        if (c > bestCount) {
          bestCount = c;
          bestCasing = casing;
        }
      }
      canonical.set(low, bestCasing);
      canonicalOrder.push(low);
    } else {
      lowFreq.push(low);
    }
  }

  // Exact case-insensitive corrections (non-best casings -> best casing).
  for (const low of canonicalOrder) {
    const best = canonical.get(low)!;
    for (const [casing] of caseMap.get(low)!) {
      if (casing !== best) corrections.set(casing, best);
    }
  }

  // Fuzzy corrections for low-frequency values.
  for (const low of lowFreq) {
    let bestScore = 0;
    let bestMatch: string | null = null;
    for (const canonLow of canonicalOrder) {
      const score = fuzzRatioTs(low, canonLow);
      if (score > bestScore) {
        bestScore = score;
        bestMatch = canonLow;
      }
    }
    if (bestScore >= matchThreshold && bestMatch !== null) {
      const canonCasing = canonical.get(bestMatch)!;
      for (const [casing] of caseMap.get(low)!) {
        corrections.set(casing, canonCasing);
      }
    }
  }

  return corrections;
}

// ---------------------------------------------------------------------------
// category_auto_correct (series, string, 35, auto_apply)
// ---------------------------------------------------------------------------

function categoryAutoCorrect(
  values: readonly ColumnValue[],
  frequencyThreshold: unknown = 0.05,
  matchThreshold: unknown = 85,
): ColumnValue[] {
  const freqThresh =
    typeof frequencyThreshold === "number"
      ? frequencyThreshold
      : Number(frequencyThreshold) || 0.05;
  const matchThresh =
    typeof matchThreshold === "number" ? matchThreshold : Number(matchThreshold) || 85;

  // value_counts over the RAW non-null string values: polars `value_counts`
  // counts the raw values BEFORE stripping (" active " / "active" are distinct
  // rows); the kernel strips/lowercases internally. `Map` keeps first-seen
  // order, and `Array.prototype.sort` is stable (ES2019+), so sorting by count
  // DESC mirrors polars `value_counts(sort=True)` (ties keep first-seen order).
  const vc = new Map<string, number>();
  for (const v of values) {
    if (v === null || typeof v !== "string") continue;
    vc.set(v, (vc.get(v) ?? 0) + 1);
  }
  const pairs = [...vc.entries()];
  pairs.sort((a, b) => b[1] - a[1]);
  const valuesArr = pairs.map(([v]) => v);
  const countsArr = pairs.map(([, c]) => c);

  const backend = getFlowWasmBackend();
  let corrections: Map<string, string>;
  if (backend) {
    // wasm returns a FLAT [from0, to0, from1, to1, ...] array; unflatten it.
    const flat = backend.buildCanonicalMap(valuesArr, countsArr, freqThresh, matchThresh);
    corrections = new Map<string, string>();
    for (let i = 0; i + 1 < flat.length; i += 2) {
      corrections.set(flat[i]!, flat[i + 1]!);
    }
  } else {
    corrections = buildCanonicalMapTs(valuesArr, countsArr, freqThresh, matchThresh);
  }

  // No corrections -> return the column unchanged (matches Python `if not
  // corrections: return series` — no strip when there's nothing to correct).
  if (corrections.size === 0) return values.slice();

  // Apply — STRIP every value (matches Python `corrections.get(v.strip(),
  // v.strip())`; returns the stripped value even when uncorrected).
  return values.map((v) => {
    if (v === null || typeof v !== "string") return v;
    const stripped = v.trim();
    return corrections.get(stripped) ?? stripped;
  });
}

registerTransform(
  { name: "category_auto_correct", inputTypes: ["string"], autoApply: true, priority: 35, mode: "series" },
  categoryAutoCorrect,
);
