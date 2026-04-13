/**
 * TabularData — edge-safe columnar data abstraction over plain JS arrays.
 * Replaces Polars DataFrame for the core scan pipeline.
 * No Node.js or external dependencies.
 */

import { createRng, mean as statMean, std as statStd } from "./stats.js";

export type ColumnValue = string | number | boolean | null;
export type Row = Readonly<Record<string, unknown>>;
export type Dtype = "string" | "integer" | "float" | "boolean" | "date" | "datetime" | "null";

/** Values treated as null (case-insensitive for strings). */
const NULL_STRINGS = new Set(["", "null", "nan", "none", "na", "n/a", "#n/a", "nil"]);

export function isNullish(v: unknown): v is null | undefined {
  if (v === null || v === undefined) return true;
  if (typeof v === "string") return NULL_STRINGS.has(v.toLowerCase());
  return false;
}

/**
 * Lightweight columnar data wrapper. Constructed from an array of row objects.
 * All profiler operations go through this interface so the edge-safe core
 * never touches Polars or Node APIs.
 */
export class TabularData {
  private readonly _rows: readonly Row[];
  private readonly _columns: readonly string[];
  private readonly _columnCache = new Map<string, readonly ColumnValue[]>();

  constructor(rows: readonly Row[]) {
    this._rows = rows;
    this._columns =
      rows.length > 0 ? Object.keys(rows[0]!) : [];
  }

  // --- Accessors ---

  get columns(): readonly string[] {
    return this._columns;
  }

  get rowCount(): number {
    return this._rows.length;
  }

  get rows(): readonly Row[] {
    return this._rows;
  }

  /** Get all values for a column (cached). */
  column(name: string): readonly ColumnValue[] {
    let cached = this._columnCache.get(name);
    if (!cached) {
      cached = this._rows.map((r) => {
        const v = r[name];
        if (v === undefined || v === null) return null;
        if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") return v;
        return String(v);
      });
      this._columnCache.set(name, cached);
    }
    return cached;
  }

  // --- Null handling ---

  /** Count null/empty/nan-like values in a column. */
  nullCount(col: string): number {
    let count = 0;
    for (const v of this.column(col)) {
      if (isNullish(v)) count++;
    }
    return count;
  }

  /** Return non-null values for a column. */
  dropNulls(col: string): ColumnValue[] {
    return this.column(col).filter((v) => !isNullish(v)) as ColumnValue[];
  }

  // --- Type detection ---

  /** Infer the predominant dtype of a column.
   *  Prioritizes JS runtime types: if all non-null values are JS strings,
   *  returns "string" (matching Polars Utf8 behavior on CSV reads). */
  dtype(col: string): Dtype {
    const nonNull = this.dropNulls(col);
    if (nonNull.length === 0) return "null";

    // First pass: check JS runtime types.
    // If ALL non-null values are JS strings, return "string" — this matches
    // how Polars reports Utf8 dtype for mixed-content CSV columns.
    // The type_inference profiler is responsible for flagging "string column
    // that is mostly numeric."
    let allString = true;
    let allNumber = true;
    let allBoolean = true;
    for (const v of nonNull) {
      if (typeof v !== "string") allString = false;
      if (typeof v !== "number") allNumber = false;
      if (typeof v !== "boolean") allBoolean = false;
    }

    if (allBoolean) return "boolean";
    if (allNumber) {
      const hasFloat = nonNull.some((v) => typeof v === "number" && !Number.isInteger(v));
      return hasFloat ? "float" : "integer";
    }
    if (allString) {
      // All JS strings — try to narrow: are they all dates/datetimes?
      let dateCount = 0;
      let datetimeCount = 0;
      for (const v of nonNull) {
        const s = v as string;
        if (ISO_DATETIME_RE.test(s)) datetimeCount++;
        else if (ISO_DATE_RE.test(s)) dateCount++;
      }
      const total = nonNull.length;
      if (datetimeCount > total * 0.7) return "datetime";
      if ((dateCount + datetimeCount) > total * 0.7) return "date";
      return "string";
    }

    // Mixed JS types — infer from content
    let boolCount = 0;
    let intCount = 0;
    let floatCount = 0;
    let dateCount = 0;
    let datetimeCount = 0;
    let stringCount = 0;

    for (const v of nonNull) {
      if (typeof v === "boolean") {
        boolCount++;
      } else if (typeof v === "number") {
        if (Number.isInteger(v)) intCount++;
        else floatCount++;
      } else {
        const s = String(v);
        if (s === "true" || s === "false" || s === "True" || s === "False") {
          boolCount++;
        } else if (ISO_DATETIME_RE.test(s)) {
          datetimeCount++;
        } else if (ISO_DATE_RE.test(s)) {
          dateCount++;
        } else if (INT_RE.test(s)) {
          intCount++;
        } else if (FLOAT_RE.test(s)) {
          floatCount++;
        } else {
          stringCount++;
        }
      }
    }

    const counts: [Dtype, number][] = [
      ["boolean", boolCount],
      ["datetime", datetimeCount],
      ["date", dateCount],
      ["integer", intCount],
      ["float", floatCount],
      ["string", stringCount],
    ];
    counts.sort((a, b) => b[1] - a[1]);
    return counts[0]![0];
  }

  isNumeric(col: string): boolean {
    const dt = this.dtype(col);
    return dt === "integer" || dt === "float";
  }

  isString(col: string): boolean {
    return this.dtype(col) === "string";
  }

  // --- Aggregation ---

  /** Count distinct non-null values. */
  nUnique(col: string): number {
    const seen = new Set<ColumnValue>();
    for (const v of this.column(col)) {
      if (!isNullish(v)) seen.add(v);
    }
    return seen.size;
  }

  /** Value → count map for non-null values. */
  valueCounts(col: string): Map<ColumnValue, number> {
    const counts = new Map<ColumnValue, number>();
    for (const v of this.column(col)) {
      if (isNullish(v)) continue;
      counts.set(v, (counts.get(v) ?? 0) + 1);
    }
    return counts;
  }

  /** Top N values by frequency, descending. */
  topValues(col: string, n: number = 10): Array<[ColumnValue, number]> {
    const counts = this.valueCounts(col);
    return [...counts.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, n);
  }

  /** Min numeric value. Safe for large arrays (no spread). */
  min(col: string): number | null {
    const nums = this.numericValues(col);
    if (nums.length === 0) return null;
    let m = nums[0]!;
    for (let i = 1; i < nums.length; i++) if (nums[i]! < m) m = nums[i]!;
    return m;
  }

  /** Max numeric value. Safe for large arrays (no spread). */
  max(col: string): number | null {
    const nums = this.numericValues(col);
    if (nums.length === 0) return null;
    let m = nums[0]!;
    for (let i = 1; i < nums.length; i++) if (nums[i]! > m) m = nums[i]!;
    return m;
  }

  /** Mean of numeric values. */
  mean(col: string): number | null {
    return statMean(this.numericValues(col));
  }

  /** Population standard deviation. */
  std(col: string): number | null {
    return statStd(this.numericValues(col));
  }

  // --- Filtering ---

  filter(predicate: (row: Row) => boolean): TabularData {
    return new TabularData(this._rows.filter(predicate));
  }

  head(n: number): TabularData {
    return new TabularData(this._rows.slice(0, n));
  }

  /** Deterministic sample using seedable PRNG (Fisher-Yates). */
  sample(n: number, seed: number = 42): TabularData {
    if (n >= this._rows.length) return this;
    const indices = Array.from({ length: this._rows.length }, (_, i) => i);
    const rng = createRng(seed);
    // Fisher-Yates partial shuffle for first n elements
    for (let i = 0; i < n; i++) {
      const j = i + Math.floor(rng() * (indices.length - i));
      [indices[i], indices[j]] = [indices[j]!, indices[i]!];
    }
    const sampled = indices.slice(0, n).map((i) => this._rows[i]!);
    return new TabularData(sampled);
  }

  // --- String operations ---

  /** Test regex against string values in a column. Returns boolean array. */
  strContains(col: string, pattern: RegExp): boolean[] {
    return this.column(col).map((v) => {
      if (isNullish(v)) return false;
      return pattern.test(String(v));
    });
  }

  /** String lengths for each value in a column (null → 0). */
  strLengths(col: string): number[] {
    return this.column(col).map((v) => {
      if (isNullish(v)) return 0;
      return String(v).length;
    });
  }

  // --- Casting ---

  /** Attempt to cast column values to floats. Non-numeric → null. */
  castFloat(col: string): (number | null)[] {
    return this.column(col).map((v) => {
      if (isNullish(v)) return null;
      const n = Number(v);
      return Number.isFinite(n) ? n : null;
    });
  }

  /** Attempt to cast column values to integers. Non-integer → null. */
  castInt(col: string): (number | null)[] {
    return this.column(col).map((v) => {
      if (isNullish(v)) return null;
      const n = Number(v);
      return Number.isInteger(n) ? n : null;
    });
  }

  // --- Sorting checks ---

  /** Check if numeric values in a column are sorted ascending. */
  isSorted(col: string, descending: boolean = false): boolean {
    const nums = this.numericValues(col);
    for (let i = 1; i < nums.length; i++) {
      if (descending ? nums[i]! > nums[i - 1]! : nums[i]! < nums[i - 1]!) {
        return false;
      }
    }
    return true;
  }

  /** Differences between consecutive numeric values. */
  diff(col: string): (number | null)[] {
    const nums = this.castFloat(col);
    const result: (number | null)[] = [null];
    for (let i = 1; i < nums.length; i++) {
      const prev = nums[i - 1] ?? null;
      const curr = nums[i] ?? null;
      result.push(prev !== null && curr !== null ? curr - prev : null);
    }
    return result;
  }

  // --- Sorted numeric values (for percentile/IQR) ---

  /** Get sorted numeric values (non-null, finite). */
  sortedNumeric(col: string): number[] {
    return this.numericValues(col).sort((a, b) => a - b);
  }

  // --- Helpers ---

  /** Extract finite numeric values from a column. */
  numericValues(col: string): number[] {
    const result: number[] = [];
    for (const v of this.column(col)) {
      if (isNullish(v)) continue;
      const n = typeof v === "number" ? v : Number(v);
      if (Number.isFinite(n)) result.push(n);
    }
    return result;
  }

  /** Get string values (non-null). */
  stringValues(col: string): string[] {
    const result: string[] = [];
    for (const v of this.column(col)) {
      if (isNullish(v)) continue;
      result.push(String(v));
    }
    return result;
  }
}

// --- Regex patterns for dtype inference ---

const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const ISO_DATETIME_RE = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/;
const INT_RE = /^-?\d+$/;
const FLOAT_RE = /^-?\d+\.\d+$/;
