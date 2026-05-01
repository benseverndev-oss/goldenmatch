/**
 * TabularData — edge-safe Polars replacement.
 * Wraps Record<string, unknown>[] and provides column operations.
 */

import type { ColumnValue, Dtype, Row } from "./types.js";

/** Values treated as null (case-insensitive for strings). */
const NULL_STRINGS = new Set([
  "",
  "null",
  "none",
  "nan",
  "n/a",
  "na",
  "nil",
  "#n/a",
  "missing",
  "undefined",
]);

export function isNullish(v: unknown): v is null | undefined {
  if (v === null || v === undefined) return true;
  if (typeof v === "string") return NULL_STRINGS.has(v.toLowerCase().trim());
  if (typeof v === "number") return Number.isNaN(v);
  return false;
}

export function toColumnValue(v: unknown): ColumnValue {
  if (isNullish(v)) return null;
  if (typeof v === "string") return v;
  if (typeof v === "number") return v;
  if (typeof v === "boolean") return v;
  return String(v);
}

/** Mulberry32 seedable PRNG (NOT Mersenne Twister — results differ from Python). */
function mulberry32(seed: number): () => number {
  let s = seed | 0;
  return () => {
    s = (s + 0x6d2b79f5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export class TabularData {
  private readonly _rows: readonly Row[];
  private _columnCache = new Map<string, readonly ColumnValue[]>();

  constructor(rows: readonly Row[]) {
    this._rows = rows;
  }

  get rows(): readonly Row[] {
    return this._rows;
  }

  get columns(): readonly string[] {
    if (this._rows.length === 0) return [];
    return Object.keys(this._rows[0]!);
  }

  get rowCount(): number {
    return this._rows.length;
  }

  // ---- Column access ----

  column(name: string): readonly ColumnValue[] {
    const cached = this._columnCache.get(name);
    if (cached) return cached;
    const values = this._rows.map((r) => toColumnValue(r[name]));
    this._columnCache.set(name, values);
    return values;
  }

  /** Raw column access — preserves original values without null coercion.
   *  Use for profiling where "N/A" should remain a string, not become null. */
  rawColumn(name: string): readonly ColumnValue[] {
    return this._rows.map((r) => {
      const v = r[name];
      if (v === null || v === undefined) return null;
      if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") return v;
      return String(v);
    });
  }

  // ---- Null handling ----

  nullCount(col: string): number {
    let count = 0;
    for (const v of this.column(col)) {
      if (v === null) count++;
    }
    return count;
  }

  dropNulls(col: string): ColumnValue[] {
    return this.column(col).filter((v): v is Exclude<ColumnValue, null> => v !== null);
  }

  // ---- Type inference ----

  dtype(col: string): Dtype {
    const values = this.dropNulls(col);
    if (values.length === 0) return "null";

    let hasInt = false;
    let hasFloat = false;
    let hasBool = false;
    let hasString = false;

    for (const v of values) {
      if (typeof v === "boolean") {
        hasBool = true;
      } else if (typeof v === "number") {
        if (Number.isInteger(v)) hasInt = true;
        else hasFloat = true;
      } else {
        hasString = true;
      }
    }

    if (hasString) return "string";
    if (hasBool && !hasInt && !hasFloat) return "boolean";
    if (hasFloat) return "float";
    if (hasInt) return "integer";
    return "string";
  }

  // ---- Aggregation ----

  nUnique(col: string): number {
    const set = new Set<ColumnValue>();
    for (const v of this.dropNulls(col)) set.add(v);
    return set.size;
  }

  valueCounts(col: string): Map<ColumnValue, number> {
    const map = new Map<ColumnValue, number>();
    for (const v of this.dropNulls(col)) {
      map.set(v, (map.get(v) ?? 0) + 1);
    }
    return map;
  }

  /** MUST use loop — Math.min(...array) crashes on >65K elements. */
  min(col: string): number | null {
    const nums = this.numericValues(col);
    if (nums.length === 0) return null;
    let m = nums[0]!;
    for (let i = 1; i < nums.length; i++) {
      if (nums[i]! < m) m = nums[i]!;
    }
    return m;
  }

  /** MUST use loop — Math.max(...array) crashes on >65K elements. */
  max(col: string): number | null {
    const nums = this.numericValues(col);
    if (nums.length === 0) return null;
    let m = nums[0]!;
    for (let i = 1; i < nums.length; i++) {
      if (nums[i]! > m) m = nums[i]!;
    }
    return m;
  }

  mean(col: string): number | null {
    const nums = this.numericValues(col);
    if (nums.length === 0) return null;
    let sum = 0;
    for (const n of nums) sum += n;
    return sum / nums.length;
  }

  std(col: string): number | null {
    const nums = this.numericValues(col);
    if (nums.length < 2) return null;
    const avg = this.mean(col)!;
    let sumSq = 0;
    for (const n of nums) sumSq += (n - avg) ** 2;
    return Math.sqrt(sumSq / (nums.length - 1));
  }

  // ---- Filtering & sampling ----

  filter(predicate: (row: Row) => boolean): TabularData {
    return new TabularData(this._rows.filter(predicate));
  }

  head(n: number): TabularData {
    return new TabularData(this._rows.slice(0, n));
  }

  sample(n: number, seed = 42): TabularData {
    if (n >= this._rows.length) return this;
    const rng = mulberry32(seed);
    const indices = Array.from({ length: this._rows.length }, (_, i) => i);
    // Fisher-Yates shuffle (partial)
    for (let i = indices.length - 1; i > 0 && indices.length - 1 - i < n; i--) {
      const j = Math.floor(rng() * (i + 1));
      [indices[i], indices[j]] = [indices[j]!, indices[i]!];
    }
    const sampled = indices.slice(indices.length - n).map((i) => this._rows[i]!);
    return new TabularData(sampled);
  }

  // ---- String operations ----

  strContains(col: string, pattern: RegExp): boolean[] {
    return this.column(col).map((v) =>
      typeof v === "string" ? pattern.test(v) : false,
    );
  }

  strLengths(col: string): number[] {
    return this.column(col).map((v) =>
      typeof v === "string" ? v.length : 0,
    );
  }

  // ---- Casting ----

  castFloat(col: string): (number | null)[] {
    return this.column(col).map((v) => {
      if (v === null) return null;
      const n = Number(v);
      return Number.isFinite(n) ? n : null;
    });
  }

  castInt(col: string): (number | null)[] {
    return this.column(col).map((v) => {
      if (v === null) return null;
      const n = Number(v);
      return Number.isFinite(n) ? Math.trunc(n) : null;
    });
  }

  // ---- Helpers ----

  numericValues(col: string): number[] {
    const result: number[] = [];
    for (const v of this.column(col)) {
      if (typeof v === "number" && Number.isFinite(v)) {
        result.push(v);
      }
    }
    return result;
  }

  stringValues(col: string): string[] {
    const result: string[] = [];
    for (const v of this.column(col)) {
      if (typeof v === "string") result.push(v);
    }
    return result;
  }

  sortedNumeric(col: string): number[] {
    return this.numericValues(col).sort((a, b) => a - b);
  }

  isSorted(col: string, descending = false): boolean {
    const nums = this.numericValues(col);
    for (let i = 1; i < nums.length; i++) {
      if (descending ? nums[i]! > nums[i - 1]! : nums[i]! < nums[i - 1]!) {
        return false;
      }
    }
    return true;
  }
}
