/**
 * Name similarity — fuzzy column name matching with alias support.
 */

const ALIASES: Readonly<Record<string, readonly string[]>> = {
  first_name: ["fname", "first", "given_name", "first_nm"],
  last_name: ["lname", "last", "surname", "family_name", "last_nm"],
  email: ["email_address", "e_mail", "email_addr", "mail"],
  phone: ["phone_number", "ph", "telephone", "tel", "mobile", "cell"],
  address: ["addr", "street_address", "addr_line_1", "address_line_1"],
  city: ["town", "municipality"],
  state: ["st", "province", "region"],
  zip: ["zipcode", "zip_code", "postal_code", "postal"],
  name: ["full_name", "fullname", "customer_name"],
  created_at: ["signup_date", "signup_dt", "create_date", "date_created"],
};

const _ALIAS_LOOKUP = new Map<string, string>();
for (const [canonical, aliases] of Object.entries(ALIASES)) {
  for (const alias of aliases) {
    _ALIAS_LOOKUP.set(alias.toLowerCase(), canonical.toLowerCase());
  }
  _ALIAS_LOOKUP.set(canonical.toLowerCase(), canonical.toLowerCase());
}

/** Simple Jaro-Winkler-like similarity using Levenshtein ratio. */
function fuzzyWRatio(a: string, b: string): number {
  if (a === b) return 100;
  if (a.length === 0 || b.length === 0) return 0;

  const maxLen = Math.max(a.length, b.length);
  const prev = new Array<number>(b.length + 1);
  const curr = new Array<number>(b.length + 1);

  for (let j = 0; j <= b.length; j++) prev[j] = j;
  for (let i = 1; i <= a.length; i++) {
    curr[0] = i;
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      curr[j] = Math.min(prev[j]! + 1, curr[j - 1]! + 1, prev[j - 1]! + cost);
    }
    for (let j = 0; j <= b.length; j++) prev[j] = curr[j]!;
  }

  const distance = prev[b.length]!;
  return 100 * (1 - distance / maxLen);
}

export function nameSimilarity(source: string, target: string): number {
  const sLower = source.toLowerCase().trim();
  const tLower = target.toLowerCase().trim();

  if (sLower === tLower) return 1.0;

  const sCanonical = _ALIAS_LOOKUP.get(sLower);
  const tCanonical = _ALIAS_LOOKUP.get(tLower);
  if (sCanonical && tCanonical && sCanonical === tCanonical) return 0.95;

  return fuzzyWRatio(sLower, tLower) / 100.0;
}
