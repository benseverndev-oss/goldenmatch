/**
 * Business legal-form reference data — edge-safe port of the entity-TYPE
 * variants from goldenmatch/refdata/data/legal_forms.json (the subset the
 * `initialism_match` scorer needs). No node:* imports; the data is embedded.
 *
 * These are the normalized entity-type legal-form tokens (Inc, LLC, Corp, Ltd,
 * GmbH, ...) that the initialism deriver DROPS from a business name before
 * taking initials, so "International Business Machines Corp" -> "IBM". It
 * EXCLUDES descriptive tokens ("Industries" / "Group" / "Holdings"), matching
 * Python `refdata.business.entity_form_variants()` exactly (77 entries). Each
 * key is `re.sub(r"\s+"," ",v).strip().rstrip(".,").lower()` of the raw variant.
 */

/** The 77 normalized entity-type legal-form variants (sorted, matching Python
 *  `sorted(entity_form_variants())`). Injected verbatim into the score-wasm
 *  kernel at `enableWasm()` so the WASM `initialism_match` drops the SAME forms
 *  the pure-TS path does. */
export const LEGAL_FORM_VARIANTS: readonly string[] = [
  "a.b", "a.g", "a.s", "ab", "ag", "aksjeselskap", "aktiebolag",
  "aktiengesellschaft", "as", "b.v", "besloten vennootschap", "bv",
  "charitable trust", "co", "company", "corp", "corporation", "foundation",
  "g.m.b.h", "gesellschaft mit beschrankter haftung", "gmbh", "inc",
  "incorporated", "k.g", "kg", "kommanditgesellschaft", "l l c", "l.l.c",
  "l.l.p", "l.p", "limited", "limited liability co", "limited liability company",
  "limited liability partnership", "limited partnership", "llc", "llp", "lp",
  "ltd", "n.v", "naamloze vennootschap", "nv", "oy", "oyj", "p.l.c", "partners",
  "partnership", "plc", "private limited", "proprietary limited", "pte ltd",
  "pte. ltd", "pty", "pty ltd", "pty. ltd", "public limited company", "pvt ltd",
  "pvt. ltd", "s.a", "s.a.r.l", "s.a.s", "s.l", "s.p.a", "s.r.l", "sa", "sarl",
  "sas", "sl", "sociedad anonima", "sociedad limitada", "societa per azioni",
  "societe a responsabilite limitee", "societe anonyme",
  "societe par actions simplifiee", "spa", "srl", "trust",
];

/** The entity-type legal-form variants as a lookup set (score-time membership
 *  test for `initialism_match`'s per-token legal-form drop). */
export const LEGAL_FORMS: ReadonlySet<string> = new Set(LEGAL_FORM_VARIANTS);

/** Injection payload for the score-wasm loader: the variant list the kernel's
 *  `set_legal_forms` OnceLock is seeded with (so WASM == pure-TS by construction). */
export function legalFormsInjectionData(): readonly string[] {
  return LEGAL_FORM_VARIANTS;
}

// ---- alias_match support (business canonicalization) ------------------------
// Port of `refdata.business.strip_legal_form` + `refdata.business_aliases`
// (`canonical_company_form`). Used by the `alias_match` scorer (score_one id 8):
// two names alias-match when they canonicalize to the SAME non-empty business
// key. Distinct from the initialism data above: strip_legal_form uses ALL legal
// forms (`variants_normalized`, incl. the descriptive "group"/"holdings"/
// "industries" tokens), whereas `entity_form_variants()` (LEGAL_FORM_VARIANTS)
// excludes descriptors — so this file carries BOTH sets.

/** The 81 normalized legal-form variants — the FULL `variants_normalized` set
 *  (`sorted(refdata.business.known_variants())`), a superset of the 77 entity
 *  variants above by the 4 descriptor tokens (group / holding / holdings /
 *  industries). `strip_legal_form` strips ANY of these as a trailing suffix.
 *  Injected into the score-wasm kernel's `set_business_aliases` so WASM builds
 *  the identical strip regex the pure path scans for. */
export const LEGAL_FORM_VARIANTS_ALL: readonly string[] = [
  "a.b", "a.g", "a.s", "ab", "ag", "aksjeselskap", "aktiebolag",
  "aktiengesellschaft", "as", "b.v", "besloten vennootschap", "bv",
  "charitable trust", "co", "company", "corp", "corporation", "foundation",
  "g.m.b.h", "gesellschaft mit beschrankter haftung", "gmbh", "group", "holding",
  "holdings", "inc", "incorporated", "industries", "k.g", "kg",
  "kommanditgesellschaft", "l l c", "l.l.c", "l.l.p", "l.p", "limited",
  "limited liability co", "limited liability company",
  "limited liability partnership", "limited partnership", "llc", "llp", "lp",
  "ltd", "n.v", "naamloze vennootschap", "nv", "oy", "oyj", "p.l.c", "partners",
  "partnership", "plc", "private limited", "proprietary limited", "pte ltd",
  "pte. ltd", "pty", "pty ltd", "pty. ltd", "public limited company", "pvt ltd",
  "pvt. ltd", "s.a", "s.a.r.l", "s.a.s", "s.l", "s.p.a", "s.r.l", "sa", "sarl",
  "sas", "sl", "sociedad anonima", "sociedad limitada", "societa per azioni",
  "societe a responsabilite limitee", "societe anonyme",
  "societe par actions simplifiee", "spa", "srl", "trust",
];

/** surface form -> canonical company key (`business_aliases._state.surface_to_
 *  canonical`). Every value is already normalized (lowercase, legal-form-
 *  stripped); a canonical maps to itself so a record already in canonical form
 *  is recognized. First-canonical-wins collisions are pre-resolved in the source
 *  data file, so this is a flat map. */
export const BUSINESS_ALIAS_MAP: ReadonlyMap<string, string> = new Map([
  ["3m", "minnesota mining and manufacturing"],
  ["acme", "acme"],
  ["alphabet", "alphabet"],
  ["big blue", "international business machines"],
  ["facebook", "meta"],
  ["federal express", "federal express"],
  ["fedex", "federal express"],
  ["ge", "general electric"],
  ["general electric", "general electric"],
  ["google", "alphabet"],
  ["ibm", "international business machines"],
  ["international business machines", "international business machines"],
  ["kentucky fried chicken", "kentucky fried chicken"],
  ["kfc", "kentucky fried chicken"],
  ["meta", "meta"],
  ["meta platforms", "meta"],
  ["minnesota mining and manufacturing", "minnesota mining and manufacturing"],
  ["philip morris", "philip morris international"],
  ["philip morris international", "philip morris international"],
  ["pmi", "philip morris international"],
]);

// Leading-separator class `[\s,\-.]` (whitespace / comma / hyphen / period) and
// trailing-separator class `[\s.,]` (whitespace / period / comma — NO hyphen),
// matching Python's `strip_legal_form` regex. Single-char tests are O(1) — NOT
// the alternation+quantifier `[\s,\-.]+(?:v...)[\s.,]*$` pattern (which is a
// polynomial-ReDoS class on caller data); the whole strip is a linear scan.
function isLeadSep(ch: string): boolean {
  return ch === "," || ch === "-" || ch === "." || /\s/.test(ch);
}
function isTrailSep(ch: string): boolean {
  return ch === "." || ch === "," || /\s/.test(ch);
}

/** Collapse whitespace runs to one space and trim — Python `re.sub(r"\s+"," ",s)
 *  .strip()`. `\s+` (single class + quantifier, no alternation) is linear. */
function collapseWs(s: string): string {
  return s.replace(/\s+/g, " ").trim();
}

// Variants sorted DESCENDING by length then lexicographically — the SAME order
// score-core's `set_business_aliases` builds the regex alternation in, so the
// longest legal form wins at a given position ("limited liability company" beats
// "limited"/"company"). Variants are ASCII, so UTF-16 `.length` == codepoint
// count (the parity edge documented in score-core).
const STRIP_VARIANTS_SORTED: readonly string[] = [...LEGAL_FORM_VARIANTS_ALL].sort(
  (a, b) => b.length - a.length || (a < b ? -1 : a > b ? 1 : 0),
);

/** Linear equivalent of ONE `pattern.sub("", cleaned)` for the anchored strip
 *  regex `[\s,\-.]+(?:variant)[\s.,]*$`. Returns the start index of the leftmost
 *  match (a separator run, followed by a variant, followed by trailing seps to
 *  end-of-string), or -1 if none. Leftmost-run + longest-variant mirrors the
 *  regex's leftmost-match + DESC-length alternation. */
function findStripMatch(s: string): number {
  const low = s.toLowerCase(); // ASCII: same length, so indices align with `s`
  const n = low.length;
  for (let i = 0; i < n; i++) {
    // Only start at a separator-RUN start (i == 0 or the prior char is a non-sep).
    if (!isLeadSep(low[i]!) || (i > 0 && isLeadSep(low[i - 1]!))) continue;
    // Consume the whole leading-separator run (greedy `[\s,\-.]+`).
    let j = i;
    while (j < n && isLeadSep(low[j]!)) j++;
    for (const v of STRIP_VARIANTS_SORTED) {
      if (!low.startsWith(v, j)) continue;
      // The remainder after the variant must be all trailing-seps to `$`.
      let allTrail = true;
      for (let p = j + v.length; p < n; p++) {
        if (!isTrailSep(low[p]!)) {
          allTrail = false;
          break;
        }
      }
      if (allTrail) return i; // longest qualifying variant at the leftmost run
    }
  }
  return -1;
}

/** Remove a trailing legal-form suffix — port of `refdata.business.strip_legal_
 *  form`. Whitespace-collapse, then iteratively (bounded 4×, like Python) strip
 *  the anchored trailing form so compound suffixes ("Acme Holdings Inc") peel one
 *  per pass. `null` → `null`; no known suffix → whitespace-collapsed passthrough. */
export function stripLegalForm(value: string | null): string | null {
  if (value === null) return null;
  let cleaned = collapseWs(value);
  if (!cleaned) return cleaned;
  for (let pass = 0; pass < 4; pass++) {
    const at = findStripMatch(cleaned);
    // Python: `new = pattern.sub("", cleaned).strip()`. No match → `new` == cleaned.
    const next = at >= 0 ? cleaned.slice(0, at).trim() : cleaned.trim();
    if (next === cleaned || next === "") {
      if (next !== "") cleaned = next; // keep `cleaned` when a strip would empty it
      break;
    }
    cleaned = next;
  }
  return cleaned;
}

/** Business `_normalize`: `strip_legal_form` → whitespace-collapse → lowercase.
 *  Byte-for-byte with `refdata.business_aliases._normalize`. */
export function businessNormalize(name: string): string {
  const stripped = stripLegalForm(name) ?? "";
  return collapseWs(stripped).toLowerCase();
}

/** Canonical company key — port of `business_aliases.canonical_company_form`.
 *  Normalize, then map surface→canonical with an idempotent passthrough default.
 *  `null` → `null`; empty/whitespace-only → `""`. */
export function canonicalCompanyForm(name: string | null): string | null {
  if (name === null) return null;
  const norm = businessNormalize(name);
  if (!norm) return "";
  return BUSINESS_ALIAS_MAP.get(norm) ?? norm;
}

/** Injection payload for the score-wasm loader's `set_business_aliases`: the FULL
 *  variant list (the kernel rebuilds the strip regex) + the surface→canonical map
 *  as parallel arrays (flat wasm-bindgen boundary). WASM == pure-TS by
 *  construction (same variants → same regex → same canonicalization). */
export function businessAliasInjectionData(): {
  variants: readonly string[];
  surfaceForms: string[];
  canonicals: string[];
} {
  const surfaceForms: string[] = [];
  const canonicals: string[] = [];
  for (const [surface, canonical] of BUSINESS_ALIAS_MAP) {
    surfaceForms.push(surface);
    canonicals.push(canonical);
  }
  return { variants: LEGAL_FORM_VARIANTS_ALL, surfaceForms, canonicals };
}
