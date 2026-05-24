/**
 * Carceral (U.S. prisons, jails, detention centers) domain pack.
 *
 * Port of goldenflow/domains/carceral.py. Targets joining HIFLD Prison
 * Boundaries, EPA ECHO, state DOC inventories, and SDWA registrations —
 * datasets that share physical facilities but disagree on naming convention.
 *
 * Three carceral-specific problems this pack solves:
 *
 * 1. Operator-org prefixes. ECHO ships records like
 *    "MDOC, SOUTH MISSISSIPPI CORRECTIONAL INSTITUTION" while HIFLD says
 *    "SOUTH MISSISSIPPI CORRECTIONAL INSTITUTION". Stripping the leading
 *    operating-agency prefix before any fuzzy comparison is the single
 *    biggest precision win on this domain.
 *
 * 2. Federal facility-type abbreviations. USP / FCI / FCC / FPC / FMC /
 *    FDC / ADC — Bureau of Prisons abbreviations that vary across HIFLD
 *    (short form) and ECHO (long form). carceral_abbreviate expands them.
 *
 * 3. State-prison-complex aliases. Arizona's HIFLD names start with ASPC-
 *    while ECHO files them as ASP - / APS- (typo). Aliasing both to the
 *    long form lets the name scorer see a common prefix.
 *
 * The pack does NOT redefine address / ZIP / state / unit normalization —
 * it composes the existing address_standardize, zip_normalize,
 * state_abbreviate, and unit_normalize transforms in its defaultConfig.
 */

import type { ColumnValue, DomainPack, Row } from "../types.js";
import { makeConfig } from "../types.js";
import { registerTransform } from "../transforms/registry.js";

// ---------------------------------------------------------------------------
// Carceral-domain constants (exported so users can extend)
// ---------------------------------------------------------------------------

/**
 * Single-token operator-org acronyms used by state DOCs and private
 * corrections operators. Stripped only when they appear as a leading prefix
 * followed by a separator (`,` / `-` / `:` / `/`); mid-string occurrences are
 * left alone.
 */
export const CARCERAL_OPERATOR_ORGS: ReadonlySet<string> = new Set([
  "MDOC", "TDCJ", "CDCR", "FDOC", "GDC", "IDOC", "NCDPS",
  "DOC", "DOCR", "DOCS",
  "CCA", "CORECIVIC", "GEO GROUP", "GEO",
]);

/**
 * Federal Bureau of Prisons facility-type abbreviations. Expanded by
 * carceral_abbreviate so HIFLD's "USP HAZELTON" and ECHO's "UNITED STATES
 * PENITENTIARY HAZELTON" land in the same shape.
 */
export const CARCERAL_BOP_ABBREVIATIONS: Readonly<Record<string, string>> = {
  USP: "UNITED STATES PENITENTIARY",
  FCI: "FEDERAL CORRECTIONAL INSTITUTION",
  FCC: "FEDERAL CORRECTIONAL COMPLEX",
  FPC: "FEDERAL PRISON CAMP",
  FMC: "FEDERAL MEDICAL CENTER",
  FDC: "FEDERAL DETENTION CENTER",
  ADC: "ADMINISTRATIVE DETENTION CENTER",
};

/**
 * State-prison-complex name aliases. HIFLD uses one form; ECHO often uses a
 * different one (sometimes a typo). Both sides map to the long form so the
 * name scorer sees a common prefix.
 */
export const CARCERAL_STATE_COMPLEX_ALIASES: Readonly<Record<string, string>> = {
  ASPC: "ARIZONA STATE PRISON COMPLEX",
  ASP: "ARIZONA STATE PRISON",
  APS: "ARIZONA STATE PRISON", // observed ECHO typo for "ASP"
};

// ---------------------------------------------------------------------------
// Regex helpers (ported from the Python module)
// ---------------------------------------------------------------------------

/**
 * Phrase-form operator-org prefixes. ECHO uses these long-form variants
 * alongside the acronyms: "TX DEPT OF CRIM JUST- MCCONNELL UNIT",
 * "PA DEPT OF CORR/CHESTER SCI".
 */
const OPERATOR_PHRASE_RE =
  /^(?:(?:[A-Z]{2}|TEXAS|CALIFORNIA|FLORIDA|MISSISSIPPI|GEORGIA|INDIANA)\s+DEPT?\s+OF\s+(?:CORR(?:ECTIONS?)?|CRIM(?:INAL)?\s+JUST(?:ICE)?))\s*[,\-:/]\s*/;

// Acronyms sorted by length descending so multi-word forms win (e.g.
// "GEO GROUP" before "GEO"). Each must be a leading prefix followed by a
// separator AND at least one whitespace char.
const OPERATOR_ACRONYMS_SORTED = [...CARCERAL_OPERATOR_ORGS].sort(
  (a, b) => b.length - a.length,
);
const OPERATOR_ACRONYM_RE = new RegExp(
  "^(?:" + OPERATOR_ACRONYMS_SORTED.map(escapeRegExp).join("|") + ")\\s*[,\\-:/]\\s+",
);

const NON_ALNUM_RE = /[^A-Z0-9 ]+/g;
const WHITESPACE_RE = /\s+/g;
const OPERATOR_SUFFIX_RE = /\b(?:LLC|INC|CORP|CO|LTD)\b\.?\s*$/;

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function stripOperatorPrefix(s: string): string {
  s = s.replace(OPERATOR_PHRASE_RE, "");
  s = s.replace(OPERATOR_ACRONYM_RE, "");
  return s;
}

function expandAbbreviations(s: string): string {
  for (const [short, long] of Object.entries(CARCERAL_BOP_ABBREVIATIONS)) {
    s = s.replace(new RegExp(`\\b${escapeRegExp(short)}\\b`, "g"), long);
  }
  for (const [short, long] of Object.entries(CARCERAL_STATE_COMPLEX_ALIASES)) {
    s = s.replace(new RegExp(`\\b${escapeRegExp(short)}\\b`, "g"), long);
  }
  return s;
}

// ---------------------------------------------------------------------------
// Transforms
// ---------------------------------------------------------------------------

/**
 * Strip leading operator-org prefix from a carceral facility name.
 *   "MDOC, SOUTH MISS CORRECTIONAL INSTITUTION" -> "SOUTH MISS CORRECTIONAL INSTITUTION"
 *   "TX DEPT OF CRIM JUST- MCCONNELL UNIT"      -> "MCCONNELL UNIT"
 *   "PA DEPT OF CORR/CHESTER SCI"               -> "CHESTER SCI"
 */
function carceralOrgStrip(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null || typeof v !== "string") return v;
    return stripOperatorPrefix(v.toUpperCase().trim()).trim();
  });
}

/**
 * Expand carceral facility-type abbreviations and state-complex aliases.
 * Covers the BOP set (USP/FCI/FCC/FPC/FMC/FDC/ADC) plus the Arizona
 * ASPC/ASP/APS alias cluster. Word-bounded; mid-token occurrences are left
 * alone.
 */
function carceralAbbreviate(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null || typeof v !== "string") return v;
    return expandAbbreviations(v.toUpperCase().trim());
  });
}

/**
 * Full carceral name pipeline: org-strip + uppercase + punctuation strip +
 * abbreviation expand + legal-suffix strip. Output is suitable for
 * Jaro-Winkler / token-set scoring against another normalized name.
 */
function carceralNameNormalize(values: readonly ColumnValue[]): ColumnValue[] {
  return values.map((v) => {
    if (v === null || typeof v !== "string") return v;
    let s = v.toUpperCase().trim();
    s = stripOperatorPrefix(s);
    s = s.replace(NON_ALNUM_RE, " ");
    s = s.replace(WHITESPACE_RE, " ").trim();
    s = expandAbbreviations(s);
    s = s.replace(OPERATOR_SUFFIX_RE, "").trim();
    s = s.replace(WHITESPACE_RE, " ").trim();
    return s;
  });
}

/**
 * Pack `lat` + `lng` into a single `latlng` column shaped "<lat>|<lng>"
 * (empty when either is null). Idempotent: skips silently if `lat` or `lng`
 * is missing. dataframe-mode transform: receives all rows, returns all rows.
 */
function latlngPack(rows: readonly Row[]): Row[] {
  if (rows.length === 0) return [...rows];
  const first = rows[0]!;
  if (!("lat" in first) || !("lng" in first)) return [...rows];
  return rows.map((row) => {
    const lat = row["lat"];
    const lng = row["lng"];
    const latlng =
      lat === null || lat === undefined || lng === null || lng === undefined
        ? ""
        : `${String(lat)}|${String(lng)}`;
    return { ...row, latlng };
  });
}

registerTransform(
  { name: "carceral_org_strip", inputTypes: ["string"], autoApply: false, priority: 55, mode: "series" },
  carceralOrgStrip,
);
registerTransform(
  { name: "carceral_abbreviate", inputTypes: ["string"], autoApply: false, priority: 50, mode: "series" },
  carceralAbbreviate,
);
registerTransform(
  { name: "carceral_name_normalize", inputTypes: ["string"], autoApply: false, priority: 60, mode: "series" },
  carceralNameNormalize,
);
registerTransform(
  { name: "latlng_pack", inputTypes: ["string"], autoApply: false, priority: 40, mode: "dataframe" },
  latlngPack,
);

export const PACK: DomainPack = {
  name: "carceral",
  description:
    "U.S. carceral facilities (prisons, jails, detention centers). " +
    "Operator-org prefix stripping (state DOCs + private operators), " +
    "BOP facility-type abbreviation expansion (USP/FCI/FCC/...), " +
    "state-prison-complex aliasing (Arizona ASPC/ASP/APS), and " +
    "lat/lng packing for geo-aware scorers. Composes with the existing " +
    "address_standardize / zip_normalize / state_abbreviate transforms.",
  transforms: [
    "carceral_org_strip",
    "carceral_abbreviate",
    "carceral_name_normalize",
    "latlng_pack",
    // composed-from-existing-pack:
    "address_standardize",
    "unit_normalize",
    "zip_normalize",
    "state_abbreviate",
  ],
  defaultConfig: makeConfig({
    transforms: [
      { column: "name", ops: ["carceral_name_normalize"] },
      { column: "address", ops: ["strip", "address_standardize", "unit_normalize"] },
      { column: "city", ops: ["strip", "uppercase"] },
      { column: "state", ops: ["state_abbreviate"] },
      { column: "zip", ops: ["zip_normalize"] },
    ],
  }),
};
