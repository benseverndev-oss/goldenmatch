/**
 * Date transforms — the owned deterministic date/datetime kernel, ported
 * byte-for-byte from `goldenflow/transforms/dates.py` (which mirrors the Rust
 * `goldenflow_core::dates`). NO `Date`-parser delegation, NO `dateutil`, NO
 * timezone dependence: `_parseDate` / `_parseDatetime` run the SAME rules as the
 * Python and Rust surfaces so the four fused string transforms
 * (`date_iso8601` / `date_us` / `date_eu` / `datetime_iso8601`, plus the
 * `date_parse` alias) are byte-identical to the WASM `applyChain` kernel.
 *
 * Side-effect module: registers 13 date transforms on import.
 *
 * Supported set (see the Python/Rust module docs): numeric ISO / US-month-first
 * (with dateutil's >12 day-first flip), English month names (abbrev + full),
 * year-only `YYYY`, optional trailing `HH:MM[:SS]` (space- or `T`-separated).
 * 2-digit years use the POSIX pivot (00-68 -> 2000-2068, 69-99 -> 1969-1999).
 * A datetime-bearing string on a date-only transform TRUNCATES to the date.
 * Anything outside the set -> the value passes through unchanged.
 */

import type { ColumnValue } from "../types.js";
import { registerTransform } from "./registry.js";

// ---------------------------------------------------------------------------
// Owned deterministic parser (mirror of dates.py / dates.rs)
// ---------------------------------------------------------------------------

type Ymd = [number, number, number];
type Ymdhms = [number, number, number, number, number, number];

const _MONTHS: Readonly<Record<string, number>> = {
  jan: 1, january: 1,
  feb: 2, february: 2,
  mar: 3, march: 3,
  apr: 4, april: 4,
  may: 5,
  jun: 6, june: 6,
  jul: 7, july: 7,
  aug: 8, august: 8,
  sep: 9, sept: 9, september: 9,
  oct: 10, october: 10,
  nov: 11, november: 11,
  dec: 12, december: 12,
};

// Python `date.weekday()` order (Monday = 0), used by extract_day_of_week.
const _DAY_NAMES = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
];

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

function pad4(n: number): string {
  return String(n).padStart(4, "0");
}

function _asciiDigits(s: string): boolean {
  return s.length > 0 && /^[0-9]+$/.test(s);
}

function _monthFromName(tok: string): number | null {
  const key = tok.replace(/\.+$/, "").toLowerCase();
  return key in _MONTHS ? _MONTHS[key]! : null;
}

function _parseDayToken(tok: string): number | null {
  if (!(tok.length > 0 && tok.length <= 2) || !_asciiDigits(tok)) return null;
  return parseInt(tok, 10);
}

function _parseYearToken(tok: string): number | null {
  if (!(tok.length > 0 && tok.length <= 4) || !_asciiDigits(tok)) return null;
  const v = parseInt(tok, 10);
  if (tok.length === 2) return v <= 68 ? 2000 + v : 1900 + v; // POSIX pivot
  return v;
}

function _isLeap(y: number): boolean {
  return (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;
}

const _DIM: Readonly<Record<number, number>> = {
  1: 31, 3: 31, 5: 31, 7: 31, 8: 31, 10: 31, 12: 31, 4: 30, 6: 30, 9: 30, 11: 30,
};

function _daysInMonth(y: number, m: number): number {
  if (m === 2) return _isLeap(y) ? 29 : 28;
  return _DIM[m] ?? 0;
}

function _validYmd(y: number, m: number, d: number): boolean {
  return m >= 1 && m <= 12 && d >= 1 && d <= _daysInMonth(y, m);
}

function _finish(y: number, m: number, d: number): Ymd | null {
  return _validYmd(y, m, d) ? [y, m, d] : null;
}

function _interpretNumeric(g1: string, g2: string, g3: string): Ymd | null {
  let y: number;
  let m: number;
  let d: number;
  if (g1.length === 4) {
    // ISO: a 4-digit first group anchors year-first (Y, M, D).
    y = parseInt(g1, 10);
    m = parseInt(g2, 10);
    d = parseInt(g3, 10);
  } else {
    // US month-first: year is the last group; flip to day-first when the month
    // field > 12 and the day field <= 12 (dateutil disambiguation).
    const yOpt = _parseYearToken(g3);
    if (yOpt === null) return null;
    y = yOpt;
    const a = parseInt(g1, 10);
    const b = parseInt(g2, 10);
    if (a > 12 && b <= 12) {
      m = b;
      d = a;
    } else {
      m = a;
      d = b;
    }
  }
  return _finish(y, m, d);
}

function _parseNumeric(s: string): Ymd | null {
  for (const sep of ["-", "/"]) {
    const parts = s.split(sep);
    if (parts.length === 3 && parts.every((p) => p.length > 0 && p.length <= 4 && _asciiDigits(p))) {
      const r = _interpretNumeric(parts[0]!, parts[1]!, parts[2]!);
      if (r !== null) return r;
    }
  }
  return null;
}

function _parseMonthName(s: string): Ymd | null {
  const toks = s
    .split(/\s+/)
    .map((tok) => tok.replace(/,+$/, ""))
    .filter((t) => t.length > 0);
  const n = toks.length;
  if (n === 3) {
    let m = _monthFromName(toks[0]!);
    if (m !== null) {
      // "Mon Day Year"
      const d = _parseDayToken(toks[1]!);
      const y = _parseYearToken(toks[2]!);
      return d !== null && y !== null ? _finish(y, m, d) : null;
    }
    m = _monthFromName(toks[1]!);
    if (m !== null) {
      // "Day Mon Year"
      const d = _parseDayToken(toks[0]!);
      const y = _parseYearToken(toks[2]!);
      return d !== null && y !== null ? _finish(y, m, d) : null;
    }
    return null;
  }
  if (n === 2) {
    const m = _monthFromName(toks[0]!);
    if (m !== null) {
      // "Mon Year" -> day 1
      const y = _parseYearToken(toks[1]!);
      return y !== null ? _finish(y, m, 1) : null;
    }
    return null;
  }
  return null;
}

function _parseDateYmd(sRaw: string): Ymd | null {
  const s = sRaw.trim();
  if (!s) return null;
  if (s.length === 4 && _asciiDigits(s)) return [parseInt(s, 10), 1, 1]; // year-only
  const r = _parseNumeric(s);
  if (r !== null) return r;
  return _parseMonthName(s);
}

function _looksLikeTime(tok: string): boolean {
  const parts = tok.split(":");
  return (
    (parts.length === 2 || parts.length === 3) &&
    parts.every((p) => p.length > 0 && p.length <= 2 && _asciiDigits(p))
  );
}

function _parseTime(t: string): [number, number, number] | null {
  const parts = t.split(":");
  if (parts.length !== 2 && parts.length !== 3) return null;
  if (parts.some((p) => !(p.length > 0 && p.length <= 2) || !_asciiDigits(p))) return null;
  const hh = parseInt(parts[0]!, 10);
  const mm = parseInt(parts[1]!, 10);
  const ss = parts.length === 3 ? parseInt(parts[2]!, 10) : 0;
  return hh < 24 && mm < 60 && ss < 60 ? [hh, mm, ss] : null;
}

function _peelTime(s: string): [string, string | null] {
  const n = s.length;
  // ISO 'T' flanked by ASCII digits separates date and time.
  for (let i = 0; i < n; i++) {
    if (
      s[i] === "T" &&
      i > 0 &&
      i < n - 1 &&
      s[i - 1]! >= "0" &&
      s[i - 1]! <= "9" &&
      s[i + 1]! >= "0" &&
      s[i + 1]! <= "9"
    ) {
      return [s.slice(0, i), s.slice(i + 1)];
    }
  }
  // Trailing whitespace-delimited time token.
  let last = -1;
  for (let i = 0; i < n; i++) {
    if (/\s/.test(s[i]!)) last = i;
  }
  if (last >= 0) {
    const head = s.slice(0, last);
    const tail = s.slice(last + 1);
    if (_looksLikeTime(tail)) return [head.replace(/\s+$/, ""), tail];
  }
  return [s, null];
}

function _parseDatetime(valRaw: string): Ymdhms | null {
  const s = valRaw.trim();
  if (!s) return null;
  const [datePart, timePart] = _peelTime(s);
  const dr = _parseDateYmd(datePart);
  if (dr === null) return null;
  let hms: [number, number, number] | null;
  if (timePart === null) {
    hms = [0, 0, 0];
  } else {
    hms = _parseTime(timePart);
    if (hms === null) return null;
  }
  return [dr[0], dr[1], dr[2], hms[0], hms[1], hms[2]];
}

/** Parse to `[y, m, d]` (truncating any valid trailing time), or `null`. */
function _parseDate(val: string): Ymd | null {
  const r = _parseDatetime(val);
  if (r === null) return null;
  return [r[0], r[1], r[2]];
}

// ---------------------------------------------------------------------------
// date_iso8601 / date_parse (series, date, 50/55) — parse -> YYYY-MM-DD
// ---------------------------------------------------------------------------

function dateIso8601(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null) return null;
    const s = String(v);
    const d = _parseDate(s);
    if (!d) return v;
    return `${pad4(d[0])}-${pad2(d[1])}-${pad2(d[2])}`;
  });
}

registerTransform(
  { name: "date_iso8601", inputTypes: ["date"], autoApply: true, priority: 50, mode: "series" },
  dateIso8601,
);

registerTransform(
  { name: "date_parse", inputTypes: ["date"], priority: 55, mode: "series" },
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
    return `${pad2(d[1])}/${pad2(d[2])}/${pad4(d[0])}`;
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
    return `${pad2(d[2])}/${pad2(d[1])}/${pad4(d[0])}`;
  });
}

registerTransform(
  { name: "date_eu", inputTypes: ["date"], priority: 50, mode: "series" },
  dateEu,
);

// ---------------------------------------------------------------------------
// age_from_dob (series, date, 40, param: reference_date=null)
// ---------------------------------------------------------------------------

function ageFromDob(
  values: readonly ColumnValue[],
  referenceDate: unknown = null,
): ColumnValue[] {
  let ref: Ymd | null;
  if (referenceDate) {
    ref = _parseDate(String(referenceDate));
  } else {
    const now = new Date();
    ref = [now.getFullYear(), now.getMonth() + 1, now.getDate()];
  }
  if (!ref) return values.slice();

  return values.map((v) => {
    if (v === null) return null;
    const dob = _parseDate(String(v));
    if (!dob) return v;
    let age = ref[0] - dob[0];
    // (ref.month, ref.day) < (dob.month, dob.day) -> not had birthday yet.
    if (ref[1] < dob[1] || (ref[1] === dob[1] && ref[2] < dob[2])) age--;
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
    const s = String(v);
    const r = _parseDatetime(s);
    if (!r) return v;
    return `${pad4(r[0])}-${pad2(r[1])}-${pad2(r[2])}T${pad2(r[3])}:${pad2(r[4])}:${pad2(r[5])}`;
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
    return d ? d[0] : v;
  });
}

function extractMonth(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null) return null;
    const d = _parseDate(String(v));
    return d ? d[1] : v;
  });
}

function extractDay(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null) return null;
    const d = _parseDate(String(v));
    return d ? d[2] : v;
  });
}

function extractQuarter(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null) return null;
    const d = _parseDate(String(v));
    if (!d) return v;
    return Math.floor((d[1] - 1) / 3) + 1;
  });
}

function extractDayOfWeek(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null) return null;
    const d = _parseDate(String(v));
    if (!d) return v;
    // Python `date.weekday()` (Monday = 0). Date.UTC is exact for these ranges.
    const wd = (new Date(Date.UTC(d[0], d[1] - 1, d[2])).getUTCDay() + 6) % 7;
    return _DAY_NAMES[wd]!;
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

  return values.map((v) => {
    if (v === null) return null;
    const d = _parseDate(String(v));
    if (!d) return v;
    // Date.UTC normalizes the day arithmetic (leap years, month lengths); mirror
    // of Python `date + timedelta(days=...)` then `.isoformat()`.
    const shifted = new Date(Date.UTC(d[0], d[1] - 1, d[2] + shift));
    return `${pad4(shifted.getUTCFullYear())}-${pad2(shifted.getUTCMonth() + 1)}-${pad2(shifted.getUTCDate())}`;
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
