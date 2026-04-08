// Column profiling + dtype inference. Pure functions, edge-safe.
//
// These replicate the Python provider profiling logic:
//   null_rate    = nulls / total
//   unique_rate  = unique non-null / total
//   value_count  = total
//   sample_values = first N non-null values as strings
//
// Python uses Polars dtype introspection. We infer dtype from sample values
// using a conservative heuristic matching Polars' normalized dtype set.
import type { Dtype, FieldInfo } from "../types.js";
import { makeFieldInfo } from "../types.js";

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const ISO_DATETIME = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+\-]\d{2}:?\d{2})?$/;
const BOOLEAN_VALUES = new Set(["true", "false", "True", "False", "TRUE", "FALSE", "0", "1"]);
const INT_RE = /^-?\d+$/;
const FLOAT_RE = /^-?\d+\.\d+$/;

export function isNullLike(value: unknown): boolean {
  if (value === null || value === undefined) return true;
  if (typeof value === "string") {
    const t = value.trim();
    return t === "" || t.toLowerCase() === "null" || t.toLowerCase() === "nan";
  }
  return false;
}

export function inferDtype(values: readonly unknown[]): Dtype {
  const sample = values.filter((v) => !isNullLike(v));
  if (sample.length === 0) return "string";

  // Check each row with all heuristics; whichever matches every row wins.
  let allBool = true;
  let allInt = true;
  let allFloat = true;
  let allDate = true;
  let allDatetime = true;

  for (const v of sample) {
    // Native types first
    if (typeof v === "boolean") {
      allInt = false;
      allFloat = false;
      allDate = false;
      allDatetime = false;
      continue;
    }
    if (typeof v === "number") {
      allBool = false;
      allDate = false;
      allDatetime = false;
      if (!Number.isInteger(v)) allInt = false;
      continue;
    }

    const s = typeof v === "string" ? v.trim() : String(v).trim();
    if (!BOOLEAN_VALUES.has(s)) allBool = false;
    if (!INT_RE.test(s)) allInt = false;
    if (!INT_RE.test(s) && !FLOAT_RE.test(s)) allFloat = false;
    if (!ISO_DATE.test(s)) allDate = false;
    if (!ISO_DATETIME.test(s)) allDatetime = false;
  }

  // Order of preference: boolean → integer → float → date → datetime → string.
  // Booleans first so "0"/"1" don't get caught as ints.
  if (allBool && sample.length > 0) {
    // Only count as boolean if we saw at least one true/false (not just 0/1)
    const hasTrueFalse = sample.some((v) => {
      const s = typeof v === "string" ? v.trim().toLowerCase() : String(v).toLowerCase();
      return s === "true" || s === "false";
    });
    if (hasTrueFalse || sample.every((v) => typeof v === "boolean")) return "boolean";
  }
  if (allInt) return "integer";
  if (allFloat) return "float";
  if (allDate) return "date";
  if (allDatetime) return "datetime";
  return "string";
}

export interface ProfileStats {
  nullRate: number;
  uniqueRate: number;
  valueCount: number;
  sampleValues: string[];
}

export function profileColumn(
  values: readonly unknown[],
  sampleSize = 500
): ProfileStats {
  const total = values.length;
  if (total === 0) {
    return { nullRate: 0, uniqueRate: 0, valueCount: 0, sampleValues: [] };
  }

  let nulls = 0;
  const nonNull: unknown[] = [];
  for (const v of values) {
    if (isNullLike(v)) {
      nulls++;
    } else {
      nonNull.push(v);
    }
  }

  const uniqueSet = new Set<string>();
  for (const v of nonNull) uniqueSet.add(String(v));

  const sampleValues = nonNull
    .slice(0, sampleSize)
    .map((v) => (typeof v === "string" ? v : String(v)));

  return {
    nullRate: nulls / total,
    uniqueRate: uniqueSet.size / total,
    valueCount: total,
    sampleValues,
  };
}

export function profileFieldFromValues(
  name: string,
  values: readonly unknown[],
  sampleSize = 500
): FieldInfo {
  const stats = profileColumn(values, sampleSize);
  return makeFieldInfo({
    name,
    dtype: inferDtype(values),
    sampleValues: stats.sampleValues,
    nullRate: stats.nullRate,
    uniqueRate: stats.uniqueRate,
    valueCount: stats.valueCount,
  });
}
