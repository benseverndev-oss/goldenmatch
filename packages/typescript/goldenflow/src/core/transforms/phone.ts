/**
 * Phone transforms — ported from goldenflow/transforms/phone.py
 * Side-effect module: registers 5 phone transforms on import.
 *
 * Edge-safe implementation: no phonenumbers library dependency.
 * Handles US numbers (10 or 11 digits starting with 1).
 */

import type { ColumnValue } from "../types.js";
import { registerTransform } from "./registry.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Extract all digits from a string. */
function extractDigits(s: string): string {
  return s.replace(/\D/g, "");
}

/**
 * Normalize a US phone string to exactly 10 digits (without country code).
 * Returns null if the input does not look like a valid US number.
 */
function normalizeUsDigits(s: string): string | null {
  const digits = extractDigits(s);
  if (digits.length === 10) return digits;
  if (digits.length === 11 && digits[0] === "1") return digits.slice(1);
  return null;
}

// ---------------------------------------------------------------------------
// phone_e164 (50, auto_apply, phone)
// ---------------------------------------------------------------------------

function phoneE164(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null || typeof v !== "string") return v;
    const digits = normalizeUsDigits(v);
    if (digits === null) return v;
    return `+1${digits}`;
  });
}

registerTransform(
  { name: "phone_e164", inputTypes: ["phone"], autoApply: true, priority: 50, mode: "series" },
  phoneE164,
);

// ---------------------------------------------------------------------------
// phone_national (50, phone)
// ---------------------------------------------------------------------------

function phoneNational(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null || typeof v !== "string") return v;
    const digits = normalizeUsDigits(v);
    if (digits === null) return v;
    return `(${digits.slice(0, 3)}) ${digits.slice(3, 6)}-${digits.slice(6)}`;
  });
}

registerTransform(
  { name: "phone_national", inputTypes: ["phone"], priority: 50, mode: "series" },
  phoneNational,
);

// ---------------------------------------------------------------------------
// phone_digits (50, phone)
// ---------------------------------------------------------------------------

function phoneDigits(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null || typeof v !== "string") return v;
    return extractDigits(v);
  });
}

registerTransform(
  { name: "phone_digits", inputTypes: ["phone"], priority: 50, mode: "series" },
  phoneDigits,
);

// ---------------------------------------------------------------------------
// phone_validate (60, phone)
// ---------------------------------------------------------------------------

function phoneValidate(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null || typeof v !== "string") return v;
    const digits = extractDigits(v);
    return digits.length === 10 || (digits.length === 11 && digits[0] === "1");
  });
}

registerTransform(
  { name: "phone_validate", inputTypes: ["phone"], priority: 60, mode: "series" },
  phoneValidate,
);

// ---------------------------------------------------------------------------
// phone_country_code (45, phone)
// ---------------------------------------------------------------------------

function phoneCountryCode(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null || typeof v !== "string") return v;
    const digits = extractDigits(v);
    // Recognise US numbers: 10 digits or 11 starting with 1
    if (digits.length === 10) return 1;
    if (digits.length === 11 && digits[0] === "1") return 1;
    return null;
  });
}

registerTransform(
  { name: "phone_country_code", inputTypes: ["phone"], priority: 45, mode: "series" },
  phoneCountryCode,
);
