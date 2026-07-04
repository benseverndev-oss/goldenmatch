/**
 * Address transforms — ported from goldenflow/transforms/address.py
 * Side-effect module: registers 8 address transforms on import.
 *
 * Owned-kernel family (Wave D address-simple): the 7 scalar transforms
 * (address_standardize/address_expand/state_abbreviate/state_expand/
 * zip_normalize/country_standardize/unit_normalize) and the multi-output
 * split_address are byte-for-byte ports of the Python pure-TS reference
 * (`_*_py` in `goldenflow/transforms/address.py`), which is itself proven
 * byte-identical to the Rust `goldenflow-core::address` kernels. The 7 scalars
 * are asserted over the shared oracle corpus
 * (`tests/parity/identifiers_corpus.jsonl`); split_address (which doesn't fit
 * a string->scalar row) is asserted with pinned vectors in
 * `tests/unit/address-kernels.test.ts`. Each registered transform dispatches
 * to the opt-in WASM backend (`FlowWasmBackend`) when `enableWasm()` has
 * succeeded; otherwise it runs the pure-TS implementation below. Pure-TS is
 * the default.
 *
 * The word-boundary / anchored-prefix / address-parse logic is hand-rolled
 * with ASCII semantics (NOT JS regex `\b`/`\w`, which are Unicode-aware and
 * would diverge from the Rust ASCII kernel).
 */

import type { ColumnValue, Row } from "../types.js";
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

/** ASCII-only lowercase (Rust `eq_ignore_ascii_case` semantics): fold `A-Z`
 * only, leave everything else (incl. non-ASCII) untouched. Mirrors
 * `_ascii_lower` in address.py. */
function asciiLower(c: string): string {
  return c >= "A" && c <= "Z" ? String.fromCharCode(c.charCodeAt(0) + 32) : c;
}

/** `\w` = ASCII `[A-Za-z0-9_]` (matches the Rust kernel, NOT JS's
 * Unicode-aware `\w`). Mirrors `_is_word_char`. */
function isWordChar(c: string): boolean {
  return (
    (c >= "a" && c <= "z") ||
    (c >= "A" && c <= "Z") ||
    (c >= "0" && c <= "9") ||
    c === "_"
  );
}

function isAsciiAlpha(c: string): boolean {
  return (c >= "a" && c <= "z") || (c >= "A" && c <= "Z");
}

function isAsciiDigit(c: string): boolean {
  return c >= "0" && c <= "9";
}

function isAllAsciiDigits(s: string): boolean {
  for (let i = 0; i < s.length; i++) {
    if (!isAsciiDigit(s[i]!)) return false;
  }
  return true;
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
// address_standardize / address_expand (series, address, 50)
//
// Case-insensitive, ASCII-word-boundary replace-all, one per `_STREET_ABBREV`
// entry, applied sequentially over the running string. Hand-rolled (no regex)
// so `\b` semantics match the Rust kernel exactly.
// ---------------------------------------------------------------------------

/** Case-insensitive, word-boundary-delimited replace-all — byte-identical to
 * `_replace_word_bounded` / `address.rs::replace_word_bounded`. `needle` is a
 * non-empty ASCII word; the surrounding text is preserved and the replacement
 * is not re-scanned. */
function replaceWordBounded(s: string, needle: string, rep: string): string {
  const nlen = needle.length;
  const hlen = s.length;
  let out = "";
  let i = 0;
  while (i < hlen) {
    let replaced = false;
    if (i + nlen <= hlen) {
      let allMatch = true;
      for (let k = 0; k < nlen; k++) {
        if (asciiLower(s[i + k]!) !== asciiLower(needle[k]!)) {
          allMatch = false;
          break;
        }
      }
      if (allMatch) {
        const leftOk = i === 0 || !isWordChar(s[i - 1]!);
        const rightIdx = i + nlen;
        const rightOk = rightIdx >= hlen || !isWordChar(s[rightIdx]!);
        if (leftOk && rightOk) {
          out += rep;
          i += nlen;
          replaced = true;
        }
      }
    }
    if (!replaced) {
      out += s[i]!;
      i += 1;
    }
  }
  return out;
}

/** Replace full street suffixes with abbreviations (`Street`->`St` ...), in
 * `_STREET_ABBREV` insertion order. Byte-identical to
 * `_address_standardize_py`. */
export function addressStandardizeTs(s: string): string {
  let out = s;
  for (const [full, abbr] of Object.entries(_STREET_ABBREV)) {
    out = replaceWordBounded(out, full, abbr);
  }
  return out;
}

function addressStandardize(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapStrings(values, backend ? (s) => backend.addressStandardize(s) : addressStandardizeTs);
}

registerTransform(
  { name: "address_standardize", inputTypes: ["address"], priority: 50, mode: "series" },
  addressStandardize,
);

/** Replace street abbreviations with full forms (`St`->`Street` ...), in
 * `_STREET_ABBREV` insertion order (abbr->full). Byte-identical to
 * `_address_expand_py`. */
export function addressExpandTs(s: string): string {
  let out = s;
  for (const [full, abbr] of Object.entries(_STREET_ABBREV)) {
    out = replaceWordBounded(out, abbr, full);
  }
  return out;
}

function addressExpand(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapStrings(values, backend ? (s) => backend.addressExpand(s) : addressExpandTs);
}

registerTransform(
  { name: "address_expand", inputTypes: ["address"], priority: 50, mode: "series" },
  addressExpand,
);

// ---------------------------------------------------------------------------
// state_abbreviate (series, state|string, 50)
// ---------------------------------------------------------------------------

/** Normalize a state name to a 2-letter abbreviation. Three-way fallback:
 * (1) a 2-char input that upper-cases to a valid abbreviation -> uppercase;
 * (2) full name (case-insensitive) -> `_STATES_LOWER`; (3) neither ->
 * the ORIGINAL (un-stripped) value. Byte-identical to `_state_abbreviate_py`. */
export function stateAbbreviateTs(s: string): string {
  const cleaned = s.trim();
  const upper = cleaned.toUpperCase();
  if (cleaned.length === 2 && _STATES_REVERSE[upper] !== undefined) {
    return upper;
  }
  const abbr = _STATES_LOWER[cleaned.toLowerCase()];
  if (abbr !== undefined) return abbr;
  return s;
}

function stateAbbreviate(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapStrings(values, backend ? (s) => backend.stateAbbreviate(s) : stateAbbreviateTs);
}

registerTransform(
  { name: "state_abbreviate", inputTypes: ["state", "string"], priority: 50, mode: "series" },
  stateAbbreviate,
);

// ---------------------------------------------------------------------------
// state_expand (series, state|string, 50)
// ---------------------------------------------------------------------------

/** Expand a 2-letter state abbreviation to its full name; unmatched inputs
 * pass through as the ORIGINAL (un-stripped) value. Byte-identical to
 * `_state_expand_py`. */
export function stateExpandTs(s: string): string {
  const full = _STATES_REVERSE[s.trim().toUpperCase()];
  return full !== undefined ? full : s;
}

function stateExpand(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapStrings(values, backend ? (s) => backend.stateExpand(s) : stateExpandTs);
}

registerTransform(
  { name: "state_expand", inputTypes: ["state", "string"], priority: 50, mode: "series" },
  stateExpand,
);

// ---------------------------------------------------------------------------
// zip_normalize (series, zip, 55, auto_apply)
// ---------------------------------------------------------------------------

/** Normalize a US ZIP to 5-digit form: strip +4, zero-pad an all-digit base to
 * width 5 (a >5-digit base is returned as-is; a non-digit base passes through
 * unchanged). Byte-identical to `_zip_normalize_py`. */
export function zipNormalizeTs(s: string): string {
  const base = s.trim().split("-")[0]!;
  if (base.length > 0 && isAllAsciiDigits(base)) {
    return base.padStart(5, "0");
  }
  return base;
}

function zipNormalize(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapStrings(values, backend ? (s) => backend.zipNormalize(s) : zipNormalizeTs);
}

registerTransform(
  { name: "zip_normalize", inputTypes: ["zip"], autoApply: true, priority: 55, mode: "series" },
  zipNormalize,
);

// ---------------------------------------------------------------------------
// country_standardize (series, country|string, 50)
// ---------------------------------------------------------------------------

/** Normalize a country name to its ISO 3166-1 alpha-2 code (trim+lowercase
 * key); unrecognized values pass through as the ORIGINAL (un-stripped) value.
 * Byte-identical to `_country_standardize_py`. */
export function countryStandardizeTs(s: string): string {
  return _COUNTRIES[s.trim().toLowerCase()] ?? s;
}

function countryStandardize(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapStrings(values, backend ? (s) => backend.countryStandardize(s) : countryStandardizeTs);
}

registerTransform(
  { name: "country_standardize", inputTypes: ["country", "string"], priority: 50, mode: "series" },
  countryStandardize,
);

// ---------------------------------------------------------------------------
// unit_normalize (series, address|string, 45)
//
// trim, then three anchored prefix substitutions applied sequentially:
//   (Apt|Apartment)`.?`\s+ -> "Unit ", (Ste|Suite)`.?`\s+ -> "Ste ",
//   #\s* -> "Unit ". Hand-rolled (no regex).
// ---------------------------------------------------------------------------

/** Case-insensitive ASCII prefix test — mirrors `_ci_startswith`. */
function ciStartsWith(s: string, prefix: string): boolean {
  if (s.length < prefix.length) return false;
  for (let i = 0; i < prefix.length; i++) {
    if (asciiLower(s[i]!) !== asciiLower(prefix[i]!)) return false;
  }
  return true;
}

/** If `s` starts (case-insensitive) with one of `tokens`, then an optional `.`
 * then one-or-more whitespace, replace that whole prefix with `rep`. Byte-
 * identical to `_sub_leading_token` (the `\s+` requires >=1 stripped char). */
function subLeadingToken(s: string, tokens: readonly string[], rep: string): string {
  for (const tok of tokens) {
    if (ciStartsWith(s, tok)) {
      const rest = s.slice(tok.length);
      const afterDot = rest.startsWith(".") ? rest.slice(1) : rest;
      const afterWs = afterDot.trimStart();
      if (afterWs.length < afterDot.length) {
        return rep + afterWs;
      }
    }
  }
  return s;
}

/** If `s` starts with `#`, replace `#` + zero-or-more whitespace with
 * "Unit ". Byte-identical to `_sub_leading_hash`. */
function subLeadingHash(s: string): string {
  if (s.startsWith("#")) {
    return "Unit " + s.slice(1).trimStart();
  }
  return s;
}

/** Normalize a unit/apartment/suite designation. Byte-identical to
 * `_unit_normalize_py`. */
export function unitNormalizeTs(s: string): string {
  let result = s.trim();
  result = subLeadingToken(result, ["Apt", "Apartment"], "Unit ");
  result = subLeadingToken(result, ["Ste", "Suite"], "Ste ");
  result = subLeadingHash(result);
  return result;
}

function unitNormalize(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapStrings(values, backend ? (s) => backend.unitNormalize(s) : unitNormalizeTs);
}

registerTransform(
  { name: "unit_normalize", inputTypes: ["address", "string"], priority: 45, mode: "series" },
  unitNormalize,
);

// ---------------------------------------------------------------------------
// split_address (dataframe, address, 45)
//
// Parse "street, city, ST zip" -> [street, city, state, zip]. Hand-rolled
// (no regex) port of `_split_address_py` / `_try_parse_address` /
// `_parse_state_zip_tail` / `_is_zip`. On no-match: [origVal, null, null, null]
// (street = the ORIGINAL, un-stripped input); null input -> all null (caller).
// ---------------------------------------------------------------------------

/** True if `s` is a 5-digit ZIP or a 5+4 dash ZIP. Mirrors `_is_zip`. */
function isZip(s: string): boolean {
  if (s.length === 5) return isAllAsciiDigits(s);
  if (s.length === 10) {
    return isAllAsciiDigits(s.slice(0, 5)) && s[5] === "-" && isAllAsciiDigits(s.slice(6, 10));
  }
  return false;
}

/** Parse the `\s*[A-Za-z]{2}\s+<zip>$` tail into [state, zip] or null. Mirrors
 * `_parse_state_zip_tail`. */
function parseStateZipTail(rem: string): [string, string] | null {
  const afterWs = rem.trimStart();
  if (afterWs.length < 2 || !(isAsciiAlpha(afterWs[0]!) && isAsciiAlpha(afterWs[1]!))) {
    return null;
  }
  const state = afterWs.slice(0, 2);
  const rest = afterWs.slice(2);
  const zipc = rest.trimStart();
  if (zipc.length === rest.length) return null; // no whitespace between state and ZIP
  if (isZip(zipc)) return [state, zipc];
  return null;
}

/** Parse a trimmed "street, city, ST zip" string. Mirrors `_try_parse_address`:
 * street = up to the first comma; city = the shortest run to a later comma
 * whose remainder is a valid `<state> <zip>` tail. */
function tryParseAddress(t: string): [string, string, string, string] | null {
  const c1 = t.indexOf(",");
  if (c1 === -1) return null;
  const group1 = t.slice(0, c1);
  if (group1 === "") return null;
  const after1Ws = t.slice(c1 + 1).trimStart();
  let search = 0;
  for (;;) {
    const c2 = after1Ws.indexOf(",", search);
    if (c2 === -1) break;
    const group2 = after1Ws.slice(0, c2);
    if (group2 !== "") {
      const tail = parseStateZipTail(after1Ws.slice(c2 + 1));
      if (tail !== null) {
        return [group1, group2, tail[0], tail[1]];
      }
    }
    search = c2 + 1;
  }
  return null;
}

/** Parse "street, city, ST zip" -> [street, city, state, zip]. On no-match
 * returns [origVal, null, null, null] (street = the ORIGINAL, un-stripped
 * input). Byte-identical to `_split_address_py`. */
export function splitAddressTs(s: string): [string, string | null, string | null, string | null] {
  const parsed = tryParseAddress(s.trim());
  if (parsed !== null) return parsed;
  return [s, null, null, null];
}

function splitAddress(rows: readonly Row[], column: string): Row[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return rows.map((row) => {
    const val = row[column];
    if (val === null || val === undefined || typeof val !== "string") {
      return { ...row, street: null, city: null, state: null, zip: null };
    }
    const [street, city, state, zip] = backend
      ? backend.splitAddress(val)
      : splitAddressTs(val);
    return {
      ...row,
      street: street ?? null,
      city: city ?? null,
      state: state ?? null,
      zip: zip ?? null,
    };
  });
}

registerTransform(
  { name: "split_address", inputTypes: ["address"], priority: 45, mode: "dataframe" },
  splitAddress,
);
