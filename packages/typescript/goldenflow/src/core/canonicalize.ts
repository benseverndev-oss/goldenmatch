/**
 * Pure, language-portable per-value field canonicalizers (#1128).
 *
 * Byte-identical TypeScript port of the Python reference at
 * `packages/python/goldenflow/goldenflow/canonicalize.py`. `canonicalize(value,
 * kind)` reduces a single field value to a deterministic canonical string for
 * match keys (email / phone / name / postal).
 *
 * The point of the port is PPRL / clean rooms: in the true-clean-room tier each
 * party hashes its own values client-side, so the browser-side TypeScript and
 * the server-side Python MUST agree on the exact canonical string before
 * hashing, or the CLKs never line up. Every rule below is therefore written to
 * be byte-for-byte identical to the Python version — which means **ASCII-only**
 * primitives, NOT the Unicode-aware `String.prototype.toLowerCase()` /
 * `.trim()` / `\s` / `\d`, which would diverge from Python's ASCII-only choices.
 *
 * Contract (identical to Python):
 * - scalar: one string in, one string out.
 * - total: `null` / `undefined` map to `""`; never throws on string input.
 * - idempotent: `f(f(x)) === f(x)`.
 * - locale-independent: case folding only touches `A`–`Z` / `a`–`z`; non-ASCII
 *   bytes pass through unchanged.
 * - dependency-free.
 *
 * Per-`kind` rules:
 * - `email`  : trim ASCII whitespace, then ASCII-lowercase.
 * - `phone`  : keep ASCII digits only; if 11 digits starting `1` (NANP country
 *   code), drop the leading `1` → 10 digits.
 * - `name`   : ASCII-lowercase, delete ASCII punctuation, collapse ASCII
 *   whitespace runs to one space, trim.
 * - `postal` : if it contains an ASCII letter (alphanumeric postcode, e.g. UK
 *   `"SW1A 1AA"` / CA `"K1A 0B1"`), keep ASCII alphanumerics and ASCII-uppercase;
 *   otherwise keep ASCII digits and take the first 5.
 */

export type CanonicalizeKind = "email" | "phone" | "name" | "postal";

// ── Portable ASCII primitives ────────────────────────────────────────────────
// Each mirrors the Python helper one-for-one. The regexes carry NO `u`/Unicode
// flag, so the character classes are strictly ASCII.

/** Lowercase ONLY ASCII A–Z (mirrors Python str.maketrans ASCII table). */
function asciiLower(s: string): string {
  return s.replace(/[A-Z]/g, (c) => String.fromCharCode(c.charCodeAt(0) + 32));
}

/** Uppercase ONLY ASCII a–z. */
function asciiUpper(s: string): string {
  return s.replace(/[a-z]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 32));
}

// The ASCII whitespace set we collapse/trim on: " \t\n\r\f\v". Deliberately NOT
// the Unicode-aware `\s`.
const ASCII_WS = /[ \t\n\r\f\v]+/g;
const ASCII_WS_LEADING = /^[ \t\n\r\f\v]+/;
const ASCII_WS_TRAILING = /[ \t\n\r\f\v]+$/;

/** Trim runs of the ASCII whitespace set from both ends. */
function trimAsciiWs(s: string): string {
  return s.replace(ASCII_WS_LEADING, "").replace(ASCII_WS_TRAILING, "");
}

/** Collapse runs of ASCII whitespace to a single space and trim the ends. */
function collapseWs(s: string): string {
  return s.split(ASCII_WS).filter((t) => t.length > 0).join(" ");
}

// ASCII punctuation == Python string.punctuation. A Set keeps deletion
// regex-metacharacter-safe and obviously ASCII-only.
const ASCII_PUNCT = new Set("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~");

/** Delete every ASCII punctuation char. */
function deletePunct(s: string): string {
  let out = "";
  for (const ch of s) {
    if (!ASCII_PUNCT.has(ch)) out += ch;
  }
  return out;
}

// ── Per-kind canonicalizers ──────────────────────────────────────────────────

function canonEmail(value: string): string {
  return asciiLower(trimAsciiWs(value));
}

function canonPhone(value: string): string {
  let digits = value.replace(/[^0-9]/g, "");
  if (digits.length === 11 && digits[0] === "1") {
    digits = digits.slice(1);
  }
  return digits;
}

function canonName(value: string): string {
  return collapseWs(deletePunct(asciiLower(value)));
}

function canonPostal(value: string): string {
  const hasLetter = /[A-Za-z]/.test(value);
  if (hasLetter) {
    return asciiUpper(value.replace(/[^A-Za-z0-9]/g, ""));
  }
  return value.replace(/[^0-9]/g, "").slice(0, 5);
}

const CANONICALIZERS: Record<CanonicalizeKind, (value: string) => string> = {
  email: canonEmail,
  phone: canonPhone,
  name: canonName,
  postal: canonPostal,
};

/**
 * Reduce a single field value to its canonical match-key string.
 *
 * Pure, total, idempotent, locale-independent, dependency-free — see the
 * module doc comment for the full spec. `null` / `undefined` map to `""`.
 *
 * @param value the raw field value (or `null` / `undefined`).
 * @param kind which canonicalizer: `"email"`, `"phone"`, `"name"`, or `"postal"`.
 * @returns the canonical string.
 * @throws if `kind` is not one of the four supported values (a programming
 *   error, surfaced loudly rather than silently no-op'd).
 */
export function canonicalize(
  value: string | null | undefined,
  kind: CanonicalizeKind,
): string {
  const fn = CANONICALIZERS[kind];
  if (fn === undefined) {
    throw new Error(
      `Unknown canonicalize kind ${JSON.stringify(kind)}; ` +
        `expected one of email, phone, name, postal.`,
    );
  }
  if (value === null || value === undefined) return "";
  return fn(value);
}
