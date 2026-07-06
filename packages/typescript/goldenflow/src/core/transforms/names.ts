/**
 * Name transforms — ported from goldenflow/transforms/names.py
 * Side-effect module: registers 10 name transforms on import.
 */

import type { ColumnValue, Row } from "../types.js";
import { registerTransform } from "./registry.js";
import { getFlowWasmBackend, type FlowWasmBackend } from "../wasm/backend.js";

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

const _TITLES = /^(Mr\.?|Mrs\.?|Ms\.?|Miss\.?|Dr\.?|Prof\.?|Rev\.?|Sr\.?|Sra\.?)\s+/i;
const _SUFFIXES = /\s+(Jr\.?|Sr\.?|II|III|IV|MD|PhD|PharmD|DDS|DVM|Esq\.?|CPA|RN|DO)$/i;
const _INITIAL_PATTERN = /\b[A-Z]\.\s/;
const _MC_PATTERN = /\bMc(\w)/g;
const _O_PATTERN = /\bO'(\w)/g;

// ---------------------------------------------------------------------------
// Nickname map
// ---------------------------------------------------------------------------

const _NICKNAMES: Record<string, string> = {
  bob: "Robert", rob: "Robert", robby: "Robert", robbie: "Robert", bobby: "Robert",
  bill: "William", billy: "William", will: "William", willy: "William",
  jim: "James", jimmy: "James", jamie: "James",
  mike: "Michael", mikey: "Michael", mick: "Michael",
  dick: "Richard", rick: "Richard", rich: "Richard", ricky: "Richard",
  tom: "Thomas", tommy: "Thomas",
  joe: "Joseph", joey: "Joseph",
  jack: "John", johnny: "John", jon: "Jonathan",
  dave: "David", davy: "David",
  steve: "Steven", stevie: "Steven",
  dan: "Daniel", danny: "Daniel",
  pat: "Patrick", patty: "Patricia", patsy: "Patricia",
  chris: "Christopher", kit: "Christopher",
  tony: "Anthony",
  ed: "Edward", eddie: "Edward", ted: "Edward", teddy: "Edward",
  al: "Albert", bert: "Albert",
  charlie: "Charles", chuck: "Charles",
  sam: "Samuel", sammy: "Samuel",
  ben: "Benjamin", benny: "Benjamin",
  matt: "Matthew",
  andy: "Andrew", drew: "Andrew",
  nick: "Nicholas",
  alex: "Alexander",
  liz: "Elizabeth", beth: "Elizabeth", betty: "Elizabeth",
  kate: "Katherine", kathy: "Katherine", katie: "Katherine",
  sue: "Susan", susie: "Susan",
  meg: "Margaret", maggie: "Margaret", peggy: "Margaret",
  jenny: "Jennifer", jen: "Jennifer",
  debbie: "Deborah", deb: "Deborah",
  barb: "Barbara",
  cindy: "Cynthia",
  sandy: "Sandra",
};

// ---------------------------------------------------------------------------
// names-remainder owned kernels (Wave D)
//
// Pure-TS references for goldenflow-core's `names::{strip_titles,strip_suffixes,
// name_proper,nickname_standardize,has_initial,split_name,split_name_reverse,
// merge_name}` kernels. MUST reproduce the Rust/Python kernel byte-for-byte:
//   - the 5 scalar transforms (strip_titles/strip_suffixes/name_proper/
//     nickname_standardize/has_initial) are asserted over
//     tests/parity/identifiers.parity.test.ts against the shared oracle corpus.
//   - the 3 multi-output transforms (split_name/split_name_reverse/merge_name)
//     are asserted over tests/unit/name-kernels.test.ts with pinned vectors.
// Ported one-for-one from the Python fallbacks in
// packages/python/goldenflow/goldenflow/transforms/names.py (kernel = spec).
// Each registered transform dispatches to the opt-in WASM backend when
// `enableWasm()` has succeeded; otherwise it runs the pure-TS `*Ts` fn below.
// Pure-TS is the default.
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// split_name (dataframe, name, 50)
// ---------------------------------------------------------------------------

/** Split "First Last" into [first, last] on the LAST space (after trimming).
 * No space -> [whole, ""]. Byte-identical to `_split_name_py`
 * (`val.strip().rsplit(" ", 1)`); the null-row case is handled by the caller. */
export function splitNameTs(s: string): [string, string] {
  const trimmed = s.trim();
  const lastSpace = trimmed.lastIndexOf(" ");
  if (lastSpace === -1) return [trimmed, ""];
  return [trimmed.slice(0, lastSpace), trimmed.slice(lastSpace + 1)];
}

function splitName(rows: readonly Row[], column: string): Row[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return rows.map((row) => {
    const val = row[column];
    if (val === null || val === undefined || typeof val !== "string") {
      return { ...row, first_name: null, last_name: null };
    }
    const [first, last] = backend ? backend.splitName(val) : splitNameTs(val);
    return { ...row, first_name: first, last_name: last };
  });
}

registerTransform(
  { name: "split_name", inputTypes: ["name"], priority: 50, mode: "dataframe" },
  splitName,
);

// ---------------------------------------------------------------------------
// split_name_reverse (dataframe, name, 50)
// ---------------------------------------------------------------------------

/** Split "Last, First" into [first, last] on the FIRST comma; each part is
 * trimmed. No comma -> [trimmed-whole, ""]. Byte-identical to
 * `_split_name_reverse_py` (`val.split(",", 1)`). */
export function splitNameReverseTs(s: string): [string, string] {
  const commaIdx = s.indexOf(",");
  if (commaIdx === -1) return [s.trim(), ""];
  return [s.slice(commaIdx + 1).trim(), s.slice(0, commaIdx).trim()];
}

function splitNameReverse(rows: readonly Row[], column: string): Row[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return rows.map((row) => {
    const val = row[column];
    if (val === null || val === undefined || typeof val !== "string") {
      return { ...row, first_name: null, last_name: null };
    }
    const [first, last] = backend ? backend.splitNameReverse(val) : splitNameReverseTs(val);
    return { ...row, first_name: first, last_name: last };
  });
}

registerTransform(
  { name: "split_name_reverse", inputTypes: ["name"], priority: 50, mode: "dataframe" },
  splitNameReverse,
);

// ---------------------------------------------------------------------------
// strip_titles (series, name, 70, auto_apply)
// ---------------------------------------------------------------------------

/** Strip a leading personal title (Mr/Mrs/Ms/Miss/Dr/Prof/Rev/Sr/Sra) then
 * trim. Byte-identical to `_strip_titles_py` (`_TITLES.sub("", val).strip()`). */
export function stripTitlesTs(s: string): string {
  return s.replace(_TITLES, "").trim();
}

function stripTitles(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapStrings(values, backend ? (s) => backend.stripTitles(s) : stripTitlesTs);
}

registerTransform(
  { name: "strip_titles", inputTypes: ["name"], autoApply: true, priority: 70, mode: "series" },
  stripTitles,
);

// ---------------------------------------------------------------------------
// strip_suffixes (series, name, 60)
// ---------------------------------------------------------------------------

/** Strip a trailing professional suffix (Jr/Sr/II/.../DO) then trim.
 * Byte-identical to `_strip_suffixes_py` (`_SUFFIXES.sub("", val).strip()`). */
export function stripSuffixesTs(s: string): string {
  return s.replace(_SUFFIXES, "").trim();
}

function stripSuffixes(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapStrings(values, backend ? (s) => backend.stripSuffixes(s) : stripSuffixesTs);
}

registerTransform(
  { name: "strip_suffixes", inputTypes: ["name"], priority: 60, mode: "series" },
  stripSuffixes,
);

// ---------------------------------------------------------------------------
// name_proper (series, name, 45)
// ---------------------------------------------------------------------------

/** Unicode `Alphabetic` property test -- mirrors Rust `char::is_alphabetic`
 * (the reference for `ascii_title`). */
const _ALPHA = /\p{Alphabetic}/u;

/** ASCII-semantics `str.title()`: the first alphabetic char of each word is
 * upper-cased, the rest lower-cased; non-alphabetic chars pass through and
 * reset the word boundary. Ported from `goldenflow-core::names::ascii_title`
 * (JS has no `String.prototype.title()`); matches Python `str.title()` on
 * ASCII. Iterates by Unicode code point (`for...of`) to mirror Python's
 * `for c in val` / Rust's `s.chars()`. */
function asciiTitle(s: string): string {
  let out = "";
  let prevAlpha = false;
  for (const c of s) {
    if (_ALPHA.test(c)) {
      out += prevAlpha ? c.toLowerCase() : c.toUpperCase();
      prevAlpha = true;
    } else {
      out += c;
      prevAlpha = false;
    }
  }
  return out;
}

/** Proper-case a name: title-case then the "Mc"/"O'" capitalization fixups.
 * Byte-identical to `_name_proper_py` (`val.title()` -> Mc sub -> O' sub). */
export function nameProperTs(s: string): string {
  let result = asciiTitle(s);
  result = result.replace(_MC_PATTERN, (_match, letter: string) => `Mc${letter.toUpperCase()}`);
  result = result.replace(_O_PATTERN, (_match, letter: string) => `O'${letter.toUpperCase()}`);
  return result;
}

function nameProper(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapStrings(values, backend ? (s) => backend.nameProper(s) : nameProperTs);
}

registerTransform(
  { name: "name_proper", inputTypes: ["name"], priority: 45, mode: "series" },
  nameProper,
);

// ---------------------------------------------------------------------------
// initial_expand (series, name, 40)
// ---------------------------------------------------------------------------

/** True if `s` contains a middle-initial pattern `\b[A-Z]\.\s`. Byte-identical
 * to `_has_initial_py` (`_INITIAL_PATTERN.search(val)`). The flag predicate
 * behind `initial_expand` (the value output is the input unchanged). */
export function hasInitialTs(s: string): boolean {
  return _INITIAL_PATTERN.test(s);
}

function initialExpand(values: readonly ColumnValue[]): [ColumnValue[], number[]] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  const hasInit = backend ? (s: string) => backend.hasInitial(s) : hasInitialTs;
  const flagged: number[] = [];
  const result: ColumnValue[] = values.map((v, i) => {
    if (v !== null && typeof v === "string" && hasInit(v)) {
      flagged.push(i);
    }
    return v === undefined ? null : (v as ColumnValue);
  });
  return [result, flagged];
}

registerTransform(
  { name: "initial_expand", inputTypes: ["name"], priority: 40, mode: "series" },
  initialExpand,
);

// ---------------------------------------------------------------------------
// nickname_standardize (series, name, 42)
// ---------------------------------------------------------------------------

/** Map a common nickname (trimmed+lowercased key) to its formal first name;
 * unknown names pass through UNCHANGED (the original, not the trimmed key).
 * Byte-identical to `_nickname_standardize_py`
 * (`_NICKNAMES.get(val.strip().lower(), val)`). */
export function nicknameStandardizeTs(s: string): string {
  return _NICKNAMES[s.trim().toLowerCase()] ?? s;
}

function nicknameStandardize(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapStrings(values, backend ? (s) => backend.nicknameStandardize(s) : nicknameStandardizeTs);
}

registerTransform(
  { name: "nickname_standardize", inputTypes: ["name"], priority: 42, mode: "series" },
  nicknameStandardize,
);

// ---------------------------------------------------------------------------
// merge_name (dataframe, name, 45, param: last_name_col="last_name")
// ---------------------------------------------------------------------------

/** Merge (first, last) into a full name: join the parts that are present and
 * non-blank (after trimming) with a single space, keeping each part's ORIGINAL
 * (unstripped) text; `undefined` if both are absent/blank. Byte-identical to
 * `_merge_name_py` (mirrors Rust `Option::None` -> `undefined`). */
export function mergeNameTs(first: string | null, last: string | null): string | undefined {
  const parts: string[] = [];
  for (const p of [first, last]) {
    if (p !== null && p.trim() !== "") parts.push(p);
  }
  return parts.length > 0 ? parts.join(" ") : undefined;
}

function mergeName(
  rows: readonly Row[],
  column: string,
  lastNameCol: unknown = "last_name",
): Row[] {
  const lnCol = typeof lastNameCol === "string" ? lastNameCol : "last_name";

  // If no rows or first row lacks the last_name column, return unchanged
  if (rows.length > 0 && !(lnCol in rows[0]!)) {
    return rows.map((r) => ({ ...r }));
  }

  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return rows.map((row) => {
    const first = row[column];
    const last = row[lnCol];
    const firstStr = first === null || first === undefined ? null : String(first);
    const lastStr = last === null || last === undefined ? null : String(last);
    const full = backend
      ? backend.mergeName(firstStr, lastStr)
      : mergeNameTs(firstStr, lastStr);
    return { ...row, full_name: full ?? null };
  });
}

registerTransform(
  { name: "merge_name", inputTypes: ["name"], priority: 45, mode: "dataframe" },
  mergeName,
);

// ---------------------------------------------------------------------------
// name_transliterate (owned i18n-name kernel)
//
// Pure-TS reference for goldenflow-core's `names::name_transliterate`
// kernel. MUST reproduce the Rust/Python kernel byte-for-byte (asserted by
// tests/parity/identifiers.parity.test.ts over
// tests/parity/identifiers_corpus.jsonl -- see
// packages/python/goldenflow/goldenflow/transforms/names.py for the
// canonical map this was copied from) -- same explicit char map, same
// ASCII-passthrough, same "drop unmapped non-ASCII" behavior. Deliberately
// NOT implemented via a Unicode normalization library -- that could drift
// from the Rust oracle by the runtime's bundled Unicode version; this map
// must stay byte-identical to
// `goldenflow-core/src/names.rs::transliterate_char`.
// ---------------------------------------------------------------------------

const _TRANSLITERATE_MAP: Record<string, string> = {
  // acute
  "á": "a", "Á": "A", "é": "e", "É": "E",
  "í": "i", "Í": "I", "ó": "o", "Ó": "O",
  "ú": "u", "Ú": "U",
  // grave
  "à": "a", "À": "A", "è": "e", "È": "E",
  "ì": "i", "Ì": "I", "ò": "o", "Ò": "O",
  "ù": "u", "Ù": "U",
  // circumflex
  "â": "a", "Â": "A", "ê": "e", "Ê": "E",
  "î": "i", "Î": "I", "ô": "o", "Ô": "O",
  "û": "u", "Û": "U",
  // diaeresis
  "ä": "a", "Ä": "A", "ë": "e", "Ë": "E",
  "ï": "i", "Ï": "I", "ö": "o", "Ö": "O",
  "ü": "u", "Ü": "U",
  // tilde (a, o -- the common precomposed vowel-tilde chars)
  "ã": "a", "Ã": "A", "õ": "o", "Õ": "O",
  // ring (a -- the common precomposed vowel-ring char)
  "å": "a", "Å": "A",
  // n-tilde / c-cedilla / y-acute / y-diaeresis
  "ñ": "n", "Ñ": "N", "ç": "c", "Ç": "C",
  "ý": "y", "Ý": "Y", "ÿ": "y", "Ÿ": "Y",
  // caron/acute consonants
  "š": "s", "Š": "S", "ž": "z", "Ž": "Z",
  "ź": "z", "Ź": "Z", "č": "c", "Č": "C",
  "ć": "c", "Ć": "C", "ř": "r", "Ř": "R",
  "ě": "e", "Ě": "E",
  // ligatures / specials
  "ß": "ss", "æ": "ae", "Æ": "AE", "œ": "oe",
  "Œ": "OE", "ø": "o", "Ø": "O", "đ": "d",
  "Đ": "D", "ł": "l", "Ł": "L", "þ": "th",
  "Þ": "Th", "ð": "d", "Ð": "D",
};

/** ASCII-fold a single name value via the explicit curated diacritic map.
 * Non-ASCII chars not in the map are dropped. Iterates by Unicode code point
 * (`for...of` over a string yields code points, not UTF-16 code units) to
 * mirror Python's `for c in val` over `str` (also code-point-wise). */
function nameTransliterateTs(val: string): string {
  const out: string[] = [];
  for (const c of val) {
    if (c.codePointAt(0)! < 128) {
      out.push(c);
    } else {
      const rep = _TRANSLITERATE_MAP[c];
      if (rep !== undefined) out.push(rep);
      // else: unmapped non-ASCII -- drop.
    }
  }
  return out.join("");
}

function nameTransliterate(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapStrings(values, backend ? (s) => backend.nameTransliterate(s) : nameTransliterateTs);
}

registerTransform(
  {
    name: "name_transliterate",
    inputTypes: ["name", "string"],
    autoApply: false,
    priority: 50,
    mode: "series",
  },
  nameTransliterate,
);

// ---------------------------------------------------------------------------
// name_script (owned i18n-name kernel)
//
// Pure-TS reference for goldenflow-core's `names::name_script` kernel. MUST
// reproduce the Rust/Python kernel byte-for-byte -- same explicit Unicode
// codepoint ranges, same tie-break order. Deliberately NOT implemented via a
// general-purpose Unicode script database -- that could drift by runtime
// Unicode version; this table must stay byte-identical to
// `goldenflow-core/src/names.rs::classify_char`.
//
// Tie-break: highest per-script count wins; an EXACT count tie resolves to
// whichever label appears earliest in `_SCRIPT_PRIORITY`.
// ---------------------------------------------------------------------------

const _SCRIPT_PRIORITY: readonly string[] = [
  "Latin", "Cyrillic", "Greek", "Han", "Hiragana", "Katakana",
  "Hangul", "Arabic", "Hebrew", "Devanagari",
];

const _SCRIPT_RANGES: ReadonlyArray<readonly [string, ReadonlyArray<readonly [number, number]>]> = [
  ["Latin", [[0x41, 0x5a], [0x61, 0x7a], [0x00c0, 0x024f]]],
  ["Cyrillic", [[0x0400, 0x04ff]]],
  ["Greek", [[0x0370, 0x03ff]]],
  ["Han", [[0x4e00, 0x9fff]]],
  ["Hiragana", [[0x3040, 0x309f]]],
  ["Katakana", [[0x30a0, 0x30ff]]],
  ["Hangul", [[0xac00, 0xd7a3]]],
  ["Arabic", [[0x0600, 0x06ff]]],
  ["Hebrew", [[0x0590, 0x05ff]]],
  ["Devanagari", [[0x0900, 0x097f]]],
];

function classifyChar(c: string): string | undefined {
  const cp = c.codePointAt(0)!;
  for (const [label, ranges] of _SCRIPT_RANGES) {
    for (const [lo, hi] of ranges) {
      if (cp >= lo && cp <= hi) return label;
    }
  }
  return undefined;
}

/** Detect the dominant Unicode script in a single name value: `"Unknown"`
 * for an empty string, `"Common"` when no tracked-script char is present,
 * else the script with the highest char count (ties -> earliest in
 * `_SCRIPT_PRIORITY`). Iterates by Unicode code point to mirror Python's
 * `for c in val`. */
function nameScriptTs(val: string): string {
  if (val === "") return "Unknown";
  const counts: Record<string, number> = {};
  for (const c of val) {
    const label = classifyChar(c);
    if (label !== undefined) {
      counts[label] = (counts[label] ?? 0) + 1;
    }
  }
  if (Object.keys(counts).length === 0) return "Common";
  let bestLabel = _SCRIPT_PRIORITY[0]!;
  let bestCount = -1;
  for (const label of _SCRIPT_PRIORITY) {
    const c = counts[label] ?? 0;
    if (c > bestCount) {
      bestCount = c;
      bestLabel = label;
    }
  }
  return bestLabel;
}

function nameScript(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapStrings(values, backend ? (s) => backend.nameScript(s) : nameScriptTs);
}

registerTransform(
  {
    name: "name_script",
    inputTypes: ["name", "string"],
    autoApply: false,
    priority: 50,
    mode: "series",
  },
  nameScript,
);

// ---------------------------------------------------------------------------
// W5 breadth: name_initials + strip_middle
// ---------------------------------------------------------------------------

function nameInitialsTs(val: string): string {
  let out = "";
  for (const tok of val.split(/\s+/)) {
    if (tok.length === 0) continue;
    const first = tok[0]!;
    if ((first >= "A" && first <= "Z") || (first >= "a" && first <= "z")) {
      out += first.toUpperCase();
    }
  }
  return out;
}

function stripMiddleTs(val: string): string {
  const tokens = val.split(/\s+/).filter((t) => t.length > 0);
  if (tokens.length === 0) return "";
  if (tokens.length === 1) return tokens[0]!;
  return `${tokens[0]} ${tokens[tokens.length - 1]}`;
}

function nameInitials(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapStrings(values, backend ? (s) => backend.nameInitials(s) : nameInitialsTs);
}

function stripMiddle(values: readonly ColumnValue[]): ColumnValue[] {
  const backend: FlowWasmBackend | null = getFlowWasmBackend();
  return mapStrings(values, backend ? (s) => backend.stripMiddle(s) : stripMiddleTs);
}

registerTransform(
  { name: "name_initials", inputTypes: ["name", "string"], autoApply: false, priority: 40, mode: "series" },
  nameInitials,
);
registerTransform(
  { name: "strip_middle", inputTypes: ["name", "string"], autoApply: false, priority: 40, mode: "series" },
  stripMiddle,
);

// ---------------------------------------------------------------------------
// Pure-TS single-value exports (cross-surface byte-parity harness)
//
// Bypass the wasm-dispatch wrappers above so a parity test can assert the
// pure-TS path independently of whatever backend is currently registered.
// ---------------------------------------------------------------------------

export { nameTransliterateTs, nameScriptTs, nameInitialsTs, stripMiddleTs };
