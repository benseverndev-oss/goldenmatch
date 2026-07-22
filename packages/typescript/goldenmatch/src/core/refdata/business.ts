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
