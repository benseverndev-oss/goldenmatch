/**
 * schema-match.ts -- schema-free column auto-mapping between two sources.
 *
 * Edge-safe port of Python `goldenmatch/core/schema_match.py::auto_map_columns`.
 * Given two record sets with DIFFERENT schemas it proposes `(col_a, col_b)`
 * mappings via column-name similarity + value-signal overlap, e.g.
 *   file A has "email", file B has "contact_email"        (synonym)
 *   file A has "full_name", file B has "first_name"+"last_name" (composite)
 *
 * NO `node:*` imports -- pure over plain `Row` objects. The reference-string
 * similarity reuses the existing `jaroWinkler` kernel from `scorer.ts`; no new
 * similarity implementation is introduced.
 *
 * Response shape mirrors Python EXACTLY (snake_case keys `col_a`/`col_b`/`score`/
 * `method`, plus `composite_cols` on composite findings) so the MCP `schema_match`
 * tool's `{mappings: [...]}` is byte-parity with the Python server.
 */

import type { Row } from "./types.js";
import { jaroWinkler } from "./scorer.js";

/** One proposed column mapping (snake_case for Python wire parity). */
export interface ColumnMapping {
  readonly col_a: string;
  readonly col_b: string;
  readonly score: number;
  readonly method: string;
  /** Present only on `method === "composite"`: the two source columns merged. */
  readonly composite_cols?: readonly [string, string];
}

// Common synonyms for column-name matching (verbatim from schema_match.py).
const SYNONYMS: Readonly<Record<string, readonly string[]>> = {
  name: ["full_name", "fullname", "customer_name", "person_name", "display_name", "contact_name"],
  first_name: ["fname", "firstname", "given_name", "forename"],
  last_name: ["lname", "lastname", "surname", "family_name"],
  email: ["email_address", "contact_email", "e_mail", "emailaddress", "mail"],
  phone: ["telephone", "phone_number", "tel", "mobile", "cell", "contact_phone", "phonenumber"],
  address: ["street_address", "addr", "street", "address_line_1", "address1", "mailing_address"],
  city: ["town", "municipality"],
  state: ["province", "region", "st"],
  zip: ["zipcode", "zip_code", "postal_code", "postcode", "postal"],
  country: ["nation", "country_code"],
  id: ["identifier", "record_id", "customer_id", "patient_id", "account_id", "uid"],
  company: ["organization", "org", "business", "employer", "firm", "company_name"],
  dob: ["date_of_birth", "birth_date", "birthdate", "birthday"],
  gender: ["sex"],
  title: ["product_name", "item_name", "description", "product_title"],
};

// Reverse lookup: alias/canonical (lowercased) -> canonical.
const SYNONYM_MAP: Record<string, string> = (() => {
  const map: Record<string, string> = {};
  for (const [canonical, aliases] of Object.entries(SYNONYMS)) {
    for (const alias of aliases) map[alias.toLowerCase()] = canonical;
    map[canonical.toLowerCase()] = canonical;
  }
  return map;
})();

const NUMERIC_SAMPLE = 200;
const VALUE_SAMPLE = 200;

function round(x: number, digits: number): number {
  const f = 10 ** digits;
  return Math.round(x * f) / f;
}

/** Columns of a record set, excluding the internal `__`-prefixed columns. */
function columnsOf(rows: readonly Row[]): string[] {
  if (rows.length === 0) return [];
  return Object.keys(rows[0] as Record<string, unknown>).filter((c) => !c.startsWith("__"));
}

/** rapidfuzz `partial_ratio` analogue built on the shared jaroWinkler kernel:
 *  best-window match of the shorter string against the longer. */
function partialRatio(a: string, b: string): number {
  if (a.length === 0 || b.length === 0) return 0;
  if (a === b) return 1;
  const shorter = a.length <= b.length ? a : b;
  const longer = a.length <= b.length ? b : a;
  if (longer.includes(shorter)) return 1;
  const n = shorter.length;
  let best = 0;
  for (let i = 0; i + n <= longer.length; i++) {
    const window = longer.slice(i, i + n);
    const s = jaroWinkler(shorter, window);
    if (s > best) best = s;
  }
  // Also compare full strings so short-vs-long near matches aren't missed.
  const full = jaroWinkler(shorter, longer);
  return Math.max(best, full);
}

/** Jaccard-like overlap between the two columns' sampled value sets. */
function valueOverlap(
  colA: string,
  colB: string,
  rowsA: readonly Row[],
  rowsB: readonly Row[],
): number {
  const collect = (col: string, rows: readonly Row[]): Set<string> => {
    const out = new Set<string>();
    const n = Math.min(rows.length, VALUE_SAMPLE);
    for (let i = 0; i < n; i++) {
      const v = (rows[i] as Record<string, unknown>)[col];
      if (v === null || v === undefined) continue;
      out.add(String(v).toLowerCase().trim());
    }
    return out;
  };
  const va = collect(colA, rowsA);
  const vb = collect(colB, rowsB);
  if (va.size === 0 || vb.size === 0) return 0;
  let inter = 0;
  for (const v of va) if (vb.has(v)) inter++;
  const union = va.size + vb.size - inter;
  return union > 0 ? inter / union : 0;
}

/** Whether a column reads as numeric over a sample (all non-null values parse). */
function isNumericColumn(col: string, rows: readonly Row[]): boolean {
  const n = Math.min(rows.length, NUMERIC_SAMPLE);
  let seen = 0;
  for (let i = 0; i < n; i++) {
    const v = (rows[i] as Record<string, unknown>)[col];
    if (v === null || v === undefined || v === "") continue;
    seen++;
    if (typeof v === "number") continue;
    const s = String(v).trim();
    if (s === "" || Number.isNaN(Number(s))) return false;
  }
  return seen > 0;
}

/** Type-compatibility bonus: both numeric -> 1.0, both string-ish -> 0.5, else 0. */
function typeSimilarity(
  colA: string,
  colB: string,
  rowsA: readonly Row[],
  rowsB: readonly Row[],
): number {
  const aNum = isNumericColumn(colA, rowsA);
  const bNum = isNumericColumn(colB, rowsB);
  if (aNum && bNum) return 1.0;
  if (!aNum && !bNum) return 0.5;
  return 0.0;
}

function scoreColumnPair(
  colA: string,
  colB: string,
  rowsA: readonly Row[],
  rowsB: readonly Row[],
): { score: number; method: string } {
  // 1. Exact name match.
  if (colA.toLowerCase().trim() === colB.toLowerCase().trim()) {
    return { score: 1.0, method: "exact_name" };
  }
  // 2. Synonym match.
  const canonicalA = SYNONYM_MAP[colA.toLowerCase().replace(/ /g, "_")] ?? "";
  const canonicalB = SYNONYM_MAP[colB.toLowerCase().replace(/ /g, "_")] ?? "";
  if (canonicalA && canonicalA === canonicalB) {
    return { score: 0.95, method: "synonym" };
  }

  let bestScore = 0.0;
  let bestMethod = "none";

  // 3. Fuzzy name similarity (reuses the jaroWinkler kernel).
  const nameSim = jaroWinkler(colA.toLowerCase(), colB.toLowerCase());
  if (nameSim > bestScore) {
    bestScore = nameSim;
    bestMethod = "name_sim";
  }

  // 4. Partial name match (one name contains the other), slightly discounted.
  const partial = partialRatio(colA.toLowerCase(), colB.toLowerCase());
  if (partial > bestScore) {
    bestScore = partial * 0.9;
    bestMethod = "partial_name";
  }

  // 5. Value overlap (sample-based).
  const valueSim = valueOverlap(colA, colB, rowsA, rowsB);
  if (valueSim > bestScore) {
    bestScore = valueSim;
    bestMethod = "value_overlap";
  }

  // 6. Type-compatibility bonus.
  const typeBonus = typeSimilarity(colA, colB, rowsA, rowsB);
  bestScore = Math.min(1.0, bestScore + typeBonus * 0.1);

  return { score: bestScore, method: bestMethod };
}

/** Detect composite mappings (e.g. full_name -> first_name + last_name). */
function detectComposites(
  unmappedA: readonly string[],
  unmappedB: readonly string[],
): ColumnMapping[] {
  const out: ColumnMapping[] = [];
  const canonicalOf = (c: string): string =>
    SYNONYM_MAP[c.toLowerCase().replace(/ /g, "_")] ?? c.toLowerCase();
  const firstNameCands = (cols: readonly string[]): string[] =>
    cols.filter((c) => (SYNONYM_MAP[c.toLowerCase().replace(/ /g, "_")] ?? "") === "first_name");
  const lastNameCands = (cols: readonly string[]): string[] =>
    cols.filter((c) => (SYNONYM_MAP[c.toLowerCase().replace(/ /g, "_")] ?? "") === "last_name");

  // A has a composite name, B has the parts.
  for (const ca of unmappedA) {
    if (canonicalOf(ca) !== "name") continue;
    const fn = firstNameCands(unmappedB);
    const ln = lastNameCands(unmappedB);
    if (fn.length > 0 && ln.length > 0) {
      out.push({
        col_a: ca,
        col_b: `${fn[0]} + ${ln[0]}`,
        score: 0.9,
        method: "composite",
        composite_cols: [fn[0]!, ln[0]!],
      });
    }
  }
  // B has a composite name, A has the parts.
  for (const cb of unmappedB) {
    if (canonicalOf(cb) !== "name") continue;
    const fn = firstNameCands(unmappedA);
    const ln = lastNameCands(unmappedA);
    if (fn.length > 0 && ln.length > 0) {
      out.push({
        col_a: `${fn[0]} + ${ln[0]}`,
        col_b: cb,
        score: 0.9,
        method: "composite",
        composite_cols: [fn[0]!, ln[0]!],
      });
    }
  }
  return out;
}

/**
 * Auto-detect column mappings between two record sets with different schemas.
 *
 * Faithful port of `auto_map_columns`: score every `(col_a, col_b)` pair, keep
 * those `>= minScore`, greedily assign best-first (each column used at most
 * once), then append composite mappings over the leftover columns.
 */
export function autoMapColumns(
  rowsA: readonly Row[],
  rowsB: readonly Row[],
  minScore = 0.5,
): ColumnMapping[] {
  const colsA = columnsOf(rowsA);
  const colsB = columnsOf(rowsB);

  const scores: { ca: string; cb: string; score: number; method: string }[] = [];
  for (const ca of colsA) {
    for (const cb of colsB) {
      const { score, method } = scoreColumnPair(ca, cb, rowsA, rowsB);
      if (score >= minScore) scores.push({ ca, cb, score, method });
    }
  }

  // Greedy best-match assignment (stable-ish: sort by descending score).
  scores.sort((x, y) => y.score - x.score);
  const usedA = new Set<string>();
  const usedB = new Set<string>();
  const mappings: ColumnMapping[] = [];
  for (const { ca, cb, score, method } of scores) {
    if (!usedA.has(ca) && !usedB.has(cb)) {
      mappings.push({ col_a: ca, col_b: cb, score: round(score, 3), method });
      usedA.add(ca);
      usedB.add(cb);
    }
  }

  const unmappedA = colsA.filter((c) => !usedA.has(c));
  const unmappedB = colsB.filter((c) => !usedB.has(c));
  mappings.push(...detectComposites(unmappedA, unmappedB));
  return mappings;
}
