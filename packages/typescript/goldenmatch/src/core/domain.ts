/**
 * domain.ts — Domain detection & lightweight feature extraction.
 * Edge-safe: no `node:` imports.
 *
 * Ports goldenmatch/core/domain.py. Detects the subject area (product,
 * person, bibliographic, company, generic) from column names and extracts
 * per-row features (brand, model, version, etc.) as extra columns.
 */

import type { Row } from "./types.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface DomainProfile {
  readonly name: string;
  readonly confidence: number;
  readonly textColumns: readonly string[];
  readonly featureColumns: readonly string[];
}

/** Columns that extractFeatures may add to a Row. Used by preflight Check 1
 *  to identify "missing but producible" column references and auto-repair
 *  config.domain. If a future PR adds more extraction outputs, append here. */
export const DOMAIN_EXTRACTED_COLS: ReadonlySet<string> = new Set([
  "__brand__",
  "__model__",
  "__version__",
]);

// ---------------------------------------------------------------------------
// Domain signature tables
// ---------------------------------------------------------------------------

type Signature = { readonly pattern: RegExp; readonly weight: number };

const PRODUCT_SIGNATURES: readonly Signature[] = [
  { pattern: /brand|manufacturer|mfr/i, weight: 2 },
  { pattern: /model/i, weight: 2 },
  { pattern: /sku|upc|ean|asin|mpn/i, weight: 3 },
  { pattern: /price|msrp|cost/i, weight: 1 },
  { pattern: /category|dept|department/i, weight: 1 },
  { pattern: /product|item/i, weight: 1 },
];

const PERSON_SIGNATURES: readonly Signature[] = [
  // Anchor must apply to every branch in the alternation. Without the
  // outer group, `/^first|first_name|fname/i` only anchors `first`;
  // `first_name` and `fname` would match anywhere in the column name.
  { pattern: /^(first|first_name|fname)/i, weight: 2 },
  { pattern: /^(last|last_name|lname|surname)/i, weight: 2 },
  { pattern: /full_name|person_name/i, weight: 2 },
  { pattern: /email/i, weight: 2 },
  { pattern: /phone|mobile|cell/i, weight: 1 },
  { pattern: /dob|birth|birthday/i, weight: 2 },
  { pattern: /ssn|nin/i, weight: 3 },
];

const BIBLIOGRAPHIC_SIGNATURES: readonly Signature[] = [
  { pattern: /^title$|article_title/i, weight: 2 },
  { pattern: /authors?|by_line/i, weight: 3 },
  { pattern: /year|pub_year|published/i, weight: 1 },
  { pattern: /venue|journal|conference/i, weight: 2 },
  { pattern: /doi|issn|isbn/i, weight: 3 },
  { pattern: /abstract/i, weight: 1 },
];

const COMPANY_SIGNATURES: readonly Signature[] = [
  { pattern: /company|employer|org(?!anization_id)/i, weight: 2 },
  { pattern: /industry|sector/i, weight: 2 },
  { pattern: /website|domain|url/i, weight: 1 },
  { pattern: /ein|duns|cik|lei/i, weight: 3 },
  { pattern: /hq|headquarters/i, weight: 1 },
];

// ---------------------------------------------------------------------------
// Detection
// ---------------------------------------------------------------------------

function scoreDomain(
  columns: readonly string[],
  signatures: readonly Signature[],
): number {
  let score = 0;
  for (const col of columns) {
    for (const sig of signatures) {
      if (sig.pattern.test(col)) {
        score += sig.weight;
        break;
      }
    }
  }
  return score;
}

function findMatchingColumns(
  columns: readonly string[],
  signatures: readonly Signature[],
): string[] {
  const hits: string[] = [];
  for (const col of columns) {
    if (signatures.some((s) => s.pattern.test(col))) {
      hits.push(col);
    }
  }
  return hits;
}

const TEXT_NAME_RE = /name|title|description|notes|text|body/i;

/**
 * Detect the domain of a dataset based on its column names.
 */
export function detectDomain(columns: readonly string[]): DomainProfile {
  const candidates: ReadonlyArray<{
    name: string;
    score: number;
    features: string[];
  }> = [
    {
      name: "product",
      score: scoreDomain(columns, PRODUCT_SIGNATURES),
      features: findMatchingColumns(columns, PRODUCT_SIGNATURES),
    },
    {
      name: "person",
      score: scoreDomain(columns, PERSON_SIGNATURES),
      features: findMatchingColumns(columns, PERSON_SIGNATURES),
    },
    {
      name: "bibliographic",
      score: scoreDomain(columns, BIBLIOGRAPHIC_SIGNATURES),
      features: findMatchingColumns(columns, BIBLIOGRAPHIC_SIGNATURES),
    },
    {
      name: "company",
      score: scoreDomain(columns, COMPANY_SIGNATURES),
      features: findMatchingColumns(columns, COMPANY_SIGNATURES),
    },
  ];

  let winner = candidates[0]!;
  for (const c of candidates) if (c.score > winner.score) winner = c;

  const MAX_SCORE = 10;
  const confidence =
    winner.score <= 0 ? 0 : Math.min(1, winner.score / MAX_SCORE);

  const textColumns = columns.filter((c) => TEXT_NAME_RE.test(c));

  if (winner.score === 0) {
    return {
      name: "generic",
      confidence: 0,
      textColumns,
      featureColumns: [],
    };
  }

  return {
    name: winner.name,
    confidence,
    textColumns,
    featureColumns: winner.features,
  };
}

// ---------------------------------------------------------------------------
// Feature extraction
// ---------------------------------------------------------------------------

function asString(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  const s = typeof value === "string" ? value : String(value);
  const trimmed = s.trim();
  return trimmed.length === 0 ? null : trimmed;
}

const KNOWN_BRANDS = new Set(
  [
    "apple",
    "samsung",
    "sony",
    "lg",
    "dell",
    "hp",
    "lenovo",
    "asus",
    "acer",
    "microsoft",
    "google",
    "amazon",
    "bose",
    "canon",
    "nikon",
    "panasonic",
    "philips",
    "toshiba",
  ].map((s) => s.toLowerCase()),
);

const MODEL_RE = /\b([A-Z0-9]{2,}[\-_]?[A-Z0-9]{2,}|[A-Z][A-Z0-9]{3,})\b/;
const SEMVER_RE = /\b(\d+\.\d+(?:\.\d+)?(?:[\-+][A-Za-z0-9.]+)?)\b/;

function extractBrand(row: Row, profile: DomainProfile): string | null {
  const manufacturer =
    asString(row["manufacturer"]) ??
    asString(row["brand"]) ??
    asString(row["mfr"]);
  if (manufacturer) return manufacturer.toLowerCase();

  for (const col of profile.textColumns) {
    const val = asString(row[col]);
    if (!val) continue;
    const first = val.split(/\s+/)[0];
    if (first && KNOWN_BRANDS.has(first.toLowerCase())) {
      return first.toLowerCase();
    }
  }
  return null;
}

function extractModel(row: Row, profile: DomainProfile): string | null {
  const explicit = asString(row["model"]) ?? asString(row["mpn"]);
  if (explicit) {
    return explicit.replace(/[\-_\s]/g, "").toUpperCase();
  }
  for (const col of profile.textColumns) {
    const val = asString(row[col]);
    if (!val) continue;
    const m = MODEL_RE.exec(val);
    if (m && m[1]) return m[1].replace(/[\-_]/g, "").toUpperCase();
  }
  return null;
}

function extractVersion(row: Row, profile: DomainProfile): string | null {
  const explicit = asString(row["version"]) ?? asString(row["ver"]);
  if (explicit) return explicit;
  for (const col of profile.textColumns) {
    const val = asString(row[col]);
    if (!val) continue;
    const m = SEMVER_RE.exec(val);
    if (m && m[1]) return m[1];
  }
  return null;
}

/**
 * Annotate rows with domain-specific extracted columns.
 * Returns enriched rows plus indices with low extraction confidence.
 */
export function extractFeatures(
  rows: readonly Row[],
  profile: DomainProfile,
  confidenceThreshold: number = 0.3,
): { rows: Row[]; lowConfidenceIds: readonly number[] } {
  if (profile.name === "generic" || profile.confidence === 0) {
    return { rows: rows.map((r) => ({ ...r })), lowConfidenceIds: [] };
  }

  const lowConfidenceIds: number[] = [];
  const out: Row[] = [];

  for (let i = 0; i < rows.length; i++) {
    const row = rows[i]!;
    const enriched: Record<string, unknown> = { ...row };

    if (profile.name === "product") {
      const brand = extractBrand(row, profile);
      const model = extractModel(row, profile);
      const version = extractVersion(row, profile);

      if (brand !== null) enriched["__brand__"] = brand;
      if (model !== null) enriched["__model__"] = model;
      if (version !== null) enriched["__version__"] = version;

      const expected = 3;
      const got = [brand, model, version].filter((v) => v !== null).length;
      const conf = got / expected;
      if (conf < confidenceThreshold) lowConfidenceIds.push(i);
    }

    out.push(enriched as Row);
  }

  return { rows: out, lowConfidenceIds };
}

// ---------------------------------------------------------------------------
// Software product feature extraction (gap 3 — ports
// goldenmatch/core/domain.py extract_software_features)
// ---------------------------------------------------------------------------

export interface SoftwareExtractionResult {
  readonly nameNormalized: string | null;
  readonly version: string | null;
  readonly edition: string | null;
  readonly platform: string | null;
  readonly partNumber: string | null;
  readonly isUpgrade: boolean;
  readonly confidence: number;
}

// Python `_SW_VERSION`: "6.5" / "v. 9.4" | "cs3"/"cc 2024" | "2007".
// Three capture groups, matched case-insensitively.
const SW_VERSION_RE = /\b(?:v\.?\s*)?(\d+(?:\.\d+)+)\b|\b((?:cs|cc)\s*\d+)\b|\b(20[0-2]\d)\b/i;
const SW_VERSION_RE_G = new RegExp(SW_VERSION_RE.source, "gi");
const SW_EDITION_RE =
  /\b(professional|pro|standard|enterprise|premium|basic|ultimate|home|personal|academic|student|unlimited|plus|lite|express|starter|essentials?)\b/i;
const SW_EDITION_RE_G = new RegExp(SW_EDITION_RE.source, "gi");
// Python `_SW_PLATFORM`: three capture groups.
const SW_PLATFORM_RE =
  /\b(windows?|win|mac(?:intosh)?|linux|unix|osx?|ios|android)\b|\b(win(?:\/mac|\\mac))\b|\bfor\s+(pc|mac)\b/i;
const SW_PLATFORM_RE_G = new RegExp(SW_PLATFORM_RE.source, "gi");
const SW_PART_NUMBER_RE = /\b(\d{5,})\b/;
const SW_PART_NUMBER_RE_G = /\b(\d{5,})\b/g;
const SW_UPGRADE_RE = /\b(upgrade|upg|update)\b/i;
const SW_UPGRADE_RE_G = /\b(upgrade|upg|update)\b/gi;
const SW_PAREN_RE_G = /\([^)]*\)/g;
const SW_PUNCT_RE_G = /[^\w\s]/g;

const SW_STOP_WORDS = new Set([
  "the", "a", "an", "for", "and", "or", "with", "by", "from", "to",
  "in", "of", "-", "inc", "inc.", "llc", "corp", "software", "edition",
  "version", "ver", "cd", "dvd", "rom", "cd-rom", "dvd-rom",
  "jewel", "case", "package", "complete", "license", "media",
]);

/**
 * Extract structured features from a software product title. Faithful port of
 * Python ``extract_software_features``.
 */
export function extractSoftwareFeatures(text: string): SoftwareExtractionResult {
  if (!text || text.trim().length === 0) {
    return {
      nameNormalized: null,
      version: null,
      edition: null,
      platform: null,
      partNumber: null,
      isUpgrade: false,
      confidence: 0.0,
    };
  }

  const textLower = text.toLowerCase().trim();
  let signals = 0;
  const totalPossible = 3; // name, version, edition

  let version: string | null = null;
  const verMatch = SW_VERSION_RE.exec(text);
  if (verMatch) {
    version = (verMatch[1] ?? verMatch[2] ?? verMatch[3] ?? "").trim().toLowerCase();
    signals += 1;
  }

  let edition: string | null = null;
  const edMatch = SW_EDITION_RE.exec(text);
  if (edMatch) {
    edition = edMatch[1]!.trim().toLowerCase();
    if (edition === "professional") edition = "pro";
    signals += 0.5;
  }

  let platform: string | null = null;
  const platMatch = SW_PLATFORM_RE.exec(text);
  if (platMatch) {
    platform = (platMatch[1] ?? platMatch[2] ?? platMatch[3] ?? "").trim().toLowerCase();
    if (platform.startsWith("win")) platform = "win";
    signals += 0.3;
  }

  let partNumber: string | null = null;
  const pnMatch = SW_PART_NUMBER_RE.exec(text);
  if (pnMatch) {
    partNumber = pnMatch[1]!;
    signals += 0.5;
  }

  let isUpgrade = false;
  if (SW_UPGRADE_RE.test(text)) {
    isUpgrade = true;
    signals += 0.2;
  }

  // Normalized name: strip version, edition, platform, part numbers, upgrade
  // keywords, parentheticals, punctuation, stop words; collapse whitespace.
  let name = textLower;
  name = name.replace(SW_VERSION_RE_G, " ");
  name = name.replace(SW_EDITION_RE_G, " ");
  name = name.replace(SW_PLATFORM_RE_G, " ");
  name = name.replace(SW_PART_NUMBER_RE_G, " ");
  name = name.replace(SW_UPGRADE_RE_G, " ");
  name = name.replace(SW_PAREN_RE_G, " ");
  name = name.replace(SW_PUNCT_RE_G, " ");
  const words = name
    .split(/\s+/)
    .filter((w) => w.length > 0 && !SW_STOP_WORDS.has(w) && w.length > 1);
  const nameNormalized = words.length > 0 ? words.join(" ").trim() : null;

  if (nameNormalized && nameNormalized.length >= 3) {
    signals += 1;
  }

  const confidence = Math.min(1.0, signals / totalPossible);
  return { nameNormalized, version, edition, platform, partNumber, isUpgrade, confidence };
}

// ---------------------------------------------------------------------------
// Bibliographic feature extraction (gap 3 — ports extract_biblio_features)
// ---------------------------------------------------------------------------

export interface BiblioFeatures {
  readonly year: string | null;
  readonly doi: string | null;
  readonly titleKey: string | null;
}

// Python `_YEAR_PATTERN` uses re.search with group(0) = the full 4-digit year.
const YEAR_RE = /\b(?:19|20)\d{2}\b/;
// Python `_DOI_PATTERN`: 10.<4+digits>/<non-space>+
const DOI_RE = /10\.\d{4,}\/\S+/;
const BIBLIO_SKIP = new Set([
  "the", "a", "an", "on", "in", "of", "for", "and", "to", "with",
]);

/**
 * Extract features (year, DOI, first significant title word) from
 * bibliographic text. Faithful port of Python ``extract_biblio_features``.
 */
export function extractBiblioFeatures(text: string): BiblioFeatures {
  let year: string | null = null;
  const yearMatch = YEAR_RE.exec(text);
  if (yearMatch) year = yearMatch[0];

  let doi: string | null = null;
  const doiMatch = DOI_RE.exec(text);
  if (doiMatch) doi = doiMatch[0];

  let titleKey: string | null = null;
  const words = text.toLowerCase().split(/\s+/).filter((w) => w.length > 0);
  for (const w of words) {
    if (!BIBLIO_SKIP.has(w) && w.length > 2) {
      titleKey = w;
      break;
    }
  }

  return { year, doi, titleKey };
}

// ---------------------------------------------------------------------------
// Product subdomain detection (gap 3 — ports detect_domain's subdomain branch)
// ---------------------------------------------------------------------------

/**
 * Detect the product subdomain ("software" vs "electronics") from column
 * names. Returns null when the columns give no signal. Mirrors the subdomain
 * branch of Python ``detect_domain``.
 */
export function detectProductSubdomain(columns: readonly string[]): string | null {
  const colsLower = columns.map((c) => c.toLowerCase());
  const swSignals = colsLower.filter((c) =>
    ["software", "version", "license", "publisher"].some((p) => c.includes(p)),
  ).length;
  const hwSignals = colsLower.filter((c) =>
    ["brand", "model", "sku", "upc", "ean", "weight", "dimensions"].some((p) =>
      c.includes(p),
    ),
  ).length;
  if (swSignals > hwSignals) return "software";
  if (hwSignals > 0) return "electronics";
  return null;
}
