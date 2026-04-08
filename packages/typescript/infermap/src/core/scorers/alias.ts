// Alias scorer — matches fields that are known synonyms of each other.
// Mirrors infermap/scorers/alias.py.
import type { FieldInfo, ScorerResult } from "../types.js";
import { makeScorerResult } from "../types.js";
import type { Scorer } from "./base.js";

/**
 * Canonical-name → list-of-aliases table. Extended by config via
 * AliasScorer constructor arg (matching Python's config behavior).
 */
export const DEFAULT_ALIASES: Record<string, readonly string[]> = {
  first_name: ["fname", "first", "given_name", "first_nm", "forename"],
  last_name: ["lname", "last", "surname", "family_name", "last_nm"],
  email: ["email_address", "e_mail", "email_addr", "mail", "contact_email"],
  phone: ["phone_number", "ph", "telephone", "tel", "mobile", "cell"],
  address: [
    "addr",
    "street_address",
    "addr_line_1",
    "address_line_1",
    "mailing_address",
  ],
  city: ["town", "municipality"],
  state: ["st", "province", "region"],
  zip: ["zipcode", "zip_code", "postal_code", "postal", "postcode"],
  name: [
    "full_name",
    "fullname",
    "customer_name",
    "display_name",
    "contact_name",
  ],
  company: [
    "organization",
    "org",
    "business",
    "employer",
    "firm",
    "company_name",
  ],
  dob: ["date_of_birth", "birth_date", "birthdate", "birthday"],
  country: ["nation", "country_code"],
  gender: ["sex"],
  id: ["identifier", "record_id", "uid"],
  created_at: ["signup_date", "create_date", "date_created"],
};

function buildLookup(
  aliases: Record<string, readonly string[]>
): Map<string, string> {
  const lookup = new Map<string, string>();
  for (const [canonical, list] of Object.entries(aliases)) {
    lookup.set(canonical, canonical);
    for (const alias of list) {
      lookup.set(alias, canonical);
    }
  }
  return lookup;
}

export class AliasScorer implements Scorer {
  readonly name = "AliasScorer";
  readonly weight = 0.95;

  private readonly lookup: Map<string, string>;

  constructor(extraAliases: Record<string, readonly string[]> = {}) {
    // Merge defaults with user-provided extras (extras win on canonical collisions).
    const merged: Record<string, readonly string[]> = { ...DEFAULT_ALIASES };
    for (const [canonical, list] of Object.entries(extraAliases)) {
      merged[canonical] = list;
    }
    this.lookup = buildLookup(merged);
  }

  private canonical(name: string): string | undefined {
    return this.lookup.get(name.trim().toLowerCase());
  }

  score(source: FieldInfo, target: FieldInfo): ScorerResult | null {
    const srcName = source.name.trim().toLowerCase();
    const tgtName = target.name.trim().toLowerCase();

    const srcCanonical = this.canonical(srcName);
    const tgtCanonical = this.canonical(tgtName);

    // Schema-file declared aliases on target
    const declaredRaw = target.metadata["aliases"];
    const declaredAliases: string[] = Array.isArray(declaredRaw)
      ? (declaredRaw as unknown[]).filter((x): x is string => typeof x === "string")
      : [];
    const declaredLower = declaredAliases.map((a) => a.trim().toLowerCase());
    const targetHasDeclared = declaredAliases.length > 0;

    if (declaredLower.includes(srcName)) {
      return makeScorerResult(
        0.95,
        `'${source.name}' matches declared alias of target '${target.name}'`
      );
    }

    // Abstain if neither field has a known alias and target has none declared
    if (
      srcCanonical === undefined &&
      tgtCanonical === undefined &&
      !targetHasDeclared
    ) {
      return null;
    }

    if (srcCanonical !== undefined && srcCanonical === tgtCanonical) {
      return makeScorerResult(
        0.95,
        `'${source.name}' and '${target.name}' share canonical name '${srcCanonical}'`
      );
    }

    return makeScorerResult(
      0.0,
      `'${source.name}' (canonical=${srcCanonical ?? "None"}) and ` +
        `'${target.name}' (canonical=${tgtCanonical ?? "None"}) are different`
    );
  }
}
