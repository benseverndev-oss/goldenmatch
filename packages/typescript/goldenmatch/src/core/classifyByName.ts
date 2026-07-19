/**
 * classifyByName.ts — the name-*pattern*-only column classifier, the pure-TS port
 * of the shared `autoconfig-core::classify::classify_by_name` (Python
 * `autoconfig.py::_classify_by_name`).
 *
 * This is the name-classification AUTHORITY the #1207 strong-identifier blocking
 * union uses for name-column detection. It classifies purely on the column NAME
 * (regex over the string), in a fixed priority order — distinct from the
 * data-aware `classifyColumn`/`profileColumn` heuristic (which also inspects
 * sample values). The distinction is load-bearing: bare `first`/`last` are NOT
 * names here (only `first_name`/`fname`/`surname`/… are), and an earlier pattern
 * shadows a later one (`created_name` → `date`, because the date pattern's
 * `created` matches first). Feeding the data-aware classifier's verdict into the
 * union instead makes it over-fire vs Python — see #1317 / the 2a→2b split.
 *
 * Pinned byte-for-byte to the Rust core by the cross-surface golden fixture
 * (`tests/parity/fixtures/classify-by-name/classify_by_name_vectors.json`, also
 * checked by the Rust golden test + the `autoconfig_classify_by_name` wasm shim),
 * so TS-pure == wasm == Rust == Python and this parallel logic cannot drift.
 *
 * Parity scope: ASCII column names (the realistic case). Rust `fancy_regex`
 * `(?i)` and JS `/i` agree on ASCII case-folding; non-ASCII case edges are a
 * documented cross-surface classifier boundary (the same one the data-aware
 * classifier carries).
 */

import type { ColumnType } from "./profiler.js";

// The 10 name patterns, in the SAME priority order as `classify_by_name` — the
// first match wins. Every regex mirrors the Rust `fancy_regex` source verbatim.
const DATE_PATTERNS_NAME = /(date|_dt$|_date$|registr|created|updated|birth.?d|dob)/i;
const YEAR_PATTERNS = /(^|_)(year|yr)(_|$)/i;
const EMAIL_PATTERNS = /(email|e.?mail|email.?addr)/i;
// ID pattern is split: the core's regex embeds a CASE-SENSITIVE alternative
// `(?<=[a-zA-Z])(?:ID|Id)$` (matches `recordID`/`recordId` but NOT `recordid`)
// alongside `(?i:…)` case-insensitive groups. JS RegExp has no inline per-group
// flags, so we express it as two regexes and OR them.
const ID_PATTERNS_CI =
  /^(?:id|key|code|sku)$|_(?:id|key)$|^uuid$|^guid$|_uuid$|_guid$|^uuid_|^guid_|^(?:account_no|account_num)$|_(?:ref|ref_num|reg_num|account_no|account_num|account)$/i;
const ID_PATTERNS_CS = /(?<=[a-zA-Z])(?:ID|Id)$/;
const PRICE_PATTERNS = /(price|cost|amount|revenue|salary|fee|charge|total|balance)/i;
const ZIP_PATTERNS = /(zip|postal|postcode|zip.?code)/i;
const GEO_PATTERNS =
  /((?<![a-z])city|^state$|state.?cd|^country$|province|region|(?<![a-z])county)/i;
const ADDRESS_PATTERNS = /(address|street|addr|line.?1|line.?2)/i;
const PHONE_PATTERNS = /(phone|tel|mobile|fax|cell)/i;
const NAME_PATTERNS =
  /(^name$|first.?name|last.?name|full.?name|fname|lname|surname|given.?name)/i;

/**
 * Classify a column by its name alone. Returns the `ColumnType` of the first
 * matching pattern in priority order, or `null` when no pattern matches (the
 * crux: bare `first`/`last`/`middle`/… return `null`).
 *
 * Faithful port of `classify_by_name` — the branch order is load-bearing.
 */
export function classifyByName(colName: string): ColumnType | null {
  if (DATE_PATTERNS_NAME.test(colName)) return "date";
  if (YEAR_PATTERNS.test(colName)) return "year";
  if (EMAIL_PATTERNS.test(colName)) return "email";
  if (ID_PATTERNS_CI.test(colName) || ID_PATTERNS_CS.test(colName)) return "identifier";
  if (PRICE_PATTERNS.test(colName)) return "numeric";
  if (ZIP_PATTERNS.test(colName)) return "zip";
  if (GEO_PATTERNS.test(colName)) return "geo";
  if (ADDRESS_PATTERNS.test(colName)) return "address";
  if (PHONE_PATTERNS.test(colName)) return "phone";
  if (NAME_PATTERNS.test(colName)) return "name";
  return null;
}
