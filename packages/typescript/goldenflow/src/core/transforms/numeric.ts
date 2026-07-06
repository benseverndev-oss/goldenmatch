/**
 * Numeric transforms — ported from goldenflow/transforms/numeric.py
 * Side-effect module: registers 9 numeric transforms on import.
 *
 * Owned-kernel family (D4 wave): each transform is a value-for-value port of
 * the Python pure-TS reference (`_currency_strip_py` et al. in
 * `goldenflow/transforms/numeric.py`), which is itself proven value-identical
 * to the Rust `goldenflow-core::numeric` kernels (parity corpus in
 * `tests/parity/identifiers_corpus.jsonl` for the string->number parsers;
 * `test_numeric_kernels.py` for the numeric-array ops). This family outputs
 * floats/ints, so parity is by VALUE, not string repr. Each transform
 * dispatches to the opt-in WASM backend (`FlowWasmBackend`) when
 * `enableWasm()` has succeeded; otherwise it runs the pure-TS implementation
 * below. Pure-TS is the default.
 */

import type { ColumnValue } from "../types.js";
import { registerTransform } from "./registry.js";
import { getFlowWasmBackend, type FlowWasmBackend } from "../wasm/backend.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Stringify a column value the way Polars' `cast(Utf8, strict=False)` would
 * for a numeric column feeding a string-parser transform: numbers become
 * their decimal repr, strings pass through unchanged. */
function toInputString(v: ColumnValue): string | null {
  if (v === null) return null;
  return typeof v === "string" ? v : String(v);
}

/** Map a column through a string->number|undefined parser fn (the Rust
 * `Option<f64>`/`Option<i64>` -- `undefined` mirrors `None`); null passes
 * through, `undefined` maps to `null` in the output column. */
function mapParser(
  values: readonly ColumnValue[],
  fn: (s: string) => number | undefined,
): ColumnValue[] {
  return values.map((v) => {
    const s = toInputString(v);
    if (s === null) return null;
    const r = fn(s);
    return r === undefined ? null : r;
  });
}

/** Map a column of numbers through a numeric-array-op fn; non-null,
 * non-numeric values are coerced via `Number()` (NaN passes through as
 * null, matching "not really numeric input" gracefully). */
function mapNumericOp(
  values: readonly ColumnValue[],
  fn: (x: number) => number,
): ColumnValue[] {
  return values.map((v) => {
    if (v === null) return null;
    const num = typeof v === "number" ? v : Number(v);
    if (Number.isNaN(num)) return null;
    return fn(num);
  });
}

// ---------------------------------------------------------------------------
// currency_strip (series, string|numeric, 50)
//
// Pure-TS reference for goldenflow-core's `numeric::currency_strip` kernel.
// Strip everything except ASCII digits, `.`, and `-`, then parse as a
// number. `undefined` (-> null) on parse failure.
// ---------------------------------------------------------------------------

function currencyStripTs(s: string): number | undefined {
  const filtered = s.replace(/[^0-9.\-]/g, "");
  if (filtered === "") return undefined;
  const n = Number(filtered);
  return Number.isNaN(n) ? undefined : n;
}

function currencyStrip(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapParser(values, backend ? (s) => backend.currencyStrip(s) : currencyStripTs);
}

registerTransform(
  { name: "currency_strip", inputTypes: ["string", "numeric"], priority: 50, mode: "series" },
  currencyStrip,
);

// ---------------------------------------------------------------------------
// percentage_normalize (series, string|numeric, 50)
//
// Pure-TS reference for goldenflow-core's `numeric::percentage_normalize`
// kernel. Trim, strip trailing `%`, trim again, parse as a number, divide by
// 100. `undefined` (-> null) on parse failure.
// ---------------------------------------------------------------------------

function percentageNormalizeTs(s: string): number | undefined {
  let v = s.trim();
  v = v.replace(/%+$/, "");
  v = v.trim();
  if (v === "") return undefined;
  const n = Number(v);
  return Number.isNaN(n) ? undefined : n / 100;
}

function percentageNormalize(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapParser(
    values,
    backend ? (s) => backend.percentageNormalize(s) : percentageNormalizeTs,
  );
}

registerTransform(
  { name: "percentage_normalize", inputTypes: ["string", "numeric"], priority: 50, mode: "series" },
  percentageNormalize,
);

// ---------------------------------------------------------------------------
// round (series, numeric, 40, param: n=2)
//
// Round-half-away-from-zero at the n-th decimal, via multiply/round/divide
// -- the SAME formula as goldenflow-core's `round_f64` kernel. Deliberately
// NOT `Math.round` (which rounds half toward +Infinity, not away from zero).
// ---------------------------------------------------------------------------

function roundValueTs(x: number, n: number): number {
  const factor = Math.pow(10, n);
  const scaled = x * factor;
  const rounded = scaled >= 0 ? Math.floor(scaled + 0.5) : Math.ceil(scaled - 0.5);
  return rounded / factor;
}

function roundTransform(values: readonly ColumnValue[], n: unknown = 2): ColumnValue[] {
  const decimals = typeof n === "number" ? n : Number(n) || 2;
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapNumericOp(
    values,
    backend ? (x) => backend.roundValue(x, decimals) : (x) => roundValueTs(x, decimals),
  );
}

registerTransform(
  { name: "round", inputTypes: ["numeric"], priority: 40, mode: "series" },
  roundTransform,
);

// ---------------------------------------------------------------------------
// clamp (series, numeric, 40, params: min_val=0, max_val=1)
// ---------------------------------------------------------------------------

function clampValueTs(x: number, minVal: number, maxVal: number): number {
  if (x < minVal) return minVal;
  if (x > maxVal) return maxVal;
  return x;
}

function clamp(values: readonly ColumnValue[], minVal: unknown = 0, maxVal: unknown = 1): ColumnValue[] {
  const lo = typeof minVal === "number" ? minVal : Number(minVal) || 0;
  const hi = typeof maxVal === "number" ? maxVal : Number(maxVal) || 1;
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapNumericOp(
    values,
    backend ? (x) => backend.clampValue(x, lo, hi) : (x) => clampValueTs(x, lo, hi),
  );
}

registerTransform(
  { name: "clamp", inputTypes: ["numeric"], priority: 40, mode: "series" },
  clamp,
);

// ---------------------------------------------------------------------------
// to_integer (series, string|numeric, 45)
//
// Pure-TS reference for goldenflow-core's `numeric::to_integer` kernel.
// Parse as a number, truncate toward zero. `undefined` (-> null) on parse
// failure.
// ---------------------------------------------------------------------------

function toIntegerTs(s: string): number | undefined {
  const trimmed = s.trim();
  if (trimmed === "") return undefined;
  const n = Number(trimmed);
  return Number.isNaN(n) ? undefined : Math.trunc(n);
}

function toInteger(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapParser(values, backend ? (s) => backend.toInteger(s) : toIntegerTs);
}

registerTransform(
  { name: "to_integer", inputTypes: ["string", "numeric"], priority: 45, mode: "series" },
  toInteger,
);

// ---------------------------------------------------------------------------
// W5 numeric breadth: roman_to_int / ordinal_to_int / fraction_to_decimal
// ---------------------------------------------------------------------------

const ROMAN_VAL: Record<string, number> = { I: 1, V: 5, X: 10, L: 50, C: 100, D: 500, M: 1000 };
const ROMAN_TABLE: [number, string][] = [
  [1000, "M"], [900, "CM"], [500, "D"], [400, "CD"], [100, "C"],
  [90, "XC"], [50, "L"], [40, "XL"], [10, "X"], [9, "IX"],
  [5, "V"], [4, "IV"], [1, "I"],
];

function intToRoman(n: number): string {
  let out = "";
  for (const [v, sym] of ROMAN_TABLE) while (n >= v) { out += sym; n -= v; }
  return out;
}

function romanToIntTs(s: string): number | undefined {
  const t = s.trim().toUpperCase();
  if (t === "") return undefined;
  const chars = [...t];
  let total = 0;
  for (let i = 0; i < chars.length; i++) {
    const cur = ROMAN_VAL[chars[i]!];
    if (cur === undefined) return undefined;
    const nxt = i + 1 < chars.length ? (ROMAN_VAL[chars[i + 1]!] ?? 0) : 0;
    total += cur < nxt ? -cur : cur;
  }
  if (total < 1 || total > 3999) return undefined;
  return intToRoman(total) === t ? total : undefined;
}

function ordinalSuffix(n: number): string {
  const m = n % 100;
  if (m >= 11 && m <= 13) return "th";
  switch (n % 10) {
    case 1: return "st";
    case 2: return "nd";
    case 3: return "rd";
    default: return "th";
  }
}

function ordinalToIntTs(s: string): number | undefined {
  const t = s.trim().toLowerCase();
  let digits = "";
  for (const c of t) {
    if (c >= "0" && c <= "9") digits += c;
    else break;
  }
  if (digits === "") return undefined;
  const n = parseInt(digits, 10);
  return t.slice(digits.length) === ordinalSuffix(n) ? n : undefined;
}

/** Strict f64 parse (rejects empty / non-numeric), mirroring Rust `parse::<f64>`. */
function parseF64(s: string): number | undefined {
  const t = s.trim();
  if (t === "") return undefined;
  const n = Number(t);
  return Number.isNaN(n) ? undefined : n;
}

function parseFraction(s: string): number | undefined {
  const idx = s.indexOf("/");
  if (idx === -1) return undefined;
  const num = parseF64(s.slice(0, idx));
  const den = parseF64(s.slice(idx + 1));
  if (num === undefined || den === undefined || den === 0) return undefined;
  return num / den;
}

function fractionToDecimalTs(s: string): number | undefined {
  const t = s.trim();
  if (t === "") return undefined;
  const wsIdx = t.search(/\s/);
  if (wsIdx !== -1) {
    const fracS = t.slice(wsIdx).trim();
    if (fracS.includes("/")) {
      const wholeS = t.slice(0, wsIdx).trim();
      if (!/^-?\d+$/.test(wholeS)) return undefined; // strict i64, matches Rust
      const whole = parseInt(wholeS, 10);
      const frac = parseFraction(fracS);
      if (frac === undefined) return undefined;
      return whole < 0 ? whole - frac : whole + frac;
    }
    return undefined;
  }
  if (t.includes("/")) return parseFraction(t);
  return parseF64(t);
}

function romanToInt(values: readonly ColumnValue[]): ColumnValue[] {
  const b: FlowWasmBackend | null = getFlowWasmBackend();
  return mapParser(values, b ? (s) => b.romanToInt(s) : romanToIntTs);
}
function ordinalToInt(values: readonly ColumnValue[]): ColumnValue[] {
  const b: FlowWasmBackend | null = getFlowWasmBackend();
  return mapParser(values, b ? (s) => b.ordinalToInt(s) : ordinalToIntTs);
}
function fractionToDecimal(values: readonly ColumnValue[]): ColumnValue[] {
  const b: FlowWasmBackend | null = getFlowWasmBackend();
  return mapParser(values, b ? (s) => b.fractionToDecimal(s) : fractionToDecimalTs);
}

registerTransform(
  { name: "roman_to_int", inputTypes: ["string"], priority: 40, mode: "series" },
  romanToInt,
);
registerTransform(
  { name: "ordinal_to_int", inputTypes: ["string"], priority: 40, mode: "series" },
  ordinalToInt,
);
registerTransform(
  { name: "fraction_to_decimal", inputTypes: ["string", "numeric"], priority: 40, mode: "series" },
  fractionToDecimal,
);

// ---------------------------------------------------------------------------
// abs_value (series, numeric, 40)
// ---------------------------------------------------------------------------

function absValue(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapNumericOp(values, backend ? (x) => backend.absValue(x) : Math.abs);
}

registerTransform(
  { name: "abs_value", inputTypes: ["numeric"], priority: 40, mode: "series" },
  absValue,
);

// ---------------------------------------------------------------------------
// fill_zero (series, numeric, 35) — null -> 0
// ---------------------------------------------------------------------------

function fillZero(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return values.map((v) => {
    if (v !== null) return v;
    return backend ? backend.fillZero(undefined) : 0;
  });
}

registerTransform(
  { name: "fill_zero", inputTypes: ["numeric"], priority: 35, mode: "series" },
  fillZero,
);

// ---------------------------------------------------------------------------
// comma_decimal (series, string|numeric, 48) — European "1.234,56" -> 1234.56
//
// Pure-TS reference for goldenflow-core's `numeric::comma_decimal` kernel.
// If the (trimmed) input has no comma, parse as-is; else treat dots as
// thousands separators and the comma as the decimal point. `undefined`
// (-> null) on parse failure.
// ---------------------------------------------------------------------------

function commaDecimalTs(s: string): number | undefined {
  const trimmed = s.trim();
  if (!trimmed.includes(",")) {
    if (trimmed === "") return undefined;
    const n = Number(trimmed);
    return Number.isNaN(n) ? undefined : n;
  }
  const converted = trimmed.replace(/\./g, "").replace(",", ".");
  const n = Number(converted);
  return Number.isNaN(n) ? undefined : n;
}

function commaDecimal(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapParser(values, backend ? (s) => backend.commaDecimal(s) : commaDecimalTs);
}

registerTransform(
  { name: "comma_decimal", inputTypes: ["string", "numeric"], priority: 48, mode: "series" },
  commaDecimal,
);

// ---------------------------------------------------------------------------
// scientific_to_decimal (series, string|numeric, 45)
//
// Pure-TS reference for goldenflow-core's `numeric::scientific_to_decimal`
// kernel. Trim, parse as a number. `undefined` (-> null) on parse failure.
// ---------------------------------------------------------------------------

function scientificToDecimalTs(s: string): number | undefined {
  const trimmed = s.trim();
  if (trimmed === "") return undefined;
  const n = Number(trimmed);
  return Number.isNaN(n) ? undefined : n;
}

function scientificToDecimal(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapParser(
    values,
    backend ? (s) => backend.scientificToDecimal(s) : scientificToDecimalTs,
  );
}

registerTransform(
  { name: "scientific_to_decimal", inputTypes: ["string", "numeric"], priority: 45, mode: "series" },
  scientificToDecimal,
);

// ---------------------------------------------------------------------------
// Pure-TS single-value exports (cross-surface byte/value-parity harness)
//
// Bypass the wasm-dispatch wrappers above so a parity test can assert the
// pure-TS path independently of whatever backend is currently registered.
// ---------------------------------------------------------------------------

export {
  currencyStripTs,
  percentageNormalizeTs,
  toIntegerTs,
  commaDecimalTs,
  scientificToDecimalTs,
  roundValueTs,
  clampValueTs,
  romanToIntTs,
  ordinalToIntTs,
  fractionToDecimalTs,
};
