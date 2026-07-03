/**
 * Identifier transforms — ported from goldenflow/transforms/identifiers.py
 * Side-effect module: registers identifier transforms on import.
 *
 * The cc/iban/isbn/ean/vat family below is a byte-for-byte port of the
 * Python pure-TS reference (`_cc_validate_py` et al. in
 * `goldenflow/transforms/identifiers.py`), which is itself proven
 * byte-identical to the Rust `goldenflow-core::identifiers` kernels (Wave
 * 0b's parity corpus). Each transform dispatches to the opt-in WASM backend
 * (`FlowWasmBackend`, a thin wasm-bindgen shim over the SAME Rust kernel)
 * when `enableWasm()` has succeeded; otherwise it runs the pure-TS
 * implementation below. Pure-TS is the default.
 */

import type { ColumnValue } from "../types.js";
import { registerTransform } from "./registry.js";
import { getFlowWasmBackend, type FlowWasmBackend } from "../wasm/backend.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function mapStrings(
  values: readonly ColumnValue[],
  fn: (s: string) => string,
): ColumnValue[] {
  return values.map((v) => {
    if (v === null || typeof v !== "string") return v;
    return fn(v);
  });
}

/** Map a string column through a boolean-returning identifier fn; nulls and
 * non-strings pass through unchanged (mirrors polars `map_elements` on an
 * Optional[str] input -- `None` in, `None` out). */
function mapToBool(
  values: readonly ColumnValue[],
  fn: (s: string) => boolean,
): ColumnValue[] {
  return values.map((v) => {
    if (v === null || typeof v !== "string") return v;
    return fn(v);
  });
}

/** Map a string column through a format/normalize/mask fn returning
 * `string | undefined` (the Rust `Option<String>` -- `undefined` mirrors
 * `None`); nulls and non-strings pass through unchanged, `undefined` maps to
 * `null` in the output column. */
function mapToStringOrNull(
  values: readonly ColumnValue[],
  fn: (s: string) => string | undefined,
): ColumnValue[] {
  return values.map((v) => {
    if (v === null || typeof v !== "string") return v;
    const r = fn(v);
    return r === undefined ? null : r;
  });
}

function extractDigits(val: string): string {
  return val.replace(/\D/g, "");
}

// ---------------------------------------------------------------------------
// ASCII char-class helpers -- mirror Python's `c.isascii() and c.isdigit()`
// / `isalpha()` / `isalnum()` combinations used throughout the Rust-parity
// identifier kernels below (restricts to the ASCII 0-9 / A-Za-z bands, NOT
// Python's broader Unicode isdigit()/isalpha()).
// ---------------------------------------------------------------------------

function isAsciiDigitChar(c: string): boolean {
  return c.length === 1 && c >= "0" && c <= "9";
}

function isAsciiAlphaChar(c: string): boolean {
  return c.length === 1 && ((c >= "A" && c <= "Z") || (c >= "a" && c <= "z"));
}

function isAsciiAlnumChar(c: string): boolean {
  return isAsciiDigitChar(c) || isAsciiAlphaChar(c);
}

// ---------------------------------------------------------------------------
// Payment-card (Luhn) identifiers
//
// Pure-TS reference for goldenflow-core's `identifiers::luhn` kernel. MUST
// reproduce the Rust/Python kernel byte-for-byte -- same separator strip,
// same Luhn, same 13-19 length band, same Amex 4-6-5 vs 4-4-4-4... grouping,
// same mask.
// ---------------------------------------------------------------------------

/** Remove ASCII spaces, '-' and '.' -- mirrors Rust `strip_sep`. */
function ccStripSep(val: string): string {
  return val.replace(/[ \-.]/g, "");
}

function ccNormalizedDigits(val: string): string | undefined {
  const d = ccStripSep(val);
  if (d.length === 0) return undefined;
  for (const c of d) {
    if (!isAsciiDigitChar(c)) return undefined;
  }
  return d;
}

function luhnOk(digits: string): boolean {
  let total = 0;
  let dbl = false;
  for (let i = digits.length - 1; i >= 0; i--) {
    let d = (digits[i] ?? "").charCodeAt(0) - 48;
    if (dbl) {
      d *= 2;
      if (d > 9) d -= 9;
    }
    total += d;
    dbl = !dbl;
  }
  return total % 10 === 0;
}

function ccValidateTs(val: string): boolean {
  const d = ccNormalizedDigits(val);
  if (d === undefined) return false;
  return d.length >= 13 && d.length <= 19 && luhnOk(d);
}

function ccGroup(d: string, sizes: readonly number[]): string {
  const out: string[] = [];
  let i = 0;
  for (const n of sizes) {
    if (i >= d.length) break;
    const end = Math.min(i + n, d.length);
    out.push(d.slice(i, end));
    i = end;
  }
  while (i < d.length) {
    const end = Math.min(i + 4, d.length);
    out.push(d.slice(i, end));
    i = end;
  }
  return out.join(" ");
}

function ccFormatTs(val: string): string | undefined {
  const d = ccNormalizedDigits(val);
  if (d === undefined) return undefined;
  if (!(d.length >= 13 && d.length <= 19 && luhnOk(d))) return undefined;
  const sizes =
    d.length === 15 && (d.startsWith("34") || d.startsWith("37"))
      ? [4, 6, 5]
      : [4, 4, 4, 4, 4];
  return ccGroup(d, sizes);
}

function ccMaskTs(val: string): string | undefined {
  const d = ccNormalizedDigits(val);
  if (d === undefined) return undefined;
  if (!(d.length >= 13 && d.length <= 19)) return undefined;
  return "*".repeat(d.length - 4) + d.slice(-4);
}

function ccValidate(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapToBool(values, backend ? (s) => backend.ccValidate(s) : ccValidateTs);
}

registerTransform(
  {
    name: "cc_validate",
    inputTypes: ["identifier", "string"],
    autoApply: false,
    priority: 50,
    mode: "series",
  },
  ccValidate,
);

function ccFormat(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapToStringOrNull(values, backend ? (s) => backend.ccFormat(s) : ccFormatTs);
}

registerTransform(
  {
    name: "cc_format",
    inputTypes: ["identifier", "string"],
    autoApply: false,
    priority: 50,
    mode: "series",
  },
  ccFormat,
);

function ccMask(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapToStringOrNull(values, backend ? (s) => backend.ccMask(s) : ccMaskTs);
}

registerTransform(
  {
    name: "cc_mask",
    inputTypes: ["identifier", "string"],
    autoApply: false,
    priority: 50,
    mode: "series",
  },
  ccMask,
);

// ---------------------------------------------------------------------------
// IBAN (ISO 7064 mod-97) identifiers
//
// Pure-TS reference for goldenflow-core's `identifiers::iban` kernel. MUST
// reproduce the Rust/Python kernel byte-for-byte -- same separator strip +
// uppercase, same structural checks, same mod-97 fold, same 4-char grouping.
// ---------------------------------------------------------------------------

/** Strip separators + uppercase -- mirrors Rust `strip_sep` + upper. */
function ibanNormalize(val: string): string {
  return ccStripSep(val).toUpperCase();
}

/** ISO 7064 mod-97 check: move the first 4 chars to the end, fold the
 * resulting decimal string mod 97 digit-by-digit (letters -> two-digit
 * A=10..Z=35 value folded in one step), require remainder 1. */
function ibanMod97Ok(t: string): boolean {
  const rearranged = t.slice(4) + t.slice(0, 4);
  let acc = 0;
  for (const c of rearranged) {
    if (isAsciiDigitChar(c)) {
      acc = (acc * 10 + (c.charCodeAt(0) - 48)) % 97;
    } else {
      const v = c.charCodeAt(0) - 65 + 10; // 'A' = 65
      acc = (acc * 100 + v) % 97;
    }
  }
  return acc === 1;
}

function ibanValidateTs(val: string): boolean {
  const t = ibanNormalize(val);
  if (t.length < 15 || t.length > 34) return false;
  if (!(isAsciiAlphaChar(t[0] ?? "") && isAsciiAlphaChar(t[1] ?? ""))) return false;
  if (!(isAsciiDigitChar(t[2] ?? "") && isAsciiDigitChar(t[3] ?? ""))) return false;
  for (let i = 4; i < t.length; i++) {
    if (!isAsciiAlnumChar(t[i] ?? "")) return false;
  }
  return ibanMod97Ok(t);
}

function ibanGroup4(t: string): string {
  const parts: string[] = [];
  for (let i = 0; i < t.length; i += 4) {
    parts.push(t.slice(i, i + 4));
  }
  return parts.join(" ");
}

function ibanFormatTs(val: string): string | undefined {
  if (!ibanValidateTs(val)) return undefined;
  return ibanGroup4(ibanNormalize(val));
}

function ibanValidate(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapToBool(values, backend ? (s) => backend.ibanValidate(s) : ibanValidateTs);
}

registerTransform(
  {
    name: "iban_validate",
    inputTypes: ["identifier", "string"],
    autoApply: false,
    priority: 50,
    mode: "series",
  },
  ibanValidate,
);

function ibanFormat(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapToStringOrNull(values, backend ? (s) => backend.ibanFormat(s) : ibanFormatTs);
}

registerTransform(
  {
    name: "iban_format",
    inputTypes: ["identifier", "string"],
    autoApply: false,
    priority: 50,
    mode: "series",
  },
  ibanFormat,
);

// ---------------------------------------------------------------------------
// ISBN (10/13 checksum) identifiers
//
// Pure-TS reference for goldenflow-core's `identifiers::isbn` kernel. MUST
// reproduce the Rust/Python kernel byte-for-byte -- same separator strip +
// trailing-X uppercase, same ISBN-10/13 checksums, same ISBN-10 -> ISBN-13
// conversion.
// ---------------------------------------------------------------------------

/** Strip separators; uppercase a trailing 'x' -- mirrors Rust
 * `normalize_case`. */
function isbnNormalizeCase(val: string): string {
  let t = ccStripSep(val);
  if (t.length > 0 && t[t.length - 1] === "x") {
    t = t.slice(0, -1) + "X";
  }
  return t;
}

function isbn10ChecksumOk(t: string): boolean {
  if (t.length !== 10) return false;
  for (let i = 0; i < 9; i++) {
    if (!isAsciiDigitChar(t[i] ?? "")) return false;
  }
  const last = t[9] ?? "";
  if (!isAsciiDigitChar(last) && last !== "X") return false;
  let total = 0;
  for (let i = 0; i < 10; i++) {
    const c = t[i] ?? "";
    const d = c === "X" ? 10 : c.charCodeAt(0) - 48;
    total += d * (10 - i);
  }
  return total % 11 === 0;
}

function isbn13ChecksumOk(t: string): boolean {
  if (t.length !== 13) return false;
  for (const c of t) {
    if (!isAsciiDigitChar(c)) return false;
  }
  let total = 0;
  for (let i = 0; i < 13; i++) {
    const c = t[i] ?? "";
    const d = c.charCodeAt(0) - 48;
    const weight = i % 2 === 0 ? 1 : 3;
    total += d * weight;
  }
  return total % 10 === 0;
}

function isbn13CheckDigit(twelve: string): string {
  let total = 0;
  for (let i = 0; i < twelve.length; i++) {
    const c = twelve[i] ?? "";
    const d = c.charCodeAt(0) - 48;
    const weight = i % 2 === 0 ? 1 : 3;
    total += d * weight;
  }
  return String((10 - (total % 10)) % 10);
}

function isbnValidateTs(val: string): boolean {
  const t = isbnNormalizeCase(val);
  if (t.length === 10) return isbn10ChecksumOk(t);
  if (t.length === 13) return isbn13ChecksumOk(t);
  return false;
}

function isbnNormalizeTs(val: string): string | undefined {
  const t = isbnNormalizeCase(val);
  if (t.length === 10) {
    if (!isbn10ChecksumOk(t)) return undefined;
    const twelve = "978" + t.slice(0, 9);
    return twelve + isbn13CheckDigit(twelve);
  }
  if (t.length === 13) {
    if (!isbn13ChecksumOk(t)) return undefined;
    return t;
  }
  return undefined;
}

function isbnValidate(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapToBool(values, backend ? (s) => backend.isbnValidate(s) : isbnValidateTs);
}

registerTransform(
  {
    name: "isbn_validate",
    inputTypes: ["identifier", "string"],
    autoApply: false,
    priority: 50,
    mode: "series",
  },
  isbnValidate,
);

function isbnNormalize(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapToStringOrNull(
    values,
    backend ? (s) => backend.isbnNormalize(s) : isbnNormalizeTs,
  );
}

registerTransform(
  {
    name: "isbn_normalize",
    inputTypes: ["identifier", "string"],
    autoApply: false,
    priority: 50,
    mode: "series",
  },
  isbnNormalize,
);

// ---------------------------------------------------------------------------
// EAN/UPC (GTIN mod-10) identifiers
//
// Pure-TS reference for goldenflow-core's `identifiers::ean` kernel. MUST
// reproduce the Rust/Python kernel byte-for-byte -- same separator strip,
// same length band (8/12/13), same GTIN mod-10 check. Validate-only -- no
// format/normalize.
// ---------------------------------------------------------------------------

function eanGtinChecksumOk(t: string): boolean {
  for (const c of t) {
    if (!isAsciiDigitChar(c)) return false;
  }
  const data = t.slice(0, -1);
  const check = t[t.length - 1] ?? "";
  const checkDigit = check.charCodeAt(0) - 48;
  const reversedData = [...data].reverse();
  let total = 0;
  for (let i = 0; i < reversedData.length; i++) {
    const c = reversedData[i] ?? "";
    const d = c.charCodeAt(0) - 48;
    const weight = i % 2 === 0 ? 3 : 1;
    total += d * weight;
  }
  const computed = (10 - (total % 10)) % 10;
  return computed === checkDigit;
}

function eanValidateTs(val: string): boolean {
  const t = ccStripSep(val);
  if (t.length !== 8 && t.length !== 12 && t.length !== 13) return false;
  return eanGtinChecksumOk(t);
}

function eanValidate(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapToBool(values, backend ? (s) => backend.eanValidate(s) : eanValidateTs);
}

registerTransform(
  {
    name: "ean_validate",
    inputTypes: ["identifier", "string"],
    autoApply: false,
    priority: 50,
    mode: "series",
  },
  eanValidate,
);

// ---------------------------------------------------------------------------
// EU VAT identifiers (bounded scope)
//
// Pure-TS reference for goldenflow-core's `identifiers::vat` kernel. MUST
// reproduce the Rust/Python kernel byte-for-byte -- same separator strip +
// uppercase, same per-prefix structural rules, same DE/IT checksums.
//
// CHECKSUM COVERAGE: DE, IT (structural-only for all other supported
// prefixes). This is a deliberate, documented bound (Wave 0b, Task 5): all
// 27 EU member-state VAT prefixes below are validated STRUCTURALLY (country
// prefix + length + per-position charset), but only Germany (DE, ISO 7064
// mod 11,10) and Italy (IT, partita IVA Luhn) additionally run a checksum.
// Unsupported/unknown prefixes (including a bare "GR" -- Greece's VAT prefix
// is the well-known quirk "EL") -> false.
// ---------------------------------------------------------------------------

/** Per-prefix structural rule: array of per-position classes ("D"igit,
 * "A"lpha, "N"alnum, or a literal char) for one or more fixed-length
 * variants. */
const VAT_FIXED_RULES: Readonly<Record<string, readonly (readonly string[])[]>> = {
  AT: [["U", "D", "D", "D", "D", "D", "D", "D", "D"]],
  BE: [Array(10).fill("D") as string[]],
  CY: [[...(Array(8).fill("D") as string[]), "A"]],
  DE: [Array(9).fill("D") as string[]],
  DK: [Array(8).fill("D") as string[]],
  EE: [Array(9).fill("D") as string[]],
  EL: [Array(9).fill("D") as string[]],
  ES: [["N", ...(Array(7).fill("D") as string[]), "N"]],
  FI: [Array(8).fill("D") as string[]],
  FR: [["N", "N", ...(Array(9).fill("D") as string[])]],
  HR: [Array(11).fill("D") as string[]],
  HU: [Array(8).fill("D") as string[]],
  IE: [
    ["D", "N", "D", "D", "D", "D", "D", "A"],
    ["D", "N", "D", "D", "D", "D", "D", "A", "A"],
  ],
  IT: [Array(11).fill("D") as string[]],
  LT: [Array(9).fill("D") as string[], Array(12).fill("D") as string[]],
  LU: [Array(8).fill("D") as string[]],
  LV: [Array(11).fill("D") as string[]],
  MT: [Array(8).fill("D") as string[]],
  NL: [[...(Array(9).fill("D") as string[]), "B", ...(Array(2).fill("D") as string[])]],
  PL: [Array(10).fill("D") as string[]],
  PT: [Array(9).fill("D") as string[]],
  SE: [Array(12).fill("D") as string[]],
  SI: [Array(8).fill("D") as string[]],
  SK: [Array(10).fill("D") as string[]],
};

const VAT_DIGITS_RULES: Readonly<Record<string, readonly [number, number]>> = {
  BG: [9, 10],
  CZ: [8, 10],
  RO: [2, 10],
};

function vatPosOk(pos: string, c: string): boolean {
  if (pos === "D") return isAsciiDigitChar(c);
  if (pos === "A") return isAsciiAlphaChar(c);
  if (pos === "N") return isAsciiAlnumChar(c);
  return c === pos; // literal char (e.g. NL's "B", AT's "U")
}

function vatFixedOk(pattern: readonly string[], suffix: string): boolean {
  if (suffix.length !== pattern.length) return false;
  for (let i = 0; i < pattern.length; i++) {
    if (!vatPosOk(pattern[i] ?? "", suffix[i] ?? "")) return false;
  }
  return true;
}

function vatStructuralOk(prefix: string, suffix: string): boolean {
  const fixedRules = VAT_FIXED_RULES[prefix];
  if (fixedRules !== undefined) {
    return fixedRules.some((p) => vatFixedOk(p, suffix));
  }
  const digitsRule = VAT_DIGITS_RULES[prefix];
  if (digitsRule !== undefined) {
    const [lo, hi] = digitsRule;
    if (suffix.length < lo || suffix.length > hi) return false;
    for (const c of suffix) {
      if (!isAsciiDigitChar(c)) return false;
    }
    return true;
  }
  return false;
}

function vatDeChecksumOk(digits: string): boolean {
  if (digits.length !== 9) return false;
  const d: number[] = [];
  for (const c of digits) d.push(c.charCodeAt(0) - 48);
  let p = 10;
  for (let i = 0; i < 8; i++) {
    let m = ((d[i] ?? 0) + p) % 10;
    if (m === 0) m = 10;
    p = (2 * m) % 11;
  }
  let check = 11 - p;
  if (check === 10) check = 0;
  return check === (d[8] ?? -1);
}

function vatItChecksumOk(digits: string): boolean {
  if (digits.length !== 11) return false;
  const d: number[] = [];
  for (const c of digits) d.push(c.charCodeAt(0) - 48);
  let total = 0;
  for (let i = 0; i < 10; i++) {
    const di = d[i] ?? 0;
    if (i % 2 === 0) {
      total += di;
    } else {
      const x = di * 2;
      total += x > 9 ? x - 9 : x;
    }
  }
  const check = (10 - (total % 10)) % 10;
  return check === (d[10] ?? -1);
}

function vatSplitPrefix(val: string): readonly [string, string] | undefined {
  const t = ccStripSep(val).toUpperCase();
  if (t.length < 3) return undefined;
  if (!(isAsciiAlphaChar(t[0] ?? "") && isAsciiAlphaChar(t[1] ?? ""))) return undefined;
  return [t.slice(0, 2), t.slice(2)];
}

function vatValidateTs(val: string): boolean {
  const split = vatSplitPrefix(val);
  if (split === undefined) return false;
  const [prefix, suffix] = split;
  if (!vatStructuralOk(prefix, suffix)) return false;
  if (prefix === "DE") return vatDeChecksumOk(suffix);
  if (prefix === "IT") return vatItChecksumOk(suffix);
  return true;
}

function vatFormatTs(val: string): string | undefined {
  if (!vatValidateTs(val)) return undefined;
  return ccStripSep(val).toUpperCase();
}

function vatValidate(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapToBool(values, backend ? (s) => backend.vatValidate(s) : vatValidateTs);
}

registerTransform(
  {
    name: "vat_validate",
    inputTypes: ["identifier", "string"],
    autoApply: false,
    priority: 50,
    mode: "series",
  },
  vatValidate,
);

function vatFormat(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapToStringOrNull(values, backend ? (s) => backend.vatFormat(s) : vatFormatTs);
}

registerTransform(
  {
    name: "vat_format",
    inputTypes: ["identifier", "string"],
    autoApply: false,
    priority: 50,
    mode: "series",
  },
  vatFormat,
);

// ---------------------------------------------------------------------------
// ssn_format (series, ssn|string, 50)
// ---------------------------------------------------------------------------

function ssnFormat(values: readonly ColumnValue[]): ColumnValue[] {
  return mapStrings(values, (s) => {
    const digits = extractDigits(s);
    if (digits.length !== 9) return s; // preserve invalid
    return `${digits.slice(0, 3)}-${digits.slice(3, 5)}-${digits.slice(5)}`;
  });
}

registerTransform(
  { name: "ssn_format", inputTypes: ["ssn", "string"], priority: 50, mode: "series" },
  ssnFormat,
);

// ---------------------------------------------------------------------------
// ssn_mask (series, ssn|string, 50)
// ---------------------------------------------------------------------------

function ssnMask(values: readonly ColumnValue[]): ColumnValue[] {
  return mapStrings(values, (s) => {
    const digits = extractDigits(s);
    if (digits.length !== 9) return s; // preserve invalid
    return `***-**-${digits.slice(5)}`;
  });
}

registerTransform(
  { name: "ssn_mask", inputTypes: ["ssn", "string"], priority: 50, mode: "series" },
  ssnMask,
);

// ---------------------------------------------------------------------------
// ein_format (series, ein|string, 50)
// ---------------------------------------------------------------------------

function einFormat(values: readonly ColumnValue[]): ColumnValue[] {
  return mapStrings(values, (s) => {
    const digits = extractDigits(s);
    if (digits.length !== 9) return s; // preserve invalid
    return `${digits.slice(0, 2)}-${digits.slice(2)}`;
  });
}

registerTransform(
  { name: "ein_format", inputTypes: ["ein", "string"], priority: 50, mode: "series" },
  einFormat,
);

// ---------------------------------------------------------------------------
// Pure-TS single-value exports (cross-surface byte-parity harness)
//
// Bypass the wasm-dispatch wrappers above (`ccValidate` et al., which take a
// ColumnValue[] and consult `getFlowWasmBackend()` internally) so a parity
// test can assert the pure-TS path independently of whatever backend is
// currently registered. The wasm leg of that same test calls
// `getFlowWasmBackend()!.ccValidate(s)` etc. directly (see
// `tests/parity/identifiers.parity.test.ts`).
// ---------------------------------------------------------------------------

export {
  ccValidateTs,
  ccFormatTs,
  ccMaskTs,
  ibanValidateTs,
  ibanFormatTs,
  isbnValidateTs,
  isbnNormalizeTs,
  eanValidateTs,
  vatValidateTs,
  vatFormatTs,
};
