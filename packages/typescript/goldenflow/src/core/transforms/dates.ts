/**
 * Date transforms — ported from goldenflow/transforms/dates.py
 * Side-effect module: registers 13 date transforms on import.
 *
 * All date operations use UTC to avoid timezone-dependent results
 * (matching Python's timezone-naive dateutil behavior).
 */

import type { ColumnValue } from "../types.js";
import { registerTransform } from "./registry.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function _parseDate(val: string): Date | null {
  const trimmed = val.trim();
  if (!trimmed) return null;
  const d = new Date(trimmed);
  if (isNaN(d.getTime())) return null;
  return d;
}

function pad(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}

const DAY_NAMES = [
  "Sunday",
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
];

// ---------------------------------------------------------------------------
// date_iso8601 (series, date, 50, auto_apply) — parse -> YYYY-MM-DD
// ---------------------------------------------------------------------------

function dateIso8601(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null) return null;
    const s = String(v);
    const d = _parseDate(s);
    if (!d) return v;
    return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
  });
}

registerTransform(
  { name: "date_iso8601", inputTypes: ["date"], autoApply: true, priority: 50, mode: "series" },
  dateIso8601,
);

// ---------------------------------------------------------------------------
// date_us (series, date, 50) — parse -> MM/DD/YYYY
// ---------------------------------------------------------------------------

function dateUs(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null) return null;
    const s = String(v);
    const d = _parseDate(s);
    if (!d) return v;
    return `${pad(d.getUTCMonth() + 1)}/${pad(d.getUTCDate())}/${d.getUTCFullYear()}`;
  });
}

registerTransform(
  { name: "date_us", inputTypes: ["date"], priority: 50, mode: "series" },
  dateUs,
);

// ---------------------------------------------------------------------------
// date_eu (series, date, 50) — parse -> DD/MM/YYYY
// ---------------------------------------------------------------------------

function dateEu(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null) return null;
    const s = String(v);
    const d = _parseDate(s);
    if (!d) return v;
    return `${pad(d.getUTCDate())}/${pad(d.getUTCMonth() + 1)}/${d.getUTCFullYear()}`;
  });
}

registerTransform(
  { name: "date_eu", inputTypes: ["date"], priority: 50, mode: "series" },
  dateEu,
);

// ---------------------------------------------------------------------------
// date_parse (series, date, 55) — same as date_iso8601
// ---------------------------------------------------------------------------

registerTransform(
  { name: "date_parse", inputTypes: ["date"], priority: 55, mode: "series" },
  dateIso8601,
);

// ---------------------------------------------------------------------------
// age_from_dob (series, date, 40, param: reference_date=null)
// ---------------------------------------------------------------------------

function ageFromDob(
  values: readonly ColumnValue[],
  referenceDate: unknown = null,
): ColumnValue[] {
  const ref = referenceDate ? _parseDate(String(referenceDate)) : new Date();
  if (!ref) return values.slice();

  return values.map((v) => {
    if (v === null) return null;
    const dob = _parseDate(String(v));
    if (!dob) return v;

    let age = ref.getUTCFullYear() - dob.getUTCFullYear();
    const monthDiff = ref.getUTCMonth() - dob.getUTCMonth();
    if (monthDiff < 0 || (monthDiff === 0 && ref.getUTCDate() < dob.getUTCDate())) {
      age--;
    }
    return age;
  });
}

registerTransform(
  { name: "age_from_dob", inputTypes: ["date"], priority: 40, mode: "series" },
  ageFromDob,
);

// ---------------------------------------------------------------------------
// datetime_iso8601 (series, date, 50) — parse -> YYYY-MM-DDTHH:MM:SS
// ---------------------------------------------------------------------------

function datetimeIso8601(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null) return null;
    const d = _parseDate(String(v));
    if (!d) return v;
    return (
      `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}` +
      `T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`
    );
  });
}

registerTransform(
  { name: "datetime_iso8601", inputTypes: ["date"], priority: 50, mode: "series" },
  datetimeIso8601,
);

// ---------------------------------------------------------------------------
// extract_year/month/day/quarter/day_of_week (series, date, 35)
// ---------------------------------------------------------------------------

function extractYear(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null) return null;
    const d = _parseDate(String(v));
    return d ? d.getUTCFullYear() : v;
  });
}

function extractMonth(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null) return null;
    const d = _parseDate(String(v));
    return d ? d.getUTCMonth() + 1 : v;
  });
}

function extractDay(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null) return null;
    const d = _parseDate(String(v));
    return d ? d.getUTCDate() : v;
  });
}

function extractQuarter(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null) return null;
    const d = _parseDate(String(v));
    if (!d) return v;
    return Math.floor(d.getUTCMonth() / 3) + 1;
  });
}

function extractDayOfWeek(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null) return null;
    const d = _parseDate(String(v));
    return d ? DAY_NAMES[d.getUTCDay()]! : v;
  });
}

registerTransform({ name: "extract_year", inputTypes: ["date"], priority: 35, mode: "series" }, extractYear);
registerTransform({ name: "extract_month", inputTypes: ["date"], priority: 35, mode: "series" }, extractMonth);
registerTransform({ name: "extract_day", inputTypes: ["date"], priority: 35, mode: "series" }, extractDay);
registerTransform({ name: "extract_quarter", inputTypes: ["date"], priority: 35, mode: "series" }, extractQuarter);
registerTransform({ name: "extract_day_of_week", inputTypes: ["date"], priority: 35, mode: "series" }, extractDayOfWeek);

// ---------------------------------------------------------------------------
// date_shift (series, date, 30, param: days=0) — add days, return ISO date
// ---------------------------------------------------------------------------

function dateShift(
  values: readonly ColumnValue[],
  days: unknown = 0,
): ColumnValue[] {
  const shift = typeof days === "number" ? days : Number(days) || 0;
  const shiftMs = shift * 86_400_000;

  return values.map((v) => {
    if (v === null) return null;
    const d = _parseDate(String(v));
    if (!d) return v;
    const shifted = new Date(d.getTime() + shiftMs);
    return `${shifted.getUTCFullYear()}-${pad(shifted.getUTCMonth() + 1)}-${pad(shifted.getUTCDate())}`;
  });
}

registerTransform(
  { name: "date_shift", inputTypes: ["date"], priority: 30, mode: "series" },
  dateShift,
);

// ---------------------------------------------------------------------------
// date_validate (series, date|string, 60) — returns boolean
// ---------------------------------------------------------------------------

function dateValidate(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null) return null;
    const s = String(v).trim();
    if (!s) return false;
    return _parseDate(s) !== null;
  });
}

registerTransform(
  { name: "date_validate", inputTypes: ["date", "string"], priority: 60, mode: "series" },
  dateValidate,
);
