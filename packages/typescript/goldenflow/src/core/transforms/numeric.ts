/**
 * Numeric transforms — ported from goldenflow/transforms/numeric.py
 * Side-effect module: registers 9 numeric transforms on import.
 */

import type { ColumnValue } from "../types.js";
import { registerTransform } from "./registry.js";

// ---------------------------------------------------------------------------
// currency_strip (series, string|numeric, 50)
// ---------------------------------------------------------------------------

function currencyStrip(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null) return null;
    if (typeof v === "number") return v;
    const cleaned = String(v).replace(/[^0-9.\-]/g, "");
    if (cleaned === "" || cleaned === "-") return v;
    const n = Number(cleaned);
    return isNaN(n) ? v : n;
  });
}

registerTransform(
  { name: "currency_strip", inputTypes: ["string", "numeric"], priority: 50, mode: "series" },
  currencyStrip,
);

// ---------------------------------------------------------------------------
// percentage_normalize (series, string|numeric, 50)
// ---------------------------------------------------------------------------

function percentageNormalize(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null) return null;
    if (typeof v === "number") return v / 100;
    const s = String(v).replace(/%/g, "").trim();
    const n = Number(s);
    return isNaN(n) ? v : n / 100;
  });
}

registerTransform(
  { name: "percentage_normalize", inputTypes: ["string", "numeric"], priority: 50, mode: "series" },
  percentageNormalize,
);

// ---------------------------------------------------------------------------
// round (series, numeric, 40, param: n=2)
// ---------------------------------------------------------------------------

function roundTransform(
  values: readonly ColumnValue[],
  n: unknown = 2,
): ColumnValue[] {
  const decimals = typeof n === "number" ? n : Number(n) || 2;
  const factor = Math.pow(10, decimals);

  return values.map((v) => {
    if (v === null) return null;
    const num = typeof v === "number" ? v : Number(v);
    if (isNaN(num)) return v;
    return Math.round(num * factor) / factor;
  });
}

registerTransform(
  { name: "round", inputTypes: ["numeric"], priority: 40, mode: "series" },
  roundTransform,
);

// ---------------------------------------------------------------------------
// clamp (series, numeric, 40, params: min_val=0, max_val=1)
// ---------------------------------------------------------------------------

function clamp(
  values: readonly ColumnValue[],
  minVal: unknown = 0,
  maxVal: unknown = 1,
): ColumnValue[] {
  const lo = typeof minVal === "number" ? minVal : Number(minVal) || 0;
  const hi = typeof maxVal === "number" ? maxVal : Number(maxVal) || 1;

  return values.map((v) => {
    if (v === null) return null;
    const num = typeof v === "number" ? v : Number(v);
    if (isNaN(num)) return v;
    return Math.min(hi, Math.max(lo, num));
  });
}

registerTransform(
  { name: "clamp", inputTypes: ["numeric"], priority: 40, mode: "series" },
  clamp,
);

// ---------------------------------------------------------------------------
// to_integer (series, string|numeric, 45)
// ---------------------------------------------------------------------------

function toInteger(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null) return null;
    const num = Number(v);
    if (isNaN(num)) return v;
    return Math.trunc(num);
  });
}

registerTransform(
  { name: "to_integer", inputTypes: ["string", "numeric"], priority: 45, mode: "series" },
  toInteger,
);

// ---------------------------------------------------------------------------
// abs_value (series, numeric, 40)
// ---------------------------------------------------------------------------

function absValue(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null) return null;
    const num = typeof v === "number" ? v : Number(v);
    if (isNaN(num)) return v;
    return Math.abs(num);
  });
}

registerTransform(
  { name: "abs_value", inputTypes: ["numeric"], priority: 40, mode: "series" },
  absValue,
);

// ---------------------------------------------------------------------------
// fill_zero (series, numeric, 35) — null -> 0
// ---------------------------------------------------------------------------

function fillZero(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => (v === null ? 0 : v));
}

registerTransform(
  { name: "fill_zero", inputTypes: ["numeric"], priority: 35, mode: "series" },
  fillZero,
);

// ---------------------------------------------------------------------------
// comma_decimal (series, string|numeric, 48) — European "1.234,56" -> 1234.56
// ---------------------------------------------------------------------------

function commaDecimal(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null) return null;
    if (typeof v === "number") return v;
    const s = String(v);
    // European format: dots as thousands separators, comma as decimal
    const converted = s.replace(/\./g, "").replace(",", ".");
    const n = Number(converted);
    return isNaN(n) ? v : n;
  });
}

registerTransform(
  { name: "comma_decimal", inputTypes: ["string", "numeric"], priority: 48, mode: "series" },
  commaDecimal,
);

// ---------------------------------------------------------------------------
// scientific_to_decimal (series, string|numeric, 45)
// ---------------------------------------------------------------------------

function scientificToDecimal(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null) return null;
    const n = Number(v);
    return isNaN(n) ? v : n;
  });
}

registerTransform(
  { name: "scientific_to_decimal", inputTypes: ["string", "numeric"], priority: 45, mode: "series" },
  scientificToDecimal,
);
