/**
 * config/from-splink.ts — Splink -> GoldenMatch config converter.
 *
 * Faithful port of `goldenmatch/config/from_splink.py`. Accepts an already
 * PARSED Splink settings object (bare or trained) and produces a validated
 * GoldenMatchConfig + ConversionReport (+ EMResult when the input carried
 * trained m/u probabilities).
 *
 * Edge-safe: no `node:` imports, no file I/O — the core takes the parsed
 * settings object only; the CLI does file reading on the Node side.
 */

import type {
  GoldenMatchConfig,
  MatchkeyConfig,
  MatchkeyField,
  BlockingConfig,
  BlockingKeyConfig,
} from "../types.js";
import { makeMatchkeyField, makeBlockingConfig } from "../types.js";
import { parseConfig } from "./loader.js";
import type { EMResult } from "../probabilistic.js";

export type Severity = "info" | "warning" | "error";

type RawObj = Record<string, unknown>;

// ---------------------------------------------------------------------------
// recognizeLevel: Splink comparison-level `sql_condition` recognizer
// ---------------------------------------------------------------------------

// Splink's levenshtein/damerau_levenshtein comparison levels express a raw
// edit distance threshold (e.g. "<= 1"), while GoldenMatch scorers are
// normalized 0-1 similarities. There is no exact distance->similarity mapping
// without the actual string lengths, so we approximate against an assumed
// average column length: sim = max(0.0, 1 - distance / LEV_ASSUMED_LEN). This
// is flagged via RecognizedLevel.approx=true so callers can surface it as a
// lossy conversion.
const LEV_ASSUMED_LEN = 10;

// convertComparison emits its per-comparison success finding with this
// mappedTo placeholder; fromSplink()'s patchFieldPlaceholders resolves it to
// the field's final position once the matchkey is assembled. One shared
// constant so producer and consumer can't drift.
const PLACEHOLDER_PREFIX = "matchkeys[?].fields[?]";

// Column atom: Splink serializes comparison-level columns as "col_l" / "col_r"
// (the _l/_r suffix INSIDE the quotes) or bare col_l / col_r.
const COL_L = String.raw`"?([A-Za-z_]\w*)_l"?`;
const COL_R = String.raw`"?([A-Za-z_]\w*)_r"?`;

const ELSE_RE = /^ELSE$/i;
const NULL_RE = new RegExp(`^${COL_L}\\s+IS\\s+NULL\\s+OR\\s+${COL_R}\\s+IS\\s+NULL$`, "i");
const EXACT_RE = new RegExp(`^${COL_L}\\s*=\\s*${COL_R}$`, "i");
const SIM_RE = new RegExp(
  `^(jaro_winkler_similarity|jaro_winkler|jaro_similarity|jaccard)` +
    `\\s*\\(\\s*${COL_L}\\s*,\\s*${COL_R}\\s*\\)\\s*>=\\s*([0-9]*\\.?[0-9]+)$`,
  "i",
);
const DIST_RE = new RegExp(
  `^(levenshtein|damerau_levenshtein)\\s*\\(\\s*${COL_L}\\s*,\\s*${COL_R}\\s*\\)\\s*<=\\s*([0-9]+)$`,
  "i",
);

export type LevelKind = "null" | "exact" | "else" | "jaro_winkler" | "levenshtein" | "jaccard";

const SIM_KIND: Readonly<Record<string, readonly [LevelKind, boolean]>> = {
  jaro_winkler_similarity: ["jaro_winkler", false],
  jaro_winkler: ["jaro_winkler", false],
  jaro_similarity: ["jaro_winkler", true],
  jaccard: ["jaccard", false],
};

export interface RecognizedLevel {
  readonly kind: LevelKind;
  readonly column: string | null;
  readonly simThreshold: number | null;
  /** True when the mapping is an approximation (jaro->jw, distance->similarity). */
  readonly approx: boolean;
}

/**
 * Recognize a Splink comparison-level `sql_condition` string.
 *
 * Returns `null` when the SQL doesn't match any recognized shape (e.g.
 * cross-column comparisons, mismatched columns, or arbitrary SQL) so the
 * caller can report a warning and drop the level.
 */
export function recognizeLevel(sql: string, isNullLevel = false): RecognizedLevel | null {
  const sqlNorm = sql.trim().length === 0 ? "" : sql.trim().split(/\s+/).join(" ");

  if (isNullLevel) {
    // Prefer extracting the column from the SQL shape even when the
    // is_null_level flag is what really tells us this is a null level (some
    // Splink serializations put non-null-shaped SQL on the null level, e.g.
    // custom null handling) -- fall back to column=null.
    const m = NULL_RE.exec(sqlNorm);
    if (m) {
      const colL = m[1]!;
      const colR = m[2]!;
      return { kind: "null", column: colL === colR ? colL : null, simThreshold: null, approx: false };
    }
    return { kind: "null", column: null, simThreshold: null, approx: false };
  }

  if (ELSE_RE.test(sqlNorm)) {
    return { kind: "else", column: null, simThreshold: null, approx: false };
  }

  let m = NULL_RE.exec(sqlNorm);
  if (m) {
    const colL = m[1]!;
    const colR = m[2]!;
    return colL === colR ? { kind: "null", column: colL, simThreshold: null, approx: false } : null;
  }

  m = EXACT_RE.exec(sqlNorm);
  if (m) {
    const colL = m[1]!;
    const colR = m[2]!;
    return colL === colR ? { kind: "exact", column: colL, simThreshold: 1.0, approx: false } : null;
  }

  m = SIM_RE.exec(sqlNorm);
  if (m) {
    const func = m[1]!;
    const colL = m[2]!;
    const colR = m[3]!;
    const threshold = Number.parseFloat(m[4]!);
    if (colL !== colR) return null;
    const [kind, approx] = SIM_KIND[func.toLowerCase()]!;
    return { kind, column: colL, simThreshold: threshold, approx };
  }

  m = DIST_RE.exec(sqlNorm);
  if (m) {
    const colL = m[2]!;
    const colR = m[3]!;
    const distance = Number.parseInt(m[4]!, 10);
    if (colL !== colR) return null;
    const sim = Math.max(0.0, 1 - distance / LEV_ASSUMED_LEN);
    return { kind: "levenshtein", column: colL, simThreshold: sim, approx: true };
  }

  return null;
}

// ---------------------------------------------------------------------------
// ConversionFinding / ConversionReport / SplinkConversionError
// ---------------------------------------------------------------------------

export interface ConversionFinding {
  readonly severity: Severity;
  readonly splinkPath: string; // where in the Splink input, e.g. "comparisons[1].comparison_levels[3]"
  readonly message: string;
  mappedTo: string | null; // GoldenMatch destination, e.g. "matchkeys[0].fields[1]" (mutated during placeholder patching)
}

export class ConversionReport {
  readonly findings: ConversionFinding[] = [];

  info(splinkPath: string, message: string, mappedTo: string | null): void {
    this.findings.push({ severity: "info", splinkPath, message, mappedTo });
  }

  warn(splinkPath: string, message: string, mappedTo: string | null): void {
    this.findings.push({ severity: "warning", splinkPath, message, mappedTo });
  }

  error(splinkPath: string, message: string, mappedTo: string | null): void {
    this.findings.push({ severity: "error", splinkPath, message, mappedTo });
  }

  get hasWarnings(): boolean {
    return this.findings.some((f) => f.severity === "warning");
  }

  get hasErrors(): boolean {
    return this.findings.some((f) => f.severity === "error");
  }

  summary(): string {
    const counts = { info: 0, warning: 0, error: 0 };
    for (const f of this.findings) counts[f.severity] += 1;
    return `${counts.error} error(s), ${counts.warning} warning(s), ${counts.info} info note(s)`;
  }
}

/** Raised in strict mode on any lossy mapping, or always on error-severity. */
export class SplinkConversionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "SplinkConversionError";
  }
}

// ---------------------------------------------------------------------------
// convertComparison: one Splink comparisons[idx] -> MatchkeyField
// ---------------------------------------------------------------------------

interface Band {
  readonly r: RecognizedLevel;
  readonly level: RawObj;
  readonly j: number;
}

/**
 * Convert one Splink `comparisons[idx]` dict into a MatchkeyField.
 *
 * Returns `null` (with a warning finding) when the comparison can't be
 * represented as a single GoldenMatch scorer family -- e.g. mixed comparator
 * families, inconsistent columns, or no usable agree levels.
 */
export function convertComparison(comp: RawObj, idx: number, report: ConversionReport): MatchkeyField | null {
  const compPath = `comparisons[${idx}]`;
  const outputColumnName =
    (comp["output_column_name"] as string | undefined) ||
    (comp["column_name"] as string | undefined) ||
    null;

  const rawLevels: RawObj[] = Array.isArray(comp["comparison_levels"])
    ? (comp["comparison_levels"] as RawObj[])
    : [];

  const recognized: Array<{ j: number; r: RecognizedLevel; level: RawObj }> = [];
  for (let j = 0; j < rawLevels.length; j++) {
    const level = rawLevels[j]!;
    const levelPath = `${compPath}.comparison_levels[${j}]`;
    const sql = typeof level["sql_condition"] === "string" ? (level["sql_condition"] as string) : "";
    const isNull = Boolean(level["is_null_level"]);
    const r = recognizeLevel(sql, isNull);
    if (r === null) {
      report.warn(levelPath, `unrecognized sql_condition, level dropped: ${sql}`, null);
      continue;
    }
    recognized.push({ j, r, level });
  }

  let nullSeen = false;
  const bands: Band[] = [];
  for (const { j, r, level } of recognized) {
    if (r.kind === "null") {
      nullSeen = true;
    } else if (r.kind === "else") {
      continue;
    } else {
      // MatchkeyField thresholds must be in (0, 1]; a converted value outside
      // that range (degenerate levenshtein distance -> sim 0.0, or a
      // nonsense >= 1.5 threshold) can't be represented -- drop the band with
      // a warning rather than let the loader raise downstream.
      const t = r.simThreshold;
      if (t !== null && !(t > 0.0 && t <= 1.0)) {
        report.warn(
          `${compPath}.comparison_levels[${j}]`,
          `converted threshold ${t} out of range (0, 1], level dropped: ${
            (level["sql_condition"] as string | undefined) ?? ""
          }`,
          null,
        );
        continue;
      }
      bands.push({ r, level, j });
    }
  }

  if (nullSeen) {
    report.info(
      compPath,
      "Splink null level = no evidence; GoldenMatch scores nulls as disagree -- " +
        "behavior differs on sparse fields",
      null,
    );
  }

  const families = new Set<LevelKind>(bands.filter((b) => b.r.kind !== "exact").map((b) => b.r.kind));
  if (families.size > 1) {
    report.warn(
      compPath,
      `mixed comparator families ${JSON.stringify([...families].sort())} in one comparison, comparison dropped`,
      null,
    );
    return null;
  }

  if (bands.length === 0) {
    report.warn(compPath, "no usable agree levels, comparison dropped", null);
    return null;
  }

  const scorer: string = families.size > 0 ? [...families][0]! : "exact";

  const columns = new Set<string>(
    bands.filter((b) => b.r.column !== null).map((b) => b.r.column as string),
  );
  if (columns.size > 1) {
    report.warn(
      compPath,
      `inconsistent columns across levels ${JSON.stringify([...columns].sort())}, comparison dropped`,
      null,
    );
    return null;
  }
  const col = columns.size > 0 ? [...columns][0]! : outputColumnName;
  if (col === null) {
    report.warn(compPath, "no column could be determined, comparison dropped", null);
    return null;
  }

  for (const { r, level, j } of bands) {
    if (r.approx) {
      const levelPath = `${compPath}.comparison_levels[${j}]`;
      const sql = (level["sql_condition"] as string | undefined) ?? "";
      let message: string;
      if (r.kind === "levenshtein") {
        // Reconstruct the original distance from the converted sim.
        const distance = Math.round((1 - (r.simThreshold ?? 0.0)) * LEV_ASSUMED_LEN);
        message =
          `approximate mapping: edit distance <= ${distance} converted via ` +
          `sim = 1 - distance/${LEV_ASSUMED_LEN} -> ${r.simThreshold} (${sql})`;
      } else {
        message =
          `approximate mapping: jaro_similarity treated as jaro_winkler ` +
          `(threshold=${r.simThreshold}) (${sql})`;
      }
      report.warn(levelPath, message, null);
    }
  }

  const thresholdSet = new Set<number>();
  for (const { r } of bands) {
    if (r.simThreshold !== null) thresholdSet.add(r.simThreshold);
  }
  const thresholds = [...thresholdSet].sort((a, b) => b - a);
  const levelsCount = thresholds.length + 1;

  let tfAdjustment = false;
  for (const { level, j } of bands) {
    const levelPath = `${compPath}.comparison_levels[${j}]`;
    const tfCol = level["tf_adjustment_column"];
    if (tfCol) {
      if (tfCol !== col) {
        report.warn(
          levelPath,
          `tf_adjustment_column '${String(tfCol)}' differs from field column '${col}', ` +
            "dropped (GoldenMatch TF adjustment is same-column only)",
          null,
        );
      } else {
        tfAdjustment = true;
      }
    }
    const tfWeight = level["tf_adjustment_weight"];
    if (tfWeight !== undefined && tfWeight !== null && tfWeight !== 1.0) {
      report.warn(levelPath, `tf_adjustment_weight=${String(tfWeight)} dropped (not supported)`, null);
    }
  }

  const mappedTo = `${PLACEHOLDER_PREFIX} (${col})`;

  let field: MatchkeyField;
  if (levelsCount === 2) {
    if (scorer === "exact") {
      field = makeMatchkeyField({ field: col, scorer: "exact", levels: 2, tfAdjustment });
    } else {
      field = makeMatchkeyField({
        field: col,
        scorer,
        levels: 2,
        // levelsCount === 2 => thresholds.length === 1 (levelsCount =
        // thresholds.length + 1), so index 0 is always present here.
        partialThreshold: thresholds[0]!,
        tfAdjustment,
      });
    }
  } else {
    field = makeMatchkeyField({
      field: col,
      scorer,
      levels: levelsCount,
      levelThresholds: thresholds,
      tfAdjustment,
    });
  }

  report.info(compPath, `converted to field '${col}' (scorer=${scorer})`, mappedTo);
  return field;
}

// ── Blocking rules -> BlockingConfig ─────────────────────────────────────────
//
// Splink blocking rules use the l."col" / r."col" PREFIX style (unlike
// comparison levels, which use the col_l / col_r SUFFIX style handled above).
const BLOCK_COL_L = String.raw`l\."?(\w+)"?`;
const BLOCK_COL_R = String.raw`r\."?(\w+)"?`;
const BLOCK_EXACT_RE = new RegExp(`^${BLOCK_COL_L}\\s*=\\s*${BLOCK_COL_R}$`, "i");
// SUBSTR(col, start, len) is SQL's 1-based, inclusive-length form. The repo's
// `substring:<start>:<end>` transform is a Python/JS slice: value.slice(start,
// end). So SUBSTR(x, 1, 4) (chars 1-4) maps to substring:0:4 (py_start =
// sql_start - 1, py_end = py_start + sql_len).
const BLOCK_SUBSTR_RE = new RegExp(
  `^SUBSTR(?:ING)?\\s*\\(\\s*${BLOCK_COL_L}\\s*,\\s*(\\d+)\\s*,\\s*(\\d+)\\s*\\)` +
    `\\s*=\\s*SUBSTR(?:ING)?\\s*\\(\\s*${BLOCK_COL_R}\\s*,\\s*(\\d+)\\s*,\\s*(\\d+)\\s*\\)$`,
  "i",
);

interface BlockConjunct {
  readonly field: string;
  readonly transform: string | null; // e.g. "substring:0:4", or null for plain equality
}

/**
 * Strip balanced outer parentheses: '(l.a = r.a)' -> 'l.a = r.a'.
 *
 * Splink 4's `block_on(...)` serialization wraps every AND-conjunct in
 * parentheses, so after splitting on AND each conjunct arrives paren-wrapped.
 * Only strips when the opening paren's match is the final character (so
 * `SUBSTR(a) = SUBSTR(b)` is untouched).
 */
function stripOuterParens(input: string): string {
  let s = input.trim();
  while (s.startsWith("(") && s.endsWith(")")) {
    let depth = 0;
    let closesAtEnd = false;
    for (let i = 0; i < s.length; i++) {
      const ch = s[i];
      if (ch === "(") {
        depth += 1;
      } else if (ch === ")") {
        depth -= 1;
        if (depth === 0) {
          closesAtEnd = i === s.length - 1;
          break;
        }
      }
    }
    if (!closesAtEnd) break;
    s = s.slice(1, -1).trim();
  }
  return s;
}

/**
 * Recognize one top-level AND-conjunct of a Splink blocking_rule.
 *
 * Returns `null` for anything not a same-column equality or a same-column/
 * same-offset SUBSTR(...) equality (OR, cross-column, arithmetic, ranges,
 * etc. all fail to match and are rejected here). Balanced outer parentheses
 * (Splink 4 `block_on` serialization style) are stripped before matching.
 */
function recognizeBlockingConjunct(conjunctRaw: string): BlockConjunct | null {
  const conjunct = stripOuterParens(conjunctRaw);

  let m = BLOCK_SUBSTR_RE.exec(conjunct);
  if (m) {
    const colL = m[1]!;
    const startL = m[2]!;
    const lenL = m[3]!;
    const colR = m[4]!;
    const startR = m[5]!;
    const lenR = m[6]!;
    if (colL !== colR || startL !== startR || lenL !== lenR) return null;
    const sqlStart = Number.parseInt(startL, 10);
    const sqlLen = Number.parseInt(lenL, 10);
    // Degenerate args: SQL SUBSTR is 1-based, so start < 1 has no clean
    // slice equivalent (py_start=-1 would wrap); length 0 would produce an
    // empty key (one mega-block). Reject as unrecognized.
    if (sqlStart < 1 || sqlLen < 1) return null;
    const pyStart = sqlStart - 1;
    const pyEnd = pyStart + sqlLen;
    return { field: colL, transform: `substring:${pyStart}:${pyEnd}` };
  }

  m = BLOCK_EXACT_RE.exec(conjunct);
  if (m) {
    const colL = m[1]!;
    const colR = m[2]!;
    if (colL !== colR) return null;
    return { field: colL, transform: null };
  }

  return null;
}

function convertOneBlockingRule(rule: unknown, idx: number, report: ConversionReport): BlockingKeyConfig | null {
  const rulePath = `blocking_rules[${idx}]`;
  let sql: unknown;
  if (typeof rule === "object" && rule !== null && !Array.isArray(rule)) {
    const obj = rule as RawObj;
    sql = "blocking_rule" in obj ? obj["blocking_rule"] : rule;
  } else {
    sql = rule;
  }
  if (typeof sql !== "string") {
    report.warn(rulePath, `blocking rule is not a SQL string, dropped: ${JSON.stringify(sql) ?? String(sql)}`, null);
    return null;
  }
  const sqlNorm = sql.trim().length === 0 ? "" : sql.trim().split(/\s+/).join(" ");

  // Whole-rule strip handles the double-wrapped case; each conjunct is
  // deliberately stripped AGAIN inside recognizeBlockingConjunct, so neither
  // call can be "simplified" away.
  // sqlNorm collapsed all whitespace runs to single spaces above, so a
  // literal ' AND ' split is exact -- and unlike a whitespace-tolerant
  // pattern it cannot backtrack polynomially on adversarial whitespace.
  const conjuncts = stripOuterParens(sqlNorm).split(/ AND /i);
  const recognized: BlockConjunct[] = [];
  for (const conjunct of conjuncts) {
    const r = recognizeBlockingConjunct(conjunct);
    if (r === null) {
      report.warn(rulePath, `unrecognized blocking rule, dropped: ${sqlNorm}`, null);
      return null;
    }
    recognized.push(r);
  }

  // Dedupe repeated fields (order-preserving): `l.a = r.a AND l.a = r.a` is
  // one field, not two identical key components.
  const fields: string[] = [];
  for (const r of recognized) if (!fields.includes(r.field)) fields.push(r.field);

  // BlockingKeyConfig.transforms is ONE chain applied uniformly to every
  // field in the key -- there is no per-field transform slot. A mixed rule
  // (plain equality on one column + SUBSTR on another) is therefore
  // approximated as a single key carrying the substring transform for ALL
  // fields. This is safe for blocking (candidate generation only needs to be
  // a superset of true matches). If two conjuncts specify SUBSTR at
  // different offsets, there is no single chain that represents both, so the
  // rule is dropped.
  const transformValues = new Set<string>(
    recognized.filter((r) => r.transform !== null).map((r) => r.transform as string),
  );
  if (transformValues.size > 1) {
    report.warn(rulePath, `conflicting SUBSTR offsets across fields, rule dropped: ${sqlNorm}`, null);
    return null;
  }
  const transforms = transformValues.size > 0 ? [[...transformValues][0]!] : [];

  const key: BlockingKeyConfig = { fields, transforms };
  const plainFields: string[] = [];
  for (const r of recognized) {
    if (r.transform === null && !plainFields.includes(r.field)) plainFields.push(r.field);
  }
  if (transforms.length > 0 && plainFields.length > 0) {
    // LOSSY: the key-level chain applies the substring transform to field(s)
    // Splink compared with plain equality. Warn (not info) so strict=true
    // gates on it, matching the approx-warn convention used for comparison
    // levels above.
    report.warn(
      rulePath,
      `approximate mapping, blocking key widened: ${transforms[0]} applied to all fields ` +
        `including plain-equality field(s) ${JSON.stringify(plainFields)} (GoldenMatch transforms ` +
        "are key-level); candidates are a superset of Splink's, precision may drop (superset " +
        "guarantee assumes skip_oversized stays False, the converter's emitted default) " +
        `(${sqlNorm})`,
      null,
    );
  } else {
    report.info(
      rulePath,
      `converted to blocking key fields=${JSON.stringify(fields)} transforms=${JSON.stringify(transforms)}`,
      null,
    );
  }
  return key;
}

/**
 * Convert Splink `blocking_rules_to_generate_predictions` into a GoldenMatch
 * `BlockingConfig`.
 *
 * Each rule is a string (or a Splink 4 dict `{"blocking_rule": ..., ...}`).
 * One surviving rule -> `strategy="static"`; two or more -> `strategy=
 * "multi_pass"` with both `keys` and `passes` set to the same list. If every
 * rule is dropped, this is fatal: GoldenMatch probabilistic matchkeys require
 * a blocking config, so an error finding is recorded and `null` is returned
 * rather than an invalid config.
 */
export function convertBlocking(rules: readonly unknown[], report: ConversionReport): BlockingConfig | null {
  const keys: BlockingKeyConfig[] = [];
  for (let idx = 0; idx < rules.length; idx++) {
    const key = convertOneBlockingRule(rules[idx], idx, report);
    if (key !== null) keys.push(key);
  }

  if (keys.length === 0) {
    report.error("blocking_rules", "no blocking rule could be converted to a BlockingConfig key", null);
    return null;
  }

  if (keys.length === 1) {
    return makeBlockingConfig({ strategy: "static", keys });
  }
  return makeBlockingConfig({ strategy: "multi_pass", keys, passes: keys });
}

// ── Trained-model (m/u) import ───────────────────────────────────────────────
//
// Splink lists comparison levels strongest -> weakest (after the null level),
// and each non-null level MAY carry an EM-trained "m_probability" /
// "u_probability". GoldenMatch's EMResult is the mirror image: level index 0
// is disagree (Splink's ELSE), index N-1 is the strongest agree level. So
// importing m/u is a re-indexing exercise, not a re-fit.

/**
 * True if any comparison level anywhere in `settings` carries m/u.
 *
 * A bare (untrained) Splink settings dict has comparison levels with only
 * `sql_condition` (+ metadata); a trained model additionally carries
 * `m_probability` / `u_probability` floats on every non-null level. Checking
 * for the presence of the key anywhere is enough to distinguish the two
 * shapes without assuming every level was populated.
 */
export function detectTrained(settings: RawObj): boolean {
  const comparisons: RawObj[] = Array.isArray(settings["comparisons"]) ? (settings["comparisons"] as RawObj[]) : [];
  for (const comp of comparisons) {
    const levels: RawObj[] = Array.isArray(comp["comparison_levels"]) ? (comp["comparison_levels"] as RawObj[]) : [];
    for (const level of levels) {
      if ("m_probability" in level || "u_probability" in level) return true;
    }
  }
  return false;
}

/**
 * Resolve a recognized agree-band level to its GoldenMatch level index.
 *
 * Resolution is by position in the field's own (already-deduped, descending)
 * thresholds -- mirrors `convertComparison`'s threshold derivation. Returns
 * `null` when the level's threshold matches none of the field's converted
 * thresholds (its m/u mass is dropped by the caller).
 */
function agreeIndexFor(r: RecognizedLevel, fld: MatchkeyField): number | null {
  const levels = fld.levels ?? 2;
  if (levels === 2) {
    if (fld.scorer === "exact") {
      return r.kind === "exact" ? 1 : null;
    }
    if (r.simThreshold === null || fld.partialThreshold === undefined) return null;
    return Math.abs(r.simThreshold - fld.partialThreshold) < 1e-9 ? 1 : null;
  }
  if (r.simThreshold === null) return null;
  const thresholds = fld.levelThresholds ?? [];
  for (let i = 0; i < thresholds.length; i++) {
    if (Math.abs(thresholds[i]! - r.simThreshold) < 1e-9) return levels - 1 - i;
  }
  return null;
}

export interface ImportEmComparison {
  readonly comp: RawObj;
  readonly compIdx: number;
  readonly field: MatchkeyField;
}

/**
 * Import trained m/u probabilities into an `EMResult`.
 *
 * `comparisons` is the explicit alignment the caller (`fromSplink`) must
 * build: one `{comp, compIdx, field}` per Splink comparison that
 * `convertComparison` successfully turned into a `MatchkeyField`.
 *
 * Returns `null` when no level in any comparison carries m/u at all (a bare,
 * untrained settings dict) or when nothing importable survives.
 */
export function importEm(
  comparisons: readonly ImportEmComparison[],
  settings: RawObj,
  report: ConversionReport,
): EMResult | null {
  if (!detectTrained(settings)) return null;

  const mProbs: Record<string, number[]> = {};
  const uProbs: Record<string, number[]> = {};
  const epsilon = 1e-6;

  for (const { comp, compIdx, field: fld } of comparisons) {
    const fieldName = fld.field;
    const n = fld.levels ?? 2;
    const mAcc = new Array<number>(n).fill(0);
    const uAcc = new Array<number>(n).fill(0);
    const assigned = new Array<boolean>(n).fill(false);
    let lostM = 0;
    let lostU = 0;
    let hadAnyProb = false;
    const compPath = `comparisons[${compIdx}]`;

    const levels: RawObj[] = Array.isArray(comp["comparison_levels"]) ? (comp["comparison_levels"] as RawObj[]) : [];
    for (let j = 0; j < levels.length; j++) {
      const level = levels[j]!;
      const levelPath = `${compPath}.comparison_levels[${j}]`;
      let mP = typeof level["m_probability"] === "number" ? (level["m_probability"] as number) : undefined;
      let uP = typeof level["u_probability"] === "number" ? (level["u_probability"] as number) : undefined;
      if (mP === undefined && uP === undefined) continue;
      hadAnyProb = true;

      // Partial data: a level carrying only one side (m without u, or u
      // without m). Silently treating the missing side as 0.0 would skew
      // log2(m/u) hard; floor it with epsilon and warn instead.
      if (mP === undefined || uP === undefined) {
        const missingSide = mP === undefined ? "m_probability" : "u_probability";
        const mappedPrefix = missingSide === "m_probability" ? "em.m_probs" : "em.u_probs";
        report.warn(
          levelPath,
          `level carries partial trained data (${missingSide} missing) for field '${fld.field}'; ` +
            `missing side filled with epsilon (${epsilon})`,
          `${mappedPrefix}.${fld.field}`,
        );
        if (mP === undefined) mP = epsilon;
        else uP = epsilon;
      }

      const isNull = Boolean(level["is_null_level"]);
      const sql = typeof level["sql_condition"] === "string" ? (level["sql_condition"] as string) : "";
      const r = recognizeLevel(sql, isNull);

      if (r === null) {
        // Unrecognized level (already dropped by convertComparison when
        // building the field) -- its m/u mass is lost.
        lostM += mP ?? 0;
        lostU += uP ?? 0;
        report.warn(
          levelPath,
          `unrecognized level carried m/u probabilities; dropped, surviving levels for ` +
            `field '${fld.field}' re-normalized`,
          `em.m_probs.${fld.field}`,
        );
        continue;
      }

      if (r.kind === "null") {
        // Splink convention: null levels carry no evidentiary m/u. Ignore
        // even if present rather than let them participate.
        continue;
      }

      let idx: number;
      if (r.kind === "else") {
        idx = 0;
      } else {
        const resolved = agreeIndexFor(r, fld);
        if (resolved === null) {
          lostM += mP ?? 0;
          lostU += uP ?? 0;
          report.warn(
            levelPath,
            `level threshold ${r.simThreshold} does not match any converted threshold for ` +
              `field '${fld.field}'; m/u dropped, surviving levels re-normalized`,
            `em.m_probs.${fld.field}`,
          );
          continue;
        }
        idx = resolved;
      }

      // Two Splink levels can collapse onto the same GoldenMatch index
      // (threshold dedupe) -- sum their m/u rather than overwrite, and warn:
      // the collapse is lossy (two Splink levels become one GoldenMatch
      // level).
      if (assigned[idx]) {
        report.warn(
          levelPath,
          `level collapsed onto GoldenMatch level ${idx} of field '${fld.field}' ` +
            "(duplicate threshold after dedupe); m/u probabilities summed with the earlier level's",
          `em.m_probs.${fld.field}`,
        );
      }
      mAcc[idx] = mAcc[idx]! + (mP ?? 0);
      uAcc[idx] = uAcc[idx]! + (uP ?? 0);
      assigned[idx] = true;
    }

    if (!hadAnyProb) {
      // This comparison carried no trained data at all (mixed bare/trained
      // input); nothing to import for this field. The resulting model is
      // PARTIAL, so surface it loudly rather than skipping silently.
      report.warn(
        compPath,
        `comparison for field '${fld.field}' carries no trained m/u while other comparisons ` +
          `do (mixed bare/trained input); the imported model will NOT cover field '${fld.field}', ` +
          "and using it via model_path with this partial model will fail validation at runtime",
        null,
      );
      continue;
    }

    if (lostM || lostU) {
      report.warn(
        compPath,
        `re-normalizing m/u probabilities for field '${fld.field}' after dropping unrecognized level(s)`,
        `em.m_probs.${fld.field}`,
      );
    }

    for (let i = 0; i < n; i++) {
      if (!assigned[i]) {
        mAcc[i] = epsilon;
        uAcc[i] = epsilon;
        report.warn(
          compPath,
          `field '${fld.field}' level ${i} had no m/u probability assigned from the Splink model; ` +
            "filled with epsilon",
          `em.m_probs.${fld.field}`,
        );
      }
    }

    const sumM = mAcc.reduce((a, b) => a + b, 0);
    const sumU = uAcc.reduce((a, b) => a + b, 0);
    const mFinal = sumM > 0 ? mAcc.map((v) => v / sumM) : new Array<number>(n).fill(1 / n);
    const uFinal = sumU > 0 ? uAcc.map((v) => v / sumU) : new Array<number>(n).fill(1 / n);

    mProbs[fieldName] = mFinal;
    uProbs[fieldName] = uFinal;
  }

  if (Object.keys(mProbs).length === 0) return null;

  // Splink model exports carry no term-frequency tables, so an imported
  // EMResult always has tfFreqs=null -- tfAdjustment on a converted field
  // silently no-ops until the model is retrained. Say so.
  const tfFields = comparisons.filter((c) => c.field.tfAdjustment).map((c) => c.field.field);
  if (tfFields.length > 0) {
    const uniqueSorted = [...new Set(tfFields)].sort();
    report.info(
      "comparisons",
      "term-frequency tables are not part of a Splink model export; tf_adjustment on field(s) " +
        `${uniqueSorted.join(", ")} will only take effect after retraining`,
      "em.tf_freqs",
    );
  }

  const matchWeights: Record<string, number[]> = {};
  for (const f of Object.keys(mProbs)) {
    matchWeights[f] = mProbs[f]!.map((m, i) => Math.log2(Math.max(m, 1e-10) / Math.max(uProbs[f]![i]!, 1e-10)));
  }

  let proportionMatched: number;
  if (typeof settings["probability_two_random_records_match"] === "number") {
    proportionMatched = settings["probability_two_random_records_match"] as number;
  } else {
    proportionMatched = 0.05;
    report.info(
      "probability_two_random_records_match",
      "probability_two_random_records_match absent from trained settings; assumed default 0.05",
      "em.proportion_matched",
    );
  }

  return {
    m: mProbs,
    u: uProbs,
    matchWeights,
    converged: true,
    iterations: 0,
    proportionMatched,
    tfFreqs: null,
    tfCollision: null,
  };
}

// ── Settings scalar mapping ──────────────────────────────────────────────────

const INFRA_IGNORED_KEYS = [
  "sql_dialect",
  "retain_matching_columns",
  "retain_intermediate_calculation_columns",
  "bayes_factor_column_prefix",
] as const;

export interface ScalarKwargs {
  convergenceThreshold?: number;
  emIterations?: number;
}

/**
 * Map top-level Splink settings scalars onto ProbabilisticMatchkey kwargs.
 *
 * Returns a kwargs object suitable for spreading into the matchkey; only keys
 * actually present in `settings` are included. Everything not representable
 * as a GoldenMatch config field (file paths, engine infra) is surfaced as a
 * report finding instead.
 */
export function convertScalars(settings: RawObj, report: ConversionReport): ScalarKwargs {
  const kwargs: ScalarKwargs = {};

  if ("em_convergence" in settings) {
    kwargs.convergenceThreshold = settings["em_convergence"] as number;
    report.info(
      "em_convergence",
      `em_convergence=${String(settings["em_convergence"])} -> convergence_threshold`,
      "matchkeys[?].convergence_threshold",
    );
  }

  if ("max_iterations" in settings) {
    kwargs.emIterations = settings["max_iterations"] as number;
    report.info(
      "max_iterations",
      `max_iterations=${String(settings["max_iterations"])} -> em_iterations`,
      "matchkeys[?].em_iterations",
    );
  }

  if ("unique_id_column_name" in settings) {
    const col = settings["unique_id_column_name"];
    report.info(
      "unique_id_column_name",
      `unique_id_column_name='${String(col)}' -> set input.files[*].id_column to '${String(col)}' ` +
        "(no InputConfig emitted; Splink settings carry no file paths)",
      "input.files[*].id_column",
    );
  }

  const linkType = settings["link_type"];
  if (linkType === "link_and_dedupe") {
    report.warn(
      "link_type",
      "link_type='link_and_dedupe' has no single GoldenMatch entry point -- run dedupe() on " +
        "each source then match() across sources (or vice versa) and combine the results",
      null,
    );
  } else if (linkType === "dedupe_only" || linkType === "link_only") {
    const entryPoint = linkType === "dedupe_only" ? "dedupe()" : "match()";
    report.info("link_type", `link_type='${linkType}' -> use GoldenMatch's ${entryPoint}`, entryPoint);
  } else if (linkType !== undefined && linkType !== null) {
    report.info("link_type", `unrecognized link_type=${JSON.stringify(linkType)}, ignored`, null);
  }

  for (const key of INFRA_IGNORED_KEYS) {
    if (key in settings) {
      report.info(key, `'${key}' ignored (engine infra)`, null);
    }
  }

  return kwargs;
}

// ── Public entry point ───────────────────────────────────────────────────────

const MATCHKEY_NAME = "splink_import";

/**
 * Result of `fromSplink()`.
 *
 * `emModel` (when present) is an in-memory `EMResult` only -- this call never
 * touches disk. Callers who want EM-skip-on-reuse behavior must persist it
 * themselves (`emResultToJson` + write) and set the resulting path on
 * `config.matchkeys[0].model_path` (via a subsequent config edit).
 */
export interface SplinkConversion {
  readonly config: GoldenMatchConfig;
  readonly report: ConversionReport;
  readonly emModel: EMResult | null;
}

const PREVIEW_MAX_FINDINGS = 10;

/**
 * Render findings for an exception message, capped at the first
 * PREVIEW_MAX_FINDINGS so a pathological input can't produce a multi-page
 * exception. The full report is on SplinkConversion.report (or re-runnable
 * with strict=false).
 */
function findingsPreview(findings: readonly ConversionFinding[]): string {
  const shown = findings.slice(0, PREVIEW_MAX_FINDINGS);
  let preview = shown.map((f) => `[${f.severity}] ${f.splinkPath}: ${f.message}`).join("; ");
  const remaining = findings.length - shown.length;
  if (remaining > 0) {
    preview += `; ... and ${remaining} more; rerun with strict=false for the full report`;
  }
  return preview;
}

/**
 * Resolve the `matchkeys[?].fields[?]` placeholder `convertComparison` leaves
 * on its own findings, now that the field's final position in the assembled
 * matchkey is known.
 */
function patchFieldPlaceholders(report: ConversionReport, compPath: string, fieldIdx: number): void {
  const resolved = `matchkeys[0].fields[${fieldIdx}]`;
  for (const f of report.findings) {
    if (f.splinkPath === compPath && f.mappedTo && f.mappedTo.startsWith(PLACEHOLDER_PREFIX)) {
      f.mappedTo = resolved + f.mappedTo.slice(PLACEHOLDER_PREFIX.length);
    }
  }
}

/**
 * Convert a Splink settings object into a GoldenMatch config.
 *
 * @param source - A Splink settings object (already parsed from JSON). Bare
 *   (untrained) or trained (carrying m_probability/u_probability) settings
 *   are both accepted. This core function does no file I/O -- Node-side
 *   callers (the CLI) read the file and pass the parsed object.
 * @param opts.strict - When true, ANY warning or error finding raises
 *   SplinkConversionError (a fully lossless conversion is required). When
 *   false (default), only error-severity findings raise -- e.g. zero
 *   convertible comparisons or blocking rules.
 *
 * @returns A SplinkConversion with a validated GoldenMatchConfig, the full
 *   ConversionReport, and an EMResult when the input settings were trained
 *   (null for bare settings).
 *
 * @throws SplinkConversionError on malformed input, zero convertible
 *   comparisons, zero convertible blocking rules, or (in strict mode) any
 *   lossy finding.
 */
export function fromSplink(source: unknown, opts?: { strict?: boolean }): SplinkConversion {
  const strict = opts?.strict ?? false;

  if (typeof source !== "object" || source === null || Array.isArray(source)) {
    const got = source === null ? "null" : Array.isArray(source) ? "array" : typeof source;
    throw new SplinkConversionError(`fromSplink() source must be a non-null object, got ${got}`);
  }
  const settings = source as RawObj;
  const report = new ConversionReport();

  const rawComparisons: RawObj[] = Array.isArray(settings["comparisons"])
    ? (settings["comparisons"] as RawObj[])
    : [];
  const survivors: ImportEmComparison[] = [];
  for (let idx = 0; idx < rawComparisons.length; idx++) {
    const comp = rawComparisons[idx]!;
    const field = convertComparison(comp, idx, report);
    if (field !== null) survivors.push({ comp, compIdx: idx, field });
  }

  if (survivors.length === 0) {
    report.error("comparisons", "no comparison could be converted to a MatchkeyField", null);
    throw new SplinkConversionError(`fromSplink(): zero convertible comparisons -- ${report.summary()}`);
  }

  survivors.forEach(({ compIdx }, fieldIdx) => {
    patchFieldPlaceholders(report, `comparisons[${compIdx}]`, fieldIdx);
  });

  const rawBlockingRules: unknown[] = Array.isArray(settings["blocking_rules_to_generate_predictions"])
    ? (settings["blocking_rules_to_generate_predictions"] as unknown[])
    : [];
  const blocking = convertBlocking(rawBlockingRules, report);
  if (blocking === null) {
    throw new SplinkConversionError(`fromSplink(): zero convertible blocking rules -- ${report.summary()}`);
  }

  const scalarKwargs = convertScalars(settings, report);

  const mk: MatchkeyConfig = {
    name: MATCHKEY_NAME,
    type: "probabilistic",
    fields: survivors.map((s) => s.field),
    ...(scalarKwargs.convergenceThreshold !== undefined
      ? { convergenceThreshold: scalarKwargs.convergenceThreshold }
      : {}),
    ...(scalarKwargs.emIterations !== undefined ? { emIterations: scalarKwargs.emIterations } : {}),
  };

  const emModel = importEm(survivors, settings, report);

  // parseConfig() re-validates the assembled config against the loader's own
  // invariants (transform shapes, levelThresholds descending/range, etc.) --
  // a failure here is a bug in this converter, so let it propagate loudly
  // rather than wrapping it in SplinkConversionError.
  const config = parseConfig({ matchkeys: [mk], blocking });

  if (strict && (report.hasWarnings || report.hasErrors)) {
    const preview = findingsPreview(
      report.findings.filter((f) => f.severity === "warning" || f.severity === "error"),
    );
    throw new SplinkConversionError(`fromSplink(strict=true): lossy conversion -- ${report.summary()}. ${preview}`);
  }
  if (report.hasErrors) {
    const preview = findingsPreview(report.findings.filter((f) => f.severity === "error"));
    throw new SplinkConversionError(`fromSplink(): conversion error -- ${report.summary()}. ${preview}`);
  }

  return { config, report, emModel };
}
