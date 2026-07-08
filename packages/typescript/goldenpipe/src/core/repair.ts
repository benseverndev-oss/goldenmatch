/**
 * repair.ts — pure-TS repair-plan kernel. The SP2 TS mirror of
 * goldenpipe-core/src/repair.rs and goldenpipe/repair.py. Deterministic, no I/O,
 * no regex; hand-rolled ASCII matchers so the three surfaces are byte-identical
 * by construction.
 *
 * BYTE-PARITY NOTE: JS strings are UTF-16, so `s.length`/`s[i]` count code
 * UNITS. Python `len`/indexing and Rust `.chars()` count code POINTS. Every
 * predicate here first spreads to a code-point array (`const cs = [...s]`) and
 * indexes/slices THAT — never the raw string.
 */

// coarse tags the host may supply
const COARSE = new Set(["date", "email", "name", "phone", "zip"]);

// ASCII char-class primitives (no regex; \d would diverge across engines)
function isDigit(c: string): boolean {
  return c >= "0" && c <= "9";
}
function isUpper(c: string): boolean {
  return c >= "A" && c <= "Z";
}
function isAlnumUpper(c: string): boolean {
  return isDigit(c) || isUpper(c);
}
// `chars` is already a code-point array; nonempty + every char passes.
function allChars(chars: string[], pred: (c: string) => boolean): boolean {
  return chars.length > 0 && chars.every(pred);
}
function asciiLower(s: string): string {
  let out = "";
  for (const c of s) {
    out += c >= "A" && c <= "Z" ? String.fromCharCode(c.charCodeAt(0) + 32) : c;
  }
  return out;
}
function isAsciiWs(c: string): boolean {
  return c === " " || c === "\t" || c === "\n" || c === "\r" || c === "\f" || c === "\v";
}

// value predicates (detection shape, not full validation)
function vCusip(s: string): boolean {
  const cs = [...s];
  return cs.length === 9 && allChars(cs, isAlnumUpper);
}
function vNpi(s: string): boolean {
  const cs = [...s];
  return cs.length === 10 && allChars(cs, isDigit);
}
function vImei(s: string): boolean {
  const cs = [...s];
  return cs.length === 15 && allChars(cs, isDigit);
}
function vEan(s: string): boolean {
  const cs = [...s];
  return (cs.length === 8 || cs.length === 13) && allChars(cs, isDigit);
}
function vIsbn(s: string): boolean {
  const cs = [...s];
  if (cs.length === 13 && allChars(cs, isDigit)) return true;
  return cs.length === 10 && allChars(cs.slice(0, 9), isDigit) && "0123456789Xx".includes(cs[9]);
}
function vAba(s: string): boolean {
  const cs = [...s];
  return cs.length === 9 && allChars(cs, isDigit);
}
function vIban(s: string): boolean {
  const cs = [...s];
  if (!(cs.length >= 15 && cs.length <= 34)) return false;
  return (
    isUpper(cs[0]) &&
    isUpper(cs[1]) &&
    isDigit(cs[2]) &&
    isDigit(cs[3]) &&
    allChars(cs.slice(4), isAlnumUpper)
  );
}
function vIsin(s: string): boolean {
  const cs = [...s];
  return (
    cs.length === 12 &&
    isUpper(cs[0]) &&
    isUpper(cs[1]) &&
    allChars(cs.slice(2, 11), isAlnumUpper) &&
    isDigit(cs[11])
  );
}
function vSwift(s: string): boolean {
  const cs = [...s];
  if (!(cs.length === 8 || cs.length === 11)) return false;
  return (
    allChars(cs.slice(0, 6), isUpper) &&
    allChars(cs.slice(6, 8), isAlnumUpper) &&
    (cs.length === 8 || allChars(cs.slice(8, 11), isAlnumUpper))
  );
}
function luhnOk(s: string): boolean {
  const cs = [...s];
  let total = 0;
  let alt = false;
  for (let i = cs.length - 1; i >= 0; i--) {
    let x = cs[i].charCodeAt(0) - 48; // digit value; callers guarantee all-digit
    if (alt) {
      x *= 2;
      if (x > 9) x -= 9;
    }
    total += x;
    alt = !alt;
  }
  return total % 10 === 0;
}
function vCreditCard(s: string): boolean {
  const t = [...s].filter((c) => c !== " " && c !== "-").join("");
  const cs = [...t];
  return cs.length >= 13 && cs.length <= 19 && allChars(cs, isDigit) && luhnOk(t);
}

// detectors: [tag, name_hints_or_null, value_predicate] in fixed order —
// name-gated group first (low false-positive), value-distinctive fallback second.
const DETECTORS: Array<[string, string[] | null, (s: string) => boolean]> = [
  ["cusip", ["cusip"], vCusip],
  ["npi", ["npi"], vNpi],
  ["imei", ["imei", "imsi"], vImei],
  ["ean", ["ean", "gtin", "barcode"], vEan],
  ["isbn", ["isbn"], vIsbn],
  ["aba_routing", ["routing", "aba"], vAba],
  ["iban", null, vIban],
  ["isin", null, vIsin],
  ["swift", null, vSwift],
  ["credit_card", null, vCreditCard],
];

function fineType(name: string, samples: string[]): string | null {
  const lname = asciiLower(name);
  const nonempty = samples.filter((s) => [...s].some((c) => !isAsciiWs(c)));
  if (nonempty.length === 0) return null;
  for (const [tag, hints, pred] of DETECTORS) {
    if (hints !== null && !hints.some((h) => lname.includes(h))) continue;
    let matches = 0;
    for (const s of nonempty) if (pred(s)) matches++;
    if (matches * 2 > nonempty.length) return tag;
  }
  return null;
}

function resolveTag(name: string, coarseType: string, samples: string[]): string | null {
  const ft = fineType(name, samples);
  if (ft !== null) return ft;
  return COARSE.has(coarseType) ? coarseType : null;
}

// per-fine-tag validator; credit_card uses luhn_validate.
const VALIDATOR: Record<string, string> = {
  iban: "iban_validate",
  isin: "isin_validate",
  swift: "swift_validate",
  cusip: "cusip_validate",
  npi: "npi_validate",
  imei: "imei_validate",
  ean: "ean_validate",
  isbn: "isbn_validate",
  credit_card: "luhn_validate",
  aba_routing: "aba_validate",
};

// mapping table: check -> tag -> transforms; tag "*" = wildcard. Nested (not a
// concatenated string key) so there is no separator char in the source.
const TABLE = new Map<string, Map<string, string[]>>();
function tableSet(check: string, tag: string, transforms: string[]): void {
  let byTag = TABLE.get(check);
  if (byTag === undefined) {
    byTag = new Map<string, string[]>();
    TABLE.set(check, byTag);
  }
  byTag.set(tag, transforms);
}
tableSet("encoding_detection", "*", ["fix_mojibake", "normalize_unicode"]);
tableSet("future_dated", "date", ["date_validate"]);
tableSet("temporal_order", "date", ["date_validate"]);
tableSet("stale_data", "date", ["date_validate"]);
tableSet("format_detection", "date", ["date_parse"]);
tableSet("format_detection", "email", ["email_normalize"]);
tableSet("pattern_consistency", "email", ["email_canonical"]);
tableSet("pattern_consistency", "name", ["name_proper"]);
tableSet("format_detection", "phone", ["phone_validate"]);
tableSet("pattern_consistency", "phone", ["phone_national"]);
tableSet("format_detection", "zip", ["zip_normalize"]);
for (const [tag, validator] of Object.entries(VALIDATOR)) {
  tableSet("format_detection", tag, [validator]);
  tableSet("pattern_consistency", tag, [validator]);
}

function lookup(check: string, tag: string | null): string[] | null {
  const byTag = TABLE.get(check);
  if (byTag === undefined) return null;
  if (tag !== null && byTag.has(tag)) return byTag.get(tag)!;
  if (byTag.has("*")) return byTag.get("*")!;
  return null;
}

export interface Finding {
  column?: string;
  check?: string;
  message?: string;
  severity?: string;
}
export interface ColumnInput {
  name: string;
  coarse_type?: string;
  samples?: string[];
}
export interface Repair {
  column: string;
  check: string;
  type_tag: string;
  suggested_transforms: string[];
  reason: string;
}
export interface RepairPlan {
  repairs: Repair[];
}

export function buildRepairPlan(findings: Finding[], columns: ColumnInput[]): RepairPlan {
  const tags = new Map<string, string | null>();
  for (const c of columns) {
    tags.set(c.name, resolveTag(c.name, c.coarse_type ?? "", c.samples ?? []));
  }

  const repairs: Repair[] = [];
  for (const f of findings) {
    const col = f.column;
    const check = f.check ?? "";
    // encoding wildcard can apply even to an omitted-tag column present in `columns`
    if (col === undefined || !tags.has(col)) continue;
    const tag = tags.get(col)!;
    const transforms = lookup(check, tag);
    if (!transforms || transforms.length === 0) continue;
    repairs.push({
      column: col,
      check,
      type_tag: tag !== null ? tag : "*",
      suggested_transforms: [...transforms],
      reason: [...String(f.message ?? "")].slice(0, 80).join(""),
    });
  }
  return { repairs };
}
