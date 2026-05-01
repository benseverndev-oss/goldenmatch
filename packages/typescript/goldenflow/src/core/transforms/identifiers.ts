/**
 * Identifier transforms — ported from goldenflow/transforms/identifiers.py
 * Side-effect module: registers 3 identifier transforms on import.
 */

import type { ColumnValue } from "../types.js";
import { registerTransform } from "./registry.js";

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

function extractDigits(val: string): string {
  return val.replace(/\D/g, "");
}

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
