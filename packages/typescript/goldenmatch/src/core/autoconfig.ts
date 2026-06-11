/**
 * autoconfig.ts — Auto-generate a GoldenMatch config from sample data.
 * Edge-safe: no `node:` imports.
 *
 * Ports goldenmatch/core/autoconfig.py. Profiles the rows, classifies
 * columns, and builds exact/weighted matchkeys + blocking config.
 */

import type {
  Row,
  GoldenMatchConfig,
  MatchkeyConfig,
  MatchkeyField,
  BlockingKeyConfig,
  BlockingConfig,
} from "./types.js";
import {
  makeConfig,
  makeMatchkeyConfig,
  makeMatchkeyField,
  makeBlockingConfig,
  makeGoldenRulesConfig,
} from "./types.js";
import { profileRows, type ColumnProfile, type DatasetProfile } from "./profiler.js";
import { detectDomain } from "./domain.js";
import { preflight, ConfigValidationError } from "./autoconfigVerify.js";
import { isAvailable as givenNamesAvailable } from "./refdata/givenNames.js";
import { isAvailable as surnamesAvailable } from "./refdata/surnames.js";

// Port of refdata.autoconfig_hooks._LAST_NAME_RE.
const LAST_NAME_RE =
  /(^last.?name|^l.?name|^lname|surname|family.?name|^last$|^surname$|^family$)/i;
// Port of refdata.autoconfig_hooks._FIRST_NAME_RE.
const FIRST_NAME_RE =
  /(^first.?name|^f.?name|^fname|given.?name|forename|^first$|^given$)/i;
const STRING_SIM_SCORERS = new Set([
  "jaro_winkler",
  "levenshtein",
  "token_sort",
  "ensemble",
  "dice",
  "jaccard",
]);

/**
 * Refdata refine (port of refine_matchkey_field): swap a string-similarity
 * scorer to a refdata-aware name scorer. Last-name is checked BEFORE first-name
 * (mirrors Python's if/elif order). colType is the Python col_type key.
 *
 * Note: Python's gate also passes when col_type is None; both TS call sites pass
 * a concrete colType, so the None pass-through is intentionally omitted.
 */
function refineNameScorer(
  columnName: string,
  scorer: string,
  colType: string,
): string {
  if (STRING_SIM_SCORERS.has(scorer) && (colType === "name" || colType === "multi_name")) {
    if (LAST_NAME_RE.test(columnName) && surnamesAvailable()) {
      return "name_freq_weighted_jw";
    }
    if (FIRST_NAME_RE.test(columnName) && givenNamesAvailable()) {
      return "given_name_aliased_jw";
    }
  }
  return scorer;
}

// ---------------------------------------------------------------------------
// Options
// ---------------------------------------------------------------------------

export interface AutoconfigOptions {
  readonly llmProvider?: string;
  readonly llmAuto?: boolean;
  /** If true, stamps `_strictAutoconfig: true` onto the returned config so
   *  postflight skips threshold auto-adjustment. */
  readonly strict?: boolean;
  /** If true, preflight preserves `embedding` / `record_embedding` scorers
   *  instead of demoting them. Use when callers have opted into remote
   *  model downloads. */
  readonly allowRemoteAssets?: boolean;
  /** v0.5.0 (Python v1.7 parity): when ``true``, route the result through the
   *  iterative ``AutoConfigController`` instead of returning the single-pass
   *  heuristic config. Default ``false`` to preserve pre-0.5.0 behavior. */
  readonly iterate?: boolean;
}

// ---------------------------------------------------------------------------
// Name-based classification patterns (authoritative over data profiling for
// some signals — matches Python's _DATE_PATTERNS / _GEO_PATTERNS behavior).
// ---------------------------------------------------------------------------

const EMAIL_NAME_PATTERNS = [/email/i, /e_mail/i, /e-mail/i];
const PHONE_NAME_PATTERNS = [/phone/i, /tel(?!e)/i, /mobile/i, /cell/i];
const NAME_NAME_PATTERNS = [/name/i, /first/i, /last/i, /full_name/i, /surname/i];
const ZIP_NAME_PATTERNS = [/zip/i, /postal/i, /postcode/i];
const GEO_NAME_PATTERNS = [
  /^city/i,
  /city_desc/i,
  /^state/i,
  /state_cd/i,
  /county/i,
  /country/i,
  /^region/i,
  /province/i,
];
const DATE_NAME_PATTERNS = [
  /date/i,
  /created/i,
  /modified/i,
  /updated/i,
  /_at$/i,
  /birth(?!_year)/i, // "birth" but not "birth_year" — year takes precedence
  /dob/i,
];
const YEAR_NAME_PATTERNS = [/(^|_)(year|yr)(_|$)/i];
const ID_NAME_PATTERNS = [
  /^id$/i,
  /_id$/i,
  /uuid/i,
  /guid/i,
  // v0.3 additions — targeted suffixes + whole-name anchors. Deliberately
  // NOT adding /_(no|num)$/ alone — would false-positive on yes_no, num_kids.
  /_(ref|ref_num|reg_num|account_no|account_num|account)$/i,
  /^(account_no|account_num)$/i,
  /^guid_/i,
  /^uuid_/i,
];

// Re-exported for consumers that wanted the spec-level constants.
export const EMAIL_PATTERNS = EMAIL_NAME_PATTERNS;
export const PHONE_PATTERNS = PHONE_NAME_PATTERNS;
export const NAME_PATTERNS = NAME_NAME_PATTERNS;
export const ZIP_PATTERNS = ZIP_NAME_PATTERNS;
export const GEO_PATTERNS = GEO_NAME_PATTERNS;
export const DATE_PATTERNS = DATE_NAME_PATTERNS;
export const ID_PATTERNS = ID_NAME_PATTERNS;

function nameMatches(name: string, patterns: readonly RegExp[]): boolean {
  return patterns.some((re) => re.test(name));
}

// ---------------------------------------------------------------------------
// Column classification (authoritative: date > geo > name heuristics)
// ---------------------------------------------------------------------------

type ClassifiedKind =
  | "email"
  | "phone"
  | "zip"
  | "geo"
  | "date"
  | "year"
  | "name"
  | "multi_name"
  | "id"
  | "numeric"
  | "text";

function classifyColumn(profile: ColumnProfile): ClassifiedKind {
  const name = profile.name;

  // Cardinality guard (spec §5.2) — a column where virtually every value is
  // unique cannot be a phone, zip, or numeric feature UNLESS its name
  // explicitly asserts it (e.g. "phone" or "zip"). Scoped to samples >= 10
  // to avoid false positives on tiny fixtures. Only overrides data-heuristic
  // classifications; explicit name patterns still win below.
  if (
    profile.totalCount >= 10 &&
    profile.cardinalityRatio >= 0.95 &&
    !nameMatches(name, EMAIL_NAME_PATTERNS) &&
    !nameMatches(name, PHONE_NAME_PATTERNS) &&
    !nameMatches(name, ZIP_NAME_PATTERNS) &&
    !nameMatches(name, NAME_NAME_PATTERNS) &&
    !nameMatches(name, GEO_NAME_PATTERNS) &&
    !nameMatches(name, DATE_NAME_PATTERNS) &&
    !nameMatches(name, YEAR_NAME_PATTERNS) &&
    profile.inferredType !== "year" &&
    (profile.inferredType === "phone" ||
      profile.inferredType === "zip" ||
      profile.inferredType === "numeric")
  ) {
    return "id";
  }

  // Year checked before date so "birth_year" routes to year, not date.
  if (nameMatches(name, YEAR_NAME_PATTERNS)) return "year";
  if (profile.inferredType === "year") return "year";

  // Date is checked first so that date-like columns never get misclassified
  // as phones by the profiler's value heuristic.
  if (nameMatches(name, DATE_NAME_PATTERNS)) return "date";
  if (profile.inferredType === "date") return "date";

  if (nameMatches(name, GEO_NAME_PATTERNS)) return "geo";
  if (profile.inferredType === "geo") return "geo";

  if (nameMatches(name, EMAIL_NAME_PATTERNS) || profile.inferredType === "email") {
    return "email";
  }
  if (nameMatches(name, PHONE_NAME_PATTERNS) || profile.inferredType === "phone") {
    return "phone";
  }
  if (nameMatches(name, ZIP_NAME_PATTERNS) || profile.inferredType === "zip") {
    return "zip";
  }
  // Multi-name (delimited author/entity list) checked before plain name so
  // "authors" column with comma-separated values routes to token_sort, not
  // jaro_winkler.
  if (profile.inferredType === "multi_name") return "multi_name";
  if (nameMatches(name, NAME_NAME_PATTERNS) || profile.inferredType === "name") {
    return "name";
  }
  if (nameMatches(name, ID_NAME_PATTERNS) || profile.inferredType === "id") {
    return "id";
  }
  if (profile.inferredType === "numeric") return "numeric";
  return "text";
}

// ---------------------------------------------------------------------------
// Heuristic builders
// ---------------------------------------------------------------------------

/** Python `_SCORER_MAP` parity — col_type → (scorer, weight, transforms). */
const SCORER_MAP: Readonly<
  Record<string, readonly [string, number, readonly string[]]>
> = {
  email: ["exact", 1.0, ["lowercase", "strip"]],
  phone: ["exact", 0.8, ["digits_only"]],
  zip: ["exact", 0.5, ["strip"]],
  name: ["ensemble", 1.0, ["lowercase", "strip"]],
  address: ["token_sort", 0.8, ["lowercase", "strip"]],
  identifier: ["exact", 1.0, ["strip"]],
  geo: ["exact", 0.3, ["lowercase", "strip"]],
  string: ["token_sort", 0.5, ["lowercase", "strip"]],
};

/** Map TS ClassifiedKind → Python-equivalent col_type key into SCORER_MAP. */
function colTypeFor(kind: ClassifiedKind): string {
  if (kind === "email") return "email";
  if (kind === "phone") return "phone";
  if (kind === "zip") return "zip";
  if (kind === "name") return "name";
  if (kind === "multi_name") return "multi_name"; // handled outside map
  if (kind === "geo") return "geo";
  if (kind === "id") return "identifier";
  if (kind === "text") return "string";
  return "skip"; // numeric, date, year, etc.
}

function buildExactMatchkeys(
  profiles: readonly ColumnProfile[],
): MatchkeyConfig[] {
  const out: MatchkeyConfig[] = [];
  for (const p of profiles) {
    const kind = classifyColumn(p);
    const colType = colTypeFor(kind);
    // Skip non-matchable columns (Python parity).
    if (colType === "skip" || kind === "multi_name") continue;
    const info = SCORER_MAP[colType];
    if (info === undefined) continue;
    const [scorer, , transforms] = info;
    if (scorer !== "exact") continue;
    // zip/geo are blocking signals, not identity claims.
    if (colType === "zip" || colType === "geo") continue;
    // Exact matchkeys need plausibly-unique values.
    if (p.cardinalityRatio > 0 && p.cardinalityRatio < 0.5) continue;
    if (p.nullRate > 0.4) continue;
    if (p.cardinalityRatio < 0.01) continue;

    // Python emits exact MatchkeyField with field+transforms only (scorer
    // and weight default to None on the Pydantic side). TS `MatchkeyField`
    // requires non-optional scorer/weight; we stamp "exact"/1.0 here and
    // normalize them away in the byte-equal parity test.
    out.push(
      makeMatchkeyConfig({
        name: `exact_${p.name}`,
        type: "exact",
        fields: [
          makeMatchkeyField({
            field: p.name,
            transforms: [...transforms],
            scorer: "exact",
            weight: 1.0,
          }),
        ],
      }),
    );
  }
  return out;
}

/** Python ``_adaptive_threshold`` parity. */
function adaptiveThreshold(fields: readonly MatchkeyField[]): number {
  const exactScorers = new Set(["exact"]);
  const embeddingScorers = new Set(["embedding", "record_embedding"]);
  const scorers = new Set<string>();
  for (const f of fields) if (f.scorer) scorers.add(f.scorer);
  let allExact = scorers.size > 0;
  for (const s of scorers) if (!exactScorers.has(s)) allExact = false;
  if (allExact) return 0.95;
  for (const s of scorers) if (embeddingScorers.has(s)) return 0.7;
  if (fields.length === 1) return 0.85;
  return 0.8;
}

function buildWeightedMatchkey(
  profiles: readonly ColumnProfile[],
): MatchkeyConfig | null {
  // Python's `build_matchkeys` splits columns into exact vs fuzzy via
  // SCORER_MAP. Exact-mapped columns become their own `exact_<col>`
  // matchkeys (handled by buildExactMatchkeys); fuzzy-mapped columns are
  // all combined into one weighted matchkey named "fuzzy_match".
  const fuzzy: MatchkeyField[] = [];

  for (const p of profiles) {
    const kind = classifyColumn(p);
    if (p.nullRate > 0.5) continue;
    const colType = colTypeFor(kind);

    if (kind === "multi_name") {
      fuzzy.push({
        field: p.name,
        // Python's multi_name branch hardcodes token_sort and does NOT call the
        // refdata refine (it early-exits before the refine call); keep parity.
        scorer: "token_sort",
        weight: 1.0,
        transforms: ["lowercase", "strip"],
      });
      continue;
    }

    if (colType === "skip") continue;
    const info = SCORER_MAP[colType];
    if (info === undefined) continue;
    const [scorer, weight, transforms] = info;

    // Exact-scorer columns: handled by buildExactMatchkeys when cardinality
    // permits. Skip in the weighted branch.
    if (scorer === "exact") continue;

    fuzzy.push({
      field: p.name,
      scorer: refineNameScorer(p.name, scorer, colType),
      weight,
      transforms: [...transforms],
    });
  }

  if (fuzzy.length === 0) return null;

  // Confidence-gated weight cap (Python parity §5.5). Only cap when
  // existing weight > 0.3.
  const profileByName: Record<string, ColumnProfile> = {};
  for (const p of profiles) profileByName[p.name] = p;
  const capped: MatchkeyField[] = fuzzy.map((f) => {
    const prof = profileByName[f.field];
    if (
      prof !== undefined &&
      prof.confidence < 0.5 &&
      (f.weight ?? 0) > 0.3
    ) {
      return { ...f, weight: 0.3 };
    }
    return f;
  });

  return makeMatchkeyConfig({
    name: "fuzzy_match",
    type: "weighted",
    fields: capped,
    threshold: adaptiveThreshold(capped),
    rerank: false,
  });
}

function buildBlocking(profiles: readonly ColumnProfile[]): BlockingConfig {
  // Python parity: prefer exact-eligible high-cardinality columns
  // (email/phone/zip/identifier/year) with null_rate<=0.20, cardinality<0.95.
  // Otherwise fall back to a name-based multi-pass blocking config.
  const MAX_NULL = 0.2;
  const exactEligible: ColumnProfile[] = [];
  const nameCols: ColumnProfile[] = [];

  for (const p of profiles) {
    if (p.nullRate > MAX_NULL) continue;
    if (p.cardinalityRatio >= 0.95) continue;
    const kind = classifyColumn(p);
    if (kind === "email" || kind === "phone" || kind === "zip" || kind === "id" || kind === "year") {
      exactEligible.push(p);
    } else if (kind === "name") {
      nameCols.push(p);
    }
  }

  if (exactEligible.length > 0) {
    // Pick highest-cardinality.
    const sorted = exactEligible
      .slice()
      .sort((a, b) => b.cardinalityRatio - a.cardinalityRatio);
    const best = sorted[0]!;
    const kind = classifyColumn(best);
    const transforms: string[] =
      kind === "email" ? ["lowercase", "strip"] : ["strip"];
    return makeBlockingConfig({
      strategy: "static",
      keys: [{ fields: [best.name], transforms }],
      maxBlockSize: 1000,
      skipOversized: true,
    });
  }

  if (nameCols.length > 0) {
    const best = nameCols[0]!.name;
    // Multi-pass mirroring Python's name-cols branch:
    //   keys=[soundex], passes=[substring:0:5, soundex, token_sort+substring:0:8]
    return makeBlockingConfig({
      strategy: "multi_pass",
      keys: [{ fields: [best], transforms: ["lowercase", "soundex"] }],
      passes: [
        { fields: [best], transforms: ["lowercase", "substring:0:5"] },
        { fields: [best], transforms: ["lowercase", "soundex"] },
        { fields: [best], transforms: ["lowercase", "token_sort", "substring:0:8"] },
      ],
      maxBlockSize: 1000,
      skipOversized: true,
    });
  }

  // Last-resort fallback: first usable column.
  for (const p of profiles) {
    if (p.nullRate > MAX_NULL) continue;
    if (p.cardinalityRatio >= 0.95) continue;
    if (p.cardinalityRatio < 0.01) continue;
    return makeBlockingConfig({
      strategy: "static",
      keys: [{ fields: [p.name], transforms: ["lowercase", "substring:0:5"] }],
      maxBlockSize: 1000,
      skipOversized: true,
    });
  }

  return makeBlockingConfig({
    strategy: "static",
    keys: profiles.length > 0
      ? [{ fields: [profiles[0]!.name], transforms: [] }]
      : [],
    maxBlockSize: 1000,
    skipOversized: true,
  });
}

// ---------------------------------------------------------------------------
// Public entry points
// ---------------------------------------------------------------------------

/**
 * Build a GoldenMatchConfig by profiling the provided rows.
 *
 * Mirrors goldenmatch.core.autoconfig.auto_configure_df. Does not apply
 * standardization rules directly — callers can merge them onto the result.
 */
export function autoConfigureRows(
  rows: readonly Row[],
  options?: AutoconfigOptions,
): GoldenMatchConfig {
  const profile: DatasetProfile = profileRows(rows);
  const profiles = profile.columns;

  const exactKeys = buildExactMatchkeys(profiles);
  let weighted = buildWeightedMatchkey(profiles);

  // Python parity: post-build adaptive threshold adjustment based on data
  // quality of the fuzzy fields.  avg_null > 0.15 → drop by 0.05 (floor 0.5);
  // else avg_len < 5 → raise by 0.05 (cap 0.95).
  if (weighted && weighted.type === "weighted") {
    const profileByName: Record<string, ColumnProfile> = {};
    for (const p of profiles) profileByName[p.name] = p;
    const fuzzyProfiles: ColumnProfile[] = [];
    for (const f of weighted.fields) {
      const pp = profileByName[f.field];
      if (pp !== undefined) fuzzyProfiles.push(pp);
    }
    if (fuzzyProfiles.length > 0) {
      const avgNull =
        fuzzyProfiles.reduce((acc, p) => acc + p.nullRate, 0) /
        fuzzyProfiles.length;
      const avgLen =
        fuzzyProfiles.reduce((acc, p) => acc + p.avgLength, 0) /
        fuzzyProfiles.length;
      const current = weighted.threshold;
      let next = current;
      if (avgNull > 0.15) {
        next = Math.max(current - 0.05, 0.5);
      } else if (avgLen < 5) {
        next = Math.min(current + 0.05, 0.95);
      }
      if (next !== current) {
        weighted = { ...weighted, threshold: +next.toFixed(2) };
      }
    }
  }

  const matchkeys: MatchkeyConfig[] = [...exactKeys];
  if (weighted) matchkeys.push(weighted);

  const blocking = buildBlocking(profiles);
  const goldenRules = makeGoldenRulesConfig({ defaultStrategy: "most_complete" });

  const config = makeConfig({
    matchkeys,
    blocking,
    goldenRules,
    threshold: 0.85,
    ...(options?.llmAuto !== undefined ? { llmAuto: options.llmAuto } : {}),
  });

  // Stash domain profile for preflight's domain-extracted column auto-repair
  // (Check 1). Confidence threshold matches Python — below 0.7 we do not
  // trust the detection enough to flip on config.domain automatically.
  const rowColumns = rows.length > 0 ? Object.keys(rows[0] as object) : [];
  const domainProfile = rowColumns.length > 0 ? detectDomain(rowColumns) : null;
  if (domainProfile !== null && domainProfile.confidence > 0.7) {
    (config as GoldenMatchConfig)._domainProfile = domainProfile;
  }

  const { report, config: repaired } = preflight(rows, config, {
    profiles,
    allowRemoteAssets: options?.allowRemoteAssets ?? false,
  });
  if (report.hasErrors) {
    throw new ConfigValidationError(report);
  }
  (repaired as GoldenMatchConfig)._preflightReport = report;
  if (options?.strict === true) {
    (repaired as GoldenMatchConfig)._strictAutoconfig = true;
  }
  return repaired;
}

/**
 * Convenience alias for API parity with the Python function that starts
 * from "files" (which, in edge-safe land, means pre-loaded row arrays).
 */
export function autoConfigure(
  rows: readonly Row[],
  options?: AutoconfigOptions,
): GoldenMatchConfig {
  return autoConfigureRows(rows, options);
}

/**
 * Iterative auto-config (Python v1.7 parity). Runs the
 * ``AutoConfigController`` and returns its committed config, complexity
 * profile, and full run history. Use this when ``options.iterate`` would
 * have been ``true`` but you also want access to the controller telemetry.
 *
 * Returns a Promise — the underlying TS dedupe pipeline is async.
 */
export async function autoConfigureRowsIterate(
  rows: readonly Row[],
  _options?: AutoconfigOptions,
): Promise<{
  config: GoldenMatchConfig;
  profile: import("./complexityProfile.js").ComplexityProfile;
  history: import("./autoconfigHistory.js").RunHistory;
}> {
  const { AutoConfigController } = await import("./autoconfigController.js");
  const { HeuristicRefitPolicy } = await import("./autoconfigPolicy.js");
  const { DEFAULT_RULES_V1_7_V1_8 } = await import("./autoconfigRules.js");
  const controller = new AutoConfigController({
    policy: new HeuristicRefitPolicy(DEFAULT_RULES_V1_7_V1_8),
  });
  const out = await controller.run(rows);
  return { config: out.committedConfig, profile: out.profile, history: out.history };
}
