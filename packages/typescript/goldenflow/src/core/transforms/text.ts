/**
 * Text transforms — ported from goldenflow/transforms/text.py
 * Side-effect module: registers 18 text transforms on import.
 *
 * Owned-kernel family (Wave D text-1 + text-2): 18 text transforms
 * (`strip`/`collapse_whitespace`/`normalize_quotes`/`normalize_line_endings`/
 * `remove_html_tags`/`remove_urls`/`remove_digits`/`remove_punctuation`/
 * `remove_emojis`/`extract_numbers` scalar + parameterized `truncate`/
 * `pad_left`/`pad_right`, plus the Unicode-heavy text-2 five `lowercase`/
 * `uppercase`/`title_case`/`normalize_unicode`/`fix_mojibake`) are byte-for-byte
 * ports of the goldenflow-core Rust kernels (`goldenflow-core::text`), proven
 * byte-identical to the Python reference. Each dispatches to the opt-in WASM
 * backend (`FlowWasmBackend`) when `enableWasm()` has succeeded; otherwise it
 * runs the pure-TS implementation below. Pure-TS is the default.
 *
 * NO regex is used where the JS engine would diverge from the Rust kernel: the
 * html-tag / url / number scans and char-class filters mirror the hand-rolled
 * Rust logic (or, for the simple literal cases, use a regex proven
 * byte-identical over the shared corpus). `\d` is spelled `[0-9]` (ASCII
 * bounded, matching the kernel); `remove_emojis` uses the kernel's explicit
 * codepoint ranges; `truncate`/`pad_*` are codepoint-based (`[...s]`) to match
 * Rust `chars()`.
 *
 * text-2 casing: `lowercase`/`uppercase` are `String.prototype.to{Lower,Upper}
 * Case()` (agree with Rust `to_{lower,upper}case` / Python `str` on the bounded
 * corpus, incl. ß->"SS"); `title_case` is a hand-rolled ASCII-title port of
 * `_title_case_py` (first alpha of each word upper, rest lower, non-alpha resets
 * -- iterated by code point); `normalize_unicode` uses the SAME explicit
 * generated `NORMALIZE_MAP` as the kernel (NOT `String.prototype.normalize`,
 * whose runtime Unicode DB would diverge); `fix_mojibake` is a portable
 * latin-1<->utf-8 round-trip (return original when the string isn't
 * latin-1-encodable or the bytes aren't valid UTF-8).
 */

import type { ColumnValue } from "../types.js";
import { registerTransform } from "./registry.js";
import { getFlowWasmBackend } from "../wasm/backend.js";
import { NORMALIZE_MAP } from "./_normalize_unicode_map.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function mapStrings(
  values: readonly ColumnValue[],
  fn: (s: string) => string,
): ColumnValue[] {
  return values.map((v) => {
    if (v === null || typeof v !== "string") return v;
    return fn(v);
  });
}

// ---------------------------------------------------------------------------
// Pure-TS kernel references (byte-identical to goldenflow-core::text). These
// are the fallbacks + the parity-harness entry points; the transform wrappers
// below dispatch native-first through the WASM backend when it is active.
// ---------------------------------------------------------------------------

/** `strip`: remove leading/trailing whitespace (Rust `str::trim`). */
function stripTs(s: string): string {
  return s.trim();
}

/** `collapse_whitespace`: replace each run of 2+ whitespace with a single
 * space; a lone whitespace char is unchanged (Rust `\s{2,}` -> " "). */
function collapseWhitespaceTs(s: string): string {
  return s.replace(/\s{2,}/g, " ");
}

/** `normalize_quotes`: smart double/prime -> `"`, smart single/prime -> `'`
 * (the exact 6 codepoints of the Rust kernel). */
function normalizeQuotesTs(s: string): string {
  return s
    .replace(/[“”″]/g, '"')
    .replace(/[‘’′]/g, "'");
}

/** `normalize_line_endings`: `\r\n` and lone `\r` -> `\n` (replace `\r\n`
 * first, then any remaining `\r`). */
function normalizeLineEndingsTs(s: string): string {
  return s.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
}

/** `remove_html_tags`: strip each `<...>` span with >=1 char between the
 * angle brackets (Rust regex `<[^>]+>`, SINGLE pass — `<>` / unclosed `<`
 * are left intact). */
function removeHtmlTagsTs(s: string): string {
  return s.replace(/<[^>]+>/g, "");
}

/** `remove_urls`: drop each `https?://` followed by >=1 non-whitespace char
 * (Rust regex `https?://\S+`). NO trailing trim — the kernel leaves
 * surrounding whitespace intact. */
function removeUrlsTs(s: string): string {
  return s.replace(/https?:\/\/\S+/g, "");
}

/** `remove_digits`: remove ASCII digit characters (kernel is bounded to
 * `[0-9]`, not Unicode `\d`). */
function removeDigitsTs(s: string): string {
  return s.replace(/[0-9]/g, "");
}

/** `remove_punctuation`: keep ASCII alphanumerics + whitespace, drop the rest
 * (Rust regex `[^a-zA-Z0-9\s]` -> ""; non-ASCII letters and `_` are dropped). */
function removePunctuationTs(s: string): string {
  return s.replace(/[^a-zA-Z0-9\s]/g, "");
}

/** `remove_emojis`: drop chars in the kernel's explicit emoji codepoint set.
 * `u` flag so astral ranges match per-codepoint (mirrors Rust `is_emoji`). */
function removeEmojisTs(s: string): string {
  return s.replace(
    /[\u{1F600}-\u{1F64F}\u{1F300}-\u{1F5FF}\u{1F680}-\u{1F6FF}\u{1F1E0}-\u{1F1FF}\u{2702}-\u{27B0}\u{24C2}-\u{1F251}\u{1F900}-\u{1F9FF}\u{1FA00}-\u{1FA6F}\u{1FA70}-\u{1FAFF}\u{2600}-\u{26FF}\u{200D}\u{FE0F}]/gu,
    "",
  );
}

/** `extract_numbers`: all `[0-9]+\.?[0-9]*` runs joined by single spaces
 * (ASCII digits only; no-match -> ""). */
function extractNumbersTs(s: string): string {
  const nums = [...s.matchAll(/[0-9]+\.?[0-9]*/g)].map((m) => m[0]);
  return nums.join(" ");
}

/** `truncate`: first `n` characters (codepoint-based, Rust `chars().take(n)`). */
function truncateTs(s: string, n: number): string {
  return [...s].slice(0, n).join("");
}

/** `pad_left`: left-pad to `width` codepoints with `pad`; unchanged when the
 * string is already at/over `width` (Rust `pad_start`). */
function padLeftTs(s: string, width: number, pad: string): string {
  const len = [...s].length;
  if (len >= width) return s;
  return pad.repeat(width - len) + s;
}

/** `pad_right`: right-pad to `width` codepoints with `pad`; unchanged when the
 * string is already at/over `width` (Rust `pad_end`). */
function padRightTs(s: string, width: number, pad: string): string {
  const len = [...s].length;
  if (len >= width) return s;
  return s + pad.repeat(width - len);
}

// --- text-2 (Unicode-heavy) ------------------------------------------------

/** `lowercase`: Rust `to_lowercase` / Python `str.lower` (bounded corpus). */
function lowercaseTs(s: string): string {
  return s.toLowerCase();
}

/** `uppercase`: Rust `to_uppercase` / Python `str.upper` (ß -> "SS" agrees). */
function uppercaseTs(s: string): string {
  return s.toUpperCase();
}

/** `title_case`: ASCII title-case, byte-identical to goldenflow-core
 * `names::ascii_title` / Python `_title_case_py`. First alphabetic char of each
 * word upper, rest lower; a non-alpha char emits as-is and resets the word.
 * Iterated by CODE POINT (`[...s]`); a Unicode-letter test mirrors Python
 * `str.isalpha` / Rust `char::is_alphabetic` (the corpus is ASCII). */
function titleCaseTs(s: string): string {
  let out = "";
  let prevAlpha = false;
  for (const ch of s) {
    if (/\p{L}/u.test(ch)) {
      out += prevAlpha ? ch.toLowerCase() : ch.toUpperCase();
      prevAlpha = true;
    } else {
      out += ch;
      prevAlpha = false;
    }
  }
  return out;
}

/** `normalize_unicode`: decompose + strip-combining via the explicit generated
 * `NORMALIZE_MAP` (codepoint -> replacement), byte-identical to
 * `_normalize_unicode_py` and the Rust kernel. Iterated by CODE POINT: ASCII
 * (`< 128`) passes through, a mapped char emits its replacement, an unmapped
 * non-ASCII char passes through unchanged (documented boundary). NOT
 * `String.prototype.normalize` -- its runtime Unicode DB would diverge. */
function normalizeUnicodeTs(s: string): string {
  let out = "";
  for (const ch of s) {
    const cp = ch.codePointAt(0)!;
    if (cp < 128) {
      out += ch;
    } else {
      out += NORMALIZE_MAP.get(cp) ?? ch;
    }
  }
  return out;
}

/** `fix_mojibake`: re-encode latin-1 bytes then decode as UTF-8; return the
 * original when the string isn't latin-1-encodable (any codepoint > 0xFF) or
 * the resulting bytes aren't valid UTF-8. Byte-identical to `_fix_mojibake_py`
 * (`val.encode("latin-1").decode("utf-8")`, original on either failure). */
function fixMojibakeTs(s: string): string {
  const bytes = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) {
    const code = s.charCodeAt(i);
    if (code > 0xff) return s; // not latin-1-encodable (mirrors encode("latin-1") failure)
    bytes[i] = code;
  }
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    return s; // invalid UTF-8 (mirrors decode("utf-8") failure)
  }
}

// ---------------------------------------------------------------------------
// strip (priority 90, auto_apply, string)
// ---------------------------------------------------------------------------

function strip(values: readonly ColumnValue[]): ColumnValue[] {
  const backend = getFlowWasmBackend();
  const fn = backend ? (s: string) => backend.strip(s) : stripTs;
  return mapStrings(values, fn);
}

registerTransform(
  { name: "strip", inputTypes: ["string"], autoApply: true, priority: 90, mode: "expr" },
  strip,
);

// ---------------------------------------------------------------------------
// lowercase (50, string) -- text-2, native-first
// ---------------------------------------------------------------------------

function lowercase(values: readonly ColumnValue[]): ColumnValue[] {
  const backend = getFlowWasmBackend();
  const fn = backend ? (s: string) => backend.lowercase(s) : lowercaseTs;
  return mapStrings(values, fn);
}

registerTransform(
  { name: "lowercase", inputTypes: ["string"], priority: 50, mode: "expr" },
  lowercase,
);

// ---------------------------------------------------------------------------
// uppercase (50, string) -- text-2, native-first
// ---------------------------------------------------------------------------

function uppercase(values: readonly ColumnValue[]): ColumnValue[] {
  const backend = getFlowWasmBackend();
  const fn = backend ? (s: string) => backend.uppercase(s) : uppercaseTs;
  return mapStrings(values, fn);
}

registerTransform(
  { name: "uppercase", inputTypes: ["string"], priority: 50, mode: "expr" },
  uppercase,
);

// ---------------------------------------------------------------------------
// title_case (50, string) -- text-2, native-first
// ---------------------------------------------------------------------------

function titleCase(values: readonly ColumnValue[]): ColumnValue[] {
  const backend = getFlowWasmBackend();
  const fn = backend ? (s: string) => backend.titleCase(s) : titleCaseTs;
  return mapStrings(values, fn);
}

registerTransform(
  { name: "title_case", inputTypes: ["string"], priority: 50, mode: "expr" },
  titleCase,
);

// ---------------------------------------------------------------------------
// normalize_unicode (85, auto_apply, string) -- text-2, native-first
// ---------------------------------------------------------------------------

function normalizeUnicode(values: readonly ColumnValue[]): ColumnValue[] {
  const backend = getFlowWasmBackend();
  const fn = backend ? (s: string) => backend.normalizeUnicode(s) : normalizeUnicodeTs;
  return mapStrings(values, fn);
}

registerTransform(
  { name: "normalize_unicode", inputTypes: ["string"], autoApply: true, priority: 85, mode: "series" },
  normalizeUnicode,
);

// ---------------------------------------------------------------------------
// remove_punctuation (40, string)
// ---------------------------------------------------------------------------

function removePunctuation(values: readonly ColumnValue[]): ColumnValue[] {
  const backend = getFlowWasmBackend();
  const fn = backend ? (s: string) => backend.removePunctuation(s) : removePunctuationTs;
  return mapStrings(values, fn);
}

registerTransform(
  { name: "remove_punctuation", inputTypes: ["string"], priority: 40, mode: "series" },
  removePunctuation,
);

// ---------------------------------------------------------------------------
// collapse_whitespace (80, auto_apply, string)
// ---------------------------------------------------------------------------

function collapseWhitespace(values: readonly ColumnValue[]): ColumnValue[] {
  const backend = getFlowWasmBackend();
  const fn = backend ? (s: string) => backend.collapseWhitespace(s) : collapseWhitespaceTs;
  return mapStrings(values, fn);
}

registerTransform(
  { name: "collapse_whitespace", inputTypes: ["string"], autoApply: true, priority: 80, mode: "expr" },
  collapseWhitespace,
);

// ---------------------------------------------------------------------------
// truncate (30, string, param: n=255)
// ---------------------------------------------------------------------------

function truncate(values: readonly ColumnValue[], n: unknown = 255): ColumnValue[] {
  const maxLen = typeof n === "number" ? n : Number(n) || 255;
  const backend = getFlowWasmBackend();
  const fn = backend
    ? (s: string) => backend.truncate(s, maxLen)
    : (s: string) => truncateTs(s, maxLen);
  return mapStrings(values, fn);
}

registerTransform(
  { name: "truncate", inputTypes: ["string"], priority: 30, mode: "series" },
  truncate,
);

// ---------------------------------------------------------------------------
// normalize_quotes (84, auto_apply, string)
// ---------------------------------------------------------------------------

function normalizeQuotes(values: readonly ColumnValue[]): ColumnValue[] {
  const backend = getFlowWasmBackend();
  const fn = backend ? (s: string) => backend.normalizeQuotes(s) : normalizeQuotesTs;
  return mapStrings(values, fn);
}

registerTransform(
  { name: "normalize_quotes", inputTypes: ["string"], autoApply: true, priority: 84, mode: "series" },
  normalizeQuotes,
);

// ---------------------------------------------------------------------------
// remove_html_tags (45, string)
// ---------------------------------------------------------------------------

function removeHtmlTags(values: readonly ColumnValue[]): ColumnValue[] {
  const backend = getFlowWasmBackend();
  const fn = backend ? (s: string) => backend.removeHtmlTags(s) : removeHtmlTagsTs;
  return mapStrings(values, fn);
}

registerTransform(
  { name: "remove_html_tags", inputTypes: ["string"], priority: 45, mode: "series" },
  removeHtmlTags,
);

// ---------------------------------------------------------------------------
// remove_urls (40, string)
// ---------------------------------------------------------------------------

function removeUrls(values: readonly ColumnValue[]): ColumnValue[] {
  const backend = getFlowWasmBackend();
  const fn = backend ? (s: string) => backend.removeUrls(s) : removeUrlsTs;
  return mapStrings(values, fn);
}

registerTransform(
  { name: "remove_urls", inputTypes: ["string"], priority: 40, mode: "series" },
  removeUrls,
);

// ---------------------------------------------------------------------------
// remove_digits (35, string)
// ---------------------------------------------------------------------------

function removeDigits(values: readonly ColumnValue[]): ColumnValue[] {
  const backend = getFlowWasmBackend();
  const fn = backend ? (s: string) => backend.removeDigits(s) : removeDigitsTs;
  return mapStrings(values, fn);
}

registerTransform(
  { name: "remove_digits", inputTypes: ["string"], priority: 35, mode: "series" },
  removeDigits,
);

// ---------------------------------------------------------------------------
// pad_left (30, string, params: width=10, char="0")
// ---------------------------------------------------------------------------

function padLeft(
  values: readonly ColumnValue[],
  width: unknown = 10,
  char: unknown = "0",
): ColumnValue[] {
  const w = typeof width === "number" ? width : Number(width) || 10;
  const c = typeof char === "string" ? char : "0";
  const backend = getFlowWasmBackend();
  const fn = backend
    ? (s: string) => backend.padLeft(s, w, c)
    : (s: string) => padLeftTs(s, w, c);
  return mapStrings(values, fn);
}

registerTransform(
  { name: "pad_left", inputTypes: ["string"], priority: 30, mode: "series" },
  padLeft,
);

// ---------------------------------------------------------------------------
// pad_right (30, string, params: width=10, char=" ")
// ---------------------------------------------------------------------------

function padRight(
  values: readonly ColumnValue[],
  width: unknown = 10,
  char: unknown = " ",
): ColumnValue[] {
  const w = typeof width === "number" ? width : Number(width) || 10;
  const c = typeof char === "string" ? char : " ";
  const backend = getFlowWasmBackend();
  const fn = backend
    ? (s: string) => backend.padRight(s, w, c)
    : (s: string) => padRightTs(s, w, c);
  return mapStrings(values, fn);
}

registerTransform(
  { name: "pad_right", inputTypes: ["string"], priority: 30, mode: "series" },
  padRight,
);

// ---------------------------------------------------------------------------
// remove_emojis (38, string)
// ---------------------------------------------------------------------------

function removeEmojis(values: readonly ColumnValue[]): ColumnValue[] {
  const backend = getFlowWasmBackend();
  const fn = backend ? (s: string) => backend.removeEmojis(s) : removeEmojisTs;
  return mapStrings(values, fn);
}

registerTransform(
  { name: "remove_emojis", inputTypes: ["string"], priority: 38, mode: "series" },
  removeEmojis,
);

// ---------------------------------------------------------------------------
// fix_mojibake (86, string) -- text-2, native-first
// ---------------------------------------------------------------------------

function fixMojibake(values: readonly ColumnValue[]): ColumnValue[] {
  const backend = getFlowWasmBackend();
  const fn = backend ? (s: string) => backend.fixMojibake(s) : fixMojibakeTs;
  return mapStrings(values, fn);
}

registerTransform(
  { name: "fix_mojibake", inputTypes: ["string"], priority: 86, mode: "series" },
  fixMojibake,
);

// ---------------------------------------------------------------------------
// normalize_line_endings (82, string)
// ---------------------------------------------------------------------------

function normalizeLineEndings(values: readonly ColumnValue[]): ColumnValue[] {
  const backend = getFlowWasmBackend();
  const fn = backend ? (s: string) => backend.normalizeLineEndings(s) : normalizeLineEndingsTs;
  return mapStrings(values, fn);
}

registerTransform(
  { name: "normalize_line_endings", inputTypes: ["string"], priority: 82, mode: "series" },
  normalizeLineEndings,
);

// ---------------------------------------------------------------------------
// extract_numbers (30, string)
// ---------------------------------------------------------------------------

function extractNumbers(values: readonly ColumnValue[]): ColumnValue[] {
  const backend = getFlowWasmBackend();
  const fn = backend ? (s: string) => backend.extractNumbers(s) : extractNumbersTs;
  return mapStrings(values, fn);
}

registerTransform(
  { name: "extract_numbers", inputTypes: ["string"], priority: 30, mode: "series" },
  extractNumbers,
);

// ---------------------------------------------------------------------------
// Pure-TS single-value exports (cross-surface byte-parity harness)
//
// Bypass the wasm-dispatch wrappers above so a parity test can assert the
// pure-TS path independently of whatever backend is currently registered.
// ---------------------------------------------------------------------------

export {
  stripTs,
  collapseWhitespaceTs,
  normalizeQuotesTs,
  normalizeLineEndingsTs,
  removeHtmlTagsTs,
  removeUrlsTs,
  removeDigitsTs,
  removePunctuationTs,
  removeEmojisTs,
  extractNumbersTs,
  truncateTs,
  padLeftTs,
  padRightTs,
  lowercaseTs,
  uppercaseTs,
  titleCaseTs,
  normalizeUnicodeTs,
  fixMojibakeTs,
};
