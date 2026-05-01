/**
 * Address transforms — ported from goldenflow/transforms/address.py
 * Side-effect module: registers 8 address transforms on import.
 */

import type { ColumnValue, Row } from "../types.js";
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

// ---------------------------------------------------------------------------
// Street abbreviation maps
// ---------------------------------------------------------------------------

const _STREET_ABBREV: Record<string, string> = {
  Street: "St", Avenue: "Ave", Boulevard: "Blvd", Drive: "Dr",
  Lane: "Ln", Road: "Rd", Court: "Ct", Place: "Pl",
  Circle: "Cir", Trail: "Trl", Way: "Way", Parkway: "Pkwy",
  Highway: "Hwy", Terrace: "Ter", Square: "Sq",
};

const _STREET_EXPAND: Record<string, string> = {};
for (const [full, abbr] of Object.entries(_STREET_ABBREV)) {
  _STREET_EXPAND[abbr] = full;
}

// ---------------------------------------------------------------------------
// US states
// ---------------------------------------------------------------------------

const _STATES: Record<string, string> = {
  Alabama: "AL", Alaska: "AK", Arizona: "AZ", Arkansas: "AR",
  California: "CA", Colorado: "CO", Connecticut: "CT", Delaware: "DE",
  Florida: "FL", Georgia: "GA", Hawaii: "HI", Idaho: "ID",
  Illinois: "IL", Indiana: "IN", Iowa: "IA", Kansas: "KS",
  Kentucky: "KY", Louisiana: "LA", Maine: "ME", Maryland: "MD",
  Massachusetts: "MA", Michigan: "MI", Minnesota: "MN", Mississippi: "MS",
  Missouri: "MO", Montana: "MT", Nebraska: "NE", Nevada: "NV",
  "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
  "North Carolina": "NC", "North Dakota": "ND", Ohio: "OH", Oklahoma: "OK",
  Oregon: "OR", Pennsylvania: "PA", "Rhode Island": "RI", "South Carolina": "SC",
  "South Dakota": "SD", Tennessee: "TN", Texas: "TX", Utah: "UT",
  Vermont: "VT", Virginia: "VA", Washington: "WA", "West Virginia": "WV",
  Wisconsin: "WI", Wyoming: "WY", "District Of Columbia": "DC",
};

const _STATES_REVERSE: Record<string, string> = {};
for (const [name, abbr] of Object.entries(_STATES)) {
  _STATES_REVERSE[abbr] = name;
}

const _STATES_LOWER: Record<string, string> = {};
for (const [name, abbr] of Object.entries(_STATES)) {
  _STATES_LOWER[name.toLowerCase()] = abbr;
}

// ---------------------------------------------------------------------------
// Country map
// ---------------------------------------------------------------------------

const _COUNTRIES: Record<string, string> = {
  "united states": "US", "united states of america": "US", usa: "US", us: "US",
  "u.s.a.": "US", "u.s.": "US", america: "US",
  "united kingdom": "GB", uk: "GB", "great britain": "GB", england: "GB",
  scotland: "GB", wales: "GB", "northern ireland": "GB",
  canada: "CA", ca: "CA",
  australia: "AU", au: "AU",
  germany: "DE", deutschland: "DE", de: "DE",
  france: "FR", fr: "FR",
  italy: "IT", italia: "IT", it: "IT",
  spain: "ES", espana: "ES", es: "ES",
  mexico: "MX", mx: "MX",
  brazil: "BR", brasil: "BR", br: "BR",
  japan: "JP", jp: "JP",
  china: "CN", cn: "CN",
  india: "IN", in: "IN",
  "south korea": "KR", korea: "KR", kr: "KR",
  netherlands: "NL", holland: "NL", nl: "NL",
  sweden: "SE", se: "SE",
  norway: "NO", no: "NO",
  denmark: "DK", dk: "DK",
  switzerland: "CH", ch: "CH",
  ireland: "IE", ie: "IE",
  "new zealand": "NZ", nz: "NZ",
  singapore: "SG", sg: "SG",
  portugal: "PT", pt: "PT",
  argentina: "AR", ar: "AR",
  colombia: "CO", co: "CO",
  philippines: "PH", ph: "PH",
  poland: "PL", pl: "PL",
  belgium: "BE", be: "BE",
  austria: "AT", at: "AT",
};

// ---------------------------------------------------------------------------
// Unit normalization patterns
// ---------------------------------------------------------------------------

const _UNIT_PATTERNS: [RegExp, string][] = [
  [/^(?:Apt|Apartment)\.?\s+/i, "Unit "],
  [/^(?:Ste|Suite)\.?\s+/i, "Ste "],
  [/^#\s*/i, "Unit "],
];

// ---------------------------------------------------------------------------
// address_standardize (series, address, 50)
// ---------------------------------------------------------------------------

// Pre-compiled regex for address standardize/expand (avoids re-creating per value)
const _ABBREV_PATTERNS = Object.entries(_STREET_ABBREV).map(
  ([full, abbr]) => [new RegExp(`\\b${full}\\b`, "gi"), abbr] as const,
);
const _EXPAND_PATTERNS = Object.entries(_STREET_EXPAND).map(
  ([abbr, full]) => [new RegExp(`\\b${abbr}\\b`, "gi"), full] as const,
);

function addressStandardize(values: readonly ColumnValue[]): ColumnValue[] {
  return mapStrings(values, (s) => {
    let result = s;
    for (const [pattern, abbr] of _ABBREV_PATTERNS) {
      result = result.replace(pattern, abbr);
    }
    return result;
  });
}

registerTransform(
  { name: "address_standardize", inputTypes: ["address"], priority: 50, mode: "series" },
  addressStandardize,
);

// ---------------------------------------------------------------------------
// address_expand (series, address, 50)
// ---------------------------------------------------------------------------

function addressExpand(values: readonly ColumnValue[]): ColumnValue[] {
  return mapStrings(values, (s) => {
    let result = s;
    for (const [pattern, full] of _EXPAND_PATTERNS) {
      result = result.replace(pattern, full);
    }
    return result;
  });
}

registerTransform(
  { name: "address_expand", inputTypes: ["address"], priority: 50, mode: "series" },
  addressExpand,
);

// ---------------------------------------------------------------------------
// state_abbreviate (series, state|string, 50)
// ---------------------------------------------------------------------------

function stateAbbreviate(values: readonly ColumnValue[]): ColumnValue[] {
  return mapStrings(values, (s) => {
    const trimmed = s.trim();
    // Already a 2-letter abbreviation?
    if (trimmed.length === 2 && _STATES_REVERSE[trimmed.toUpperCase()]) {
      return trimmed.toUpperCase();
    }
    const matched = _STATES_LOWER[trimmed.toLowerCase()];
    return matched ?? s;
  });
}

registerTransform(
  { name: "state_abbreviate", inputTypes: ["state", "string"], priority: 50, mode: "series" },
  stateAbbreviate,
);

// ---------------------------------------------------------------------------
// state_expand (series, state|string, 50)
// ---------------------------------------------------------------------------

function stateExpand(values: readonly ColumnValue[]): ColumnValue[] {
  return mapStrings(values, (s) => {
    return _STATES_REVERSE[s.trim().toUpperCase()] ?? s;
  });
}

registerTransform(
  { name: "state_expand", inputTypes: ["state", "string"], priority: 50, mode: "series" },
  stateExpand,
);

// ---------------------------------------------------------------------------
// zip_normalize (series, zip, 55, auto_apply)
// ---------------------------------------------------------------------------

function zipNormalize(values: readonly ColumnValue[]): ColumnValue[] {
  return mapStrings(values, (s) => {
    let val = s.trim();
    // Strip +4 extension
    val = val.split("-")[0]!;
    if (/^\d+$/.test(val)) {
      return val.padStart(5, "0");
    }
    return val; // preserve invalid
  });
}

registerTransform(
  { name: "zip_normalize", inputTypes: ["zip"], autoApply: true, priority: 55, mode: "series" },
  zipNormalize,
);

// ---------------------------------------------------------------------------
// split_address (dataframe, address, 45)
// ---------------------------------------------------------------------------

const _ADDRESS_PATTERN = /^(.+?),\s*(.+?),\s*([A-Za-z]{2})\s+(\d{5}(?:-\d{4})?)$/;

function splitAddress(rows: readonly Row[], column: string): Row[] {
  return rows.map((row) => {
    const val = row[column];
    if (val === null || val === undefined || typeof val !== "string") {
      return { ...row, street: null, city: null, state: null, zip: null };
    }
    const m = val.trim().match(_ADDRESS_PATTERN);
    if (m) {
      return { ...row, street: m[1], city: m[2], state: m[3], zip: m[4] };
    }
    return { ...row, street: val, city: null, state: null, zip: null };
  });
}

registerTransform(
  { name: "split_address", inputTypes: ["address"], priority: 45, mode: "dataframe" },
  splitAddress,
);

// ---------------------------------------------------------------------------
// country_standardize (series, country|string, 50)
// ---------------------------------------------------------------------------

function countryStandardize(values: readonly ColumnValue[]): ColumnValue[] {
  return mapStrings(values, (s) => {
    const lookup = s.trim().toLowerCase();
    return _COUNTRIES[lookup] ?? s;
  });
}

registerTransform(
  { name: "country_standardize", inputTypes: ["country", "string"], priority: 50, mode: "series" },
  countryStandardize,
);

// ---------------------------------------------------------------------------
// unit_normalize (series, address|string, 45)
// ---------------------------------------------------------------------------

function unitNormalize(values: readonly ColumnValue[]): ColumnValue[] {
  return mapStrings(values, (s) => {
    let result = s.trim();
    for (const [pattern, replacement] of _UNIT_PATTERNS) {
      result = result.replace(pattern, replacement);
    }
    return result;
  });
}

registerTransform(
  { name: "unit_normalize", inputTypes: ["address", "string"], priority: 45, mode: "series" },
  unitNormalize,
);
