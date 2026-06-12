/**
 * Fuzzy near-duplicate VALUE detection (column profiler).
 * Port of goldencheck/profilers/fuzzy_values.py (the pure-Python fallback
 * `_python_clusters`; the native kernel is Python-only and byte-identical).
 *
 * Flags categorical string columns whose distinct values include
 * edit-distance-close variants ("California"/"Californa"/"CALIFORNIA"). Runs on
 * a column's DISTINCT values with trigram + 2-char-prefix blocking and a
 * Levenshtein-ratio scorer, then union-find clusters.
 */
import type { TabularData } from "../data.js";
import { isNullish } from "../data.js";
import { type Finding, Severity, makeFinding } from "../types.js";
import type { Profiler } from "./base.js";

const MIN_ROWS = 50;
const MIN_DISTINCT = 3;
const MAX_DISTINCT = 2000;
const MIN_SIMILARITY = 0.82;
const MAX_CLUSTERS_REPORTED = 5;

function normalize(s: string): string {
  return s.toLowerCase().replace(/\s+/g, " ").trim();
}

function levenshtein(a: string, b: string): number {
  if (a.length === 0) return b.length;
  if (b.length === 0) return a.length;
  let prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 0; i < a.length; i++) {
    const cur = [i + 1];
    for (let j = 0; j < b.length; j++) {
      const cost = a[i] === b[j] ? 0 : 1;
      cur.push(Math.min(prev[j + 1]! + 1, cur[j]! + 1, prev[j]! + cost));
    }
    prev = cur;
  }
  return prev[b.length]!;
}

function levRatio(a: string, b: string): number {
  const maxlen = Math.max(a.length, b.length);
  if (maxlen === 0) return 1.0;
  return 1.0 - levenshtein(a, b) / maxlen;
}

function pushBucket(map: Map<string, number[]>, key: string, value: number): void {
  let bucket = map.get(key);
  if (!bucket) {
    bucket = [];
    map.set(key, bucket);
  }
  bucket.push(value);
}

/** Mirror of goldencheck_core::near_duplicate_clusters / the Python fallback. */
function clusters(values: readonly string[], minSimilarity: number): number[][] {
  const norm = values.map(normalize);
  const n = values.length;
  const trigram = new Map<string, number[]>();
  const prefix = new Map<string, number[]>();
  for (let i = 0; i < n; i++) {
    const s = norm[i]!;
    if (s.length < 3) continue;
    for (let k = 0; k < s.length - 2; k++) {
      pushBucket(trigram, s.slice(k, k + 3), i);
    }
    pushBucket(prefix, s.slice(0, 2), i);
  }

  // Candidate pairs from blocking buckets (size in [2, 300]); dedup via a Set.
  const candidates = new Set<number>(); // encode (i,j), i<j, as i*n+j
  for (const bucket of [...trigram.values(), ...prefix.values()]) {
    if (bucket.length < 2 || bucket.length > 300) continue;
    for (let a = 0; a < bucket.length; a++) {
      for (let b = a + 1; b < bucket.length; b++) {
        const i = bucket[a]!;
        const j = bucket[b]!;
        candidates.add(i < j ? i * n + j : j * n + i);
      }
    }
  }

  const parent = Array.from({ length: n }, (_, i) => i);
  const find = (x: number): number => {
    while (parent[x] !== x) {
      parent[x] = parent[parent[x]!]!;
      x = parent[x]!;
    }
    return x;
  };

  let linked = false;
  for (const enc of candidates) {
    const i = Math.floor(enc / n);
    const j = enc % n;
    if (levRatio(norm[i]!, norm[j]!) >= minSimilarity) {
      const ri = find(i);
      const rj = find(j);
      if (ri !== rj) parent[ri] = rj;
      linked = true;
    }
  }
  if (!linked) return [];

  const groups = new Map<number, number[]>();
  for (let i = 0; i < n; i++) {
    pushBucket(groups, String(find(i)), i);
  }
  const out = [...groups.values()]
    .filter((g) => g.length >= 2)
    .map((g) => [...g].sort((a, b) => a - b));
  // Lexicographic sort of clusters (element-wise), mirroring Python clusters.sort().
  out.sort((x, y) => {
    const len = Math.min(x.length, y.length);
    for (let k = 0; k < len; k++) {
      if (x[k] !== y[k]) return x[k]! - y[k]!;
    }
    return x.length - y.length;
  });
  return out;
}

export class FuzzyValuesProfiler implements Profiler {
  profile(data: TabularData, column: string): Finding[] {
    if (data.rowCount < MIN_ROWS) return [];
    if (data.dtype(column) !== "string") return [];

    // Distinct non-null values, first-seen order.
    const seen = new Set<string>();
    const values: string[] = [];
    for (const v of data.column(column)) {
      if (isNullish(v)) continue;
      const s = String(v);
      if (!seen.has(s)) {
        seen.add(s);
        values.push(s);
      }
    }
    const nDistinct = values.length;
    if (nDistinct < MIN_DISTINCT || nDistinct > MAX_DISTINCT) return [];

    const found = clusters(values, MIN_SIMILARITY);
    if (found.length === 0) return [];

    // Largest clusters first (stable), report a bounded number.
    const ordered = [...found].sort((a, b) => b.length - a.length);
    const findings: Finding[] = [];
    const fullColumn = data.column(column);
    for (const cluster of ordered.slice(0, MAX_CLUSTERS_REPORTED)) {
      const variants = cluster.map((i) => values[i]!);
      const shown = variants.slice(0, 6);
      const variantSet = new Set(variants);
      let affected = 0;
      for (const v of fullColumn) {
        if (v !== null && variantSet.has(String(v))) affected++;
      }
      const ellipsis = variants.length > shown.length ? " …" : "";
      findings.push(
        makeFinding({
          severity: Severity.WARNING,
          column,
          check: "fuzzy_duplicate_values",
          message:
            `Column '${column}' has ${variants.length} near-duplicate values that look ` +
            `like variants of one another: ${shown.map((v) => `'${v}'`).join(", ")}${ellipsis}.`,
          affectedRows: affected,
          sampleValues: shown.map((v) => String(v)),
          suggestion:
            "Standardize these to a single canonical value (casing/spelling), " +
            "or define an enum, so they reconcile.",
          confidence: 0.6,
          metadata: { technique: "fuzzy_duplicate_values", variants },
        }),
      );
    }
    return findings;
  }
}
