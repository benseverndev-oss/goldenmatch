/**
 * config-critique.ts -- deterministic config-weakness generator.
 *
 * Edge-safe port of Python `goldenmatch/core/config_critique.py::diagnose_config`.
 * After auto-config builds a config and a first run produces a result, this
 * explains -- in plain English -- what the auto-built rules did and where they
 * are risky. Read-only + deterministic; NO `node:*`, NO LLM.
 *
 * `diagnoseConfig(rows, config, result)` returns
 * `{findings: [...], summary_plain: str}` (snake_case for Python wire parity).
 * Each detector maps engine signals (the resolved config, the column profile,
 * and the postflight signals / clusters on the result) to at most one finding;
 * detectors are defensive -- a missing signal is skipped, never an error.
 *
 * The optional one-paragraph LLM summary (Python `_maybe_llm_summary`, gated on
 * `GOLDENMATCH_WEAKNESS_LLM=1`) is deliberately NOT ported -- the default,
 * offline behavior is the deterministic template summary, which this reproduces
 * faithfully.
 */

import type { Row, GoldenMatchConfig } from "./types.js";
import { getMatchkeys } from "./types.js";
import { profileRows, type ColumnProfile } from "./profiler.js";
import type { ClusterInfo } from "./types.js";
import type { PostflightSignals } from "./autoconfigVerify.js";

// -- Tunables (deterministic thresholds; mirror config_critique.py) -----------

const NULL_SINK_RATE = 0.2;
const LOW_SIGNAL_CARD = 0.01;
const ID_CARD = 0.98;
const IDENTITY_COL_TYPES = new Set(["email", "name", "phone", "address", "zip", "geo"]);
const BLOCK_P99_WARN = 5000;
const BLOCK_P99_HIGH = 50_000;
const DIST_STRONG_BLOCK_CARD = 0.5;
const DIST_LOW_BLOCK_CARD = 0.1;
const DIST_STRONG_BLOCK_TYPES = new Set(["email", "phone", "identifier"]);
const DIST_MERGE_RATE = 0.1;
const DIST_CLUSTER_P95 = 3;

const SEVERITY_RANK: Readonly<Record<string, number>> = { high: 0, medium: 1, low: 2 };

const SOURCE_NAME_RE =
  /^(source|source_system|src|origin|feed|dataset|system|provider|channel)$/i;
const ID_NAME_RE =
  /(^|_)(id|uuid|guid|pk)$|^(record_id|row_id|external_id|legacy_id|source_id|source_pk)$/i;

// -- Public types -------------------------------------------------------------

export interface CritiqueFinding {
  readonly id: string;
  readonly severity: string;
  readonly title_plain: string;
  readonly detail_plain: string;
  readonly evidence: Record<string, unknown>;
  readonly fix_plain: string;
  readonly fix_config_hint: Record<string, unknown>;
}

export interface CritiqueResult {
  readonly findings: readonly CritiqueFinding[];
  readonly summary_plain: string;
}

/** DedupeResult-like shape this reads defensively (fields may be absent). */
export interface CritiqueRunLike {
  readonly clusters?: ReadonlyMap<number, ClusterInfo>;
  readonly postflightReport?: { readonly signals?: PostflightSignals };
}

export type Phrasing = "plain" | "technical";

function round(x: number, digits: number): number {
  const f = 10 ** digits;
  return Math.round(x * f) / f;
}

function finding(
  id: string,
  severity: string,
  evidence: Record<string, unknown>,
  fixConfigHint: Record<string, unknown>,
  titlePlain: string,
  detailPlain: string,
  fixPlain: string,
): CritiqueFinding {
  return {
    id,
    severity,
    title_plain: titlePlain,
    detail_plain: detailPlain,
    evidence,
    fix_plain: fixPlain,
    fix_config_hint: fixConfigHint,
  };
}

// -- Config walks -------------------------------------------------------------

function collectReferenced(config: GoldenMatchConfig): Set<string> {
  const cols = new Set<string>();
  try {
    for (const mk of getMatchkeys(config)) {
      for (const f of mk.fields) {
        if (f.field && f.field !== "__record__") cols.add(f.field);
        for (const c of f.columns ?? []) cols.add(c);
      }
    }
  } catch {
    /* skip */
  }
  const bk = config.blocking;
  if (bk) {
    for (const attr of [bk.keys, bk.passes, bk.subBlockKeys]) {
      for (const kc of attr ?? []) for (const f of kc.fields ?? []) cols.add(f);
    }
  }
  return cols;
}

function collectBlockingFields(config: GoldenMatchConfig): string[] {
  const out = new Set<string>();
  const bk = config.blocking;
  if (!bk) return [];
  for (const attr of [bk.keys, bk.passes, bk.subBlockKeys]) {
    for (const kc of attr ?? []) for (const f of kc.fields ?? []) out.add(f);
  }
  return [...out];
}

function matchkeyColumns(config: GoldenMatchConfig): Set<string> {
  const cols = new Set<string>();
  try {
    for (const mk of getMatchkeys(config)) {
      for (const f of mk.fields) {
        if (f.field && f.field !== "__record__") cols.add(f.field);
        for (const c of f.columns ?? []) cols.add(c);
      }
    }
  } catch {
    return new Set();
  }
  return cols;
}

// -- Detectors ----------------------------------------------------------------

function detectSourceAdmitted(
  referenced: Set<string>,
  excluded: Set<string>,
  phrasing: Phrasing,
): CritiqueFinding[] {
  const out: CritiqueFinding[] = [];
  for (const col of [...referenced].sort()) {
    if (excluded.has(col)) continue;
    if (!SOURCE_NAME_RE.test(col)) continue;
    const title =
      phrasing === "technical"
        ? `Provenance column '${col}' is used as a matching signal`
        : "Two different companies may be merged";
    const detail =
      phrasing === "technical"
        ? `'${col}' looks like a source/provenance label, not an identity attribute. Using it in a matchkey or blocking pass makes records from the same feed look more alike and splits one entity across feeds.`
        : `The matcher is using the '${col}' label as a matching signal, but that label only records where a row came from. Records from the same system get treated as more similar, and the same person split across systems looks less similar.`;
    out.push(
      finding(
        "source_admitted",
        "high",
        { column: col, reason: "source/provenance label" },
        { action: "exclude_column", column: col },
        title,
        detail,
        `Stop matching on '${col}'; it just records where a row came from.`,
      ),
    );
  }
  return out;
}

function detectIdAdmitted(
  referenced: Set<string>,
  excluded: Set<string>,
  profiles: Map<string, ColumnProfile>,
  phrasing: Phrasing,
): CritiqueFinding[] {
  const out: CritiqueFinding[] = [];
  for (const col of [...referenced].sort()) {
    if (excluded.has(col)) continue;
    const prof = profiles.get(col);
    const byName = ID_NAME_RE.test(col);
    const byType = prof !== undefined && prof.inferredType === "identifier";
    const byCard =
      prof !== undefined &&
      prof.cardinalityRatio >= ID_CARD &&
      !IDENTITY_COL_TYPES.has(prof.inferredType);
    if (!(byName || byType || byCard)) continue;
    if (SOURCE_NAME_RE.test(col)) continue; // don't double-fire with source_admitted
    const why = byName
      ? "name looks like a per-row id"
      : byType
        ? "profiled as an identifier"
        : "nearly every value is unique";
    const evidence: Record<string, unknown> = { column: col, reason: why };
    if (prof !== undefined) evidence["cardinality_ratio"] = round(prof.cardinalityRatio, 4);
    const title =
      phrasing === "technical"
        ? `Identifier column '${col}' is used as a matching signal`
        : "An ID number is being used to decide who matches";
    const detail =
      phrasing === "technical"
        ? `'${col}' is a per-row id (${why}). Identifiers don't repeat across true duplicates, so as an exact key it never agrees, and as a fuzzy signal it only adds noise.`
        : `The matcher is using '${col}' to compare records, but that column is a unique ID (every row has a different value). It can't tell you which records are the same person.`;
    out.push(
      finding(
        "id_admitted",
        "high",
        evidence,
        { action: "exclude_column", column: col },
        title,
        detail,
        `Stop matching on '${col}'; it's a unique ID, not a shared trait.`,
      ),
    );
  }
  return out;
}

function detectNullSink(
  matchkeyCols: Set<string>,
  profiles: Map<string, ColumnProfile>,
  phrasing: Phrasing,
): CritiqueFinding[] {
  const out: CritiqueFinding[] = [];
  for (const col of [...matchkeyCols].sort()) {
    const prof = profiles.get(col);
    if (prof === undefined || prof.nullRate <= NULL_SINK_RATE) continue;
    const pct = Math.round(prof.nullRate * 100);
    const title =
      phrasing === "technical"
        ? `Matchkey column '${col}' is ${pct}% null`
        : `The matcher relies on a mostly-empty column ('${col}')`;
    const detail =
      phrasing === "technical"
        ? `'${col}' has null_rate ${prof.nullRate.toFixed(2)} (> ${NULL_SINK_RATE.toFixed(2)}). Most pairs have nothing to compare on this field, so it contributes little to the score.`
        : `About ${pct}% of rows have no value in '${col}', so for most records there's nothing to compare. Matching leans on it anyway, which weakens the result.`;
    out.push(
      finding(
        "null_sink",
        "medium",
        { column: col, null_rate: round(prof.nullRate, 4) },
        { action: "demote_to_blocking", column: col },
        title,
        detail,
        `Stop matching on '${col}'; it's empty for most rows.`,
      ),
    );
  }
  return out;
}

function detectLowSignalKey(
  matchkeyCols: Set<string>,
  profiles: Map<string, ColumnProfile>,
  phrasing: Phrasing,
): CritiqueFinding[] {
  const out: CritiqueFinding[] = [];
  for (const col of [...matchkeyCols].sort()) {
    const prof = profiles.get(col);
    if (prof === undefined) continue;
    if (prof.cardinalityRatio >= LOW_SIGNAL_CARD) continue;
    const title =
      phrasing === "technical"
        ? `Matchkey column '${col}' has near-zero cardinality`
        : `A column ('${col}') is almost the same for every row`;
    const detail =
      phrasing === "technical"
        ? `'${col}' has cardinality_ratio ${prof.cardinalityRatio.toFixed(4)} (< ${LOW_SIGNAL_CARD}). Almost every row shares the same value, so the field barely separates matches from non-matches.`
        : `Nearly all rows share the same '${col}' value, so it does little to tell records apart. It's mostly along for the ride.`;
    out.push(
      finding(
        "low_signal_key",
        "low",
        { column: col, cardinality_ratio: round(prof.cardinalityRatio, 4) },
        { action: "exclude_column", column: col },
        title,
        detail,
        `Drop '${col}' from matching; it barely varies.`,
      ),
    );
  }
  return out;
}

function detectOversizedBlock(
  signals: PostflightSignals | undefined,
  phrasing: Phrasing,
): CritiqueFinding[] {
  if (signals === undefined) return [];
  const pct = signals.blockSizePercentiles;
  if (pct === undefined) return [];
  const p99 = pct.p99;
  if (typeof p99 !== "number") return [];
  if (p99 <= BLOCK_P99_WARN) return [];
  const severity = p99 >= BLOCK_P99_HIGH ? "high" : "medium";
  const evidence: Record<string, unknown> = { p99: Math.trunc(p99) };
  if (typeof pct.max === "number") evidence["max"] = Math.trunc(pct.max);
  const action = severity === "high" ? "compound_blocking" : "tighten_blocking";
  const title =
    phrasing === "technical"
      ? `A blocking key produces oversized blocks (P99=${Math.trunc(p99)})`
      : "A common value is lumping too many records together";
  const detail =
    phrasing === "technical"
      ? `Block-size P99 is ${Math.trunc(p99)} (ceiling ${BLOCK_P99_WARN}); a shared value is grouping too many rows into one block, which both slows scoring and risks over-merging within the block.`
      : `One blocking value groups thousands of records at once (the biggest group is around ${Math.trunc(p99)} rows). That makes the run slow and risks merging records that only share that one value.`;
  return [
    finding(
      "shared_value_block",
      severity,
      evidence,
      { action },
      title,
      detail,
      "Block on a more specific combination of fields so each group stays small.",
    ),
  ];
}

function detectOverMerge(
  signals: PostflightSignals | undefined,
  clusters: ReadonlyMap<number, ClusterInfo> | undefined,
  phrasing: Phrasing,
): CritiqueFinding[] {
  let maxSize = 0;
  let nOversized = 0;
  if (signals !== undefined) {
    const oversized = signals.oversizedClusters ?? [];
    nOversized = oversized.length;
    for (const c of oversized) {
      if (typeof c.size === "number" && Math.trunc(c.size) > maxSize) maxSize = Math.trunc(c.size);
    }
    const pmax = signals.preliminaryClusterSizes?.max;
    if (typeof pmax === "number") maxSize = Math.max(maxSize, Math.trunc(pmax));
  }
  if (maxSize === 0 && clusters !== undefined) {
    for (const info of clusters.values()) {
      const size = info.members?.length ?? info.size ?? 0;
      if (size > maxSize) maxSize = size;
    }
  }
  if (nOversized === 0 && maxSize <= 100) return [];
  const title =
    phrasing === "technical"
      ? `Cluster sizes show over-merging (max=${maxSize})`
      : "Too many records got merged into one giant group";
  const detail =
    phrasing === "technical"
      ? `The largest cluster has ${maxSize} records (${nOversized} oversized cluster(s) flagged). A few mega-clusters usually mean the threshold is too loose or a weak signal is chaining records together.`
      : `The biggest merged group has about ${maxSize} records, which is far larger than a real duplicate set. The rules are probably merging records that aren't actually the same.`;
  return [
    finding(
      "over_merge",
      "high",
      { max_cluster_size: maxSize, oversized_clusters: nOversized },
      { action: "raise_threshold" },
      title,
      detail,
      "Raise the match threshold (or tighten the rules) so only strong evidence merges records.",
    ),
  ];
}

function mergeSignal(
  signals: PostflightSignals | undefined,
  clusters: ReadonlyMap<number, ClusterInfo> | undefined,
  nRows: number,
): { mergeRate: number | null; p95: number; mega: boolean } {
  let mega = false;
  let p95 = 0;
  if (signals !== undefined) {
    if ((signals.oversizedClusters ?? []).length > 0) mega = true;
    const pv = signals.preliminaryClusterSizes?.p95;
    if (typeof pv === "number") p95 = Math.trunc(pv);
    const pmax = signals.preliminaryClusterSizes?.max;
    if (typeof pmax === "number" && pmax > 100) mega = true;
  }
  let mergeRate: number | null = null;
  if (clusters !== undefined && nRows > 0) {
    let merged = 0;
    for (const info of clusters.values()) {
      const size = info.members?.length ?? info.size ?? 0;
      if (size > 1) {
        merged += size;
        if (size > 100) mega = true;
      }
    }
    mergeRate = merged / nRows;
  }
  return { mergeRate, p95, mega };
}

function detectDistributedOverMerge(
  config: GoldenMatchConfig,
  signals: PostflightSignals | undefined,
  clusters: ReadonlyMap<number, ClusterInfo> | undefined,
  profiles: Map<string, ColumnProfile>,
  nRows: number,
  phrasing: Phrasing,
): CritiqueFinding[] {
  if (config.blocking === undefined) return [];
  const blockFields = collectBlockingFields(config);
  if (blockFields.length === 0) return [];

  const lowCard: { col: string; distinct: number }[] = [];
  for (const col of blockFields) {
    const prof = profiles.get(col);
    if (prof === undefined) continue;
    if (
      prof.cardinalityRatio >= DIST_STRONG_BLOCK_CARD ||
      DIST_STRONG_BLOCK_TYPES.has(prof.inferredType)
    ) {
      return []; // a strong anchor key -- distributed over-merge unlikely.
    }
    if (prof.cardinalityRatio < DIST_LOW_BLOCK_CARD) {
      const distinct = Math.max(1, Math.round(prof.cardinalityRatio * nRows));
      lowCard.push({ col, distinct });
    }
  }
  if (lowCard.length === 0) return [];

  const { mergeRate, p95, mega } = mergeSignal(signals, clusters, nRows);
  if (mega) return [];
  const hasMerge =
    (mergeRate !== null && mergeRate >= DIST_MERGE_RATE) ||
    (mergeRate === null && p95 >= DIST_CLUSTER_P95);
  if (!hasMerge) return [];

  // Report the weakest (fewest-distinct) blocking key as the culprit.
  let culprit = lowCard[0]!;
  for (const lc of lowCard) if (lc.distinct < culprit.distinct) culprit = lc;
  const { col, distinct } = culprit;
  const evidence: Record<string, unknown> = { blocking_key: col, distinct_values: distinct };
  if (mergeRate !== null) evidence["merge_rate"] = round(mergeRate, 3);
  else evidence["cluster_p95"] = p95;
  const pct = mergeRate !== null ? `${Math.round(mergeRate * 100)}%` : null;
  const title =
    phrasing === "technical"
      ? `Blocking key '${col}' has only ${distinct} distinct values (distributed over-merge risk)`
      : "Records are grouped using a column with very few values";
  const detail =
    phrasing === "technical"
      ? `Blocking relies on low-cardinality column '${col}' (~${distinct} distinct values) with no near-unique anchor key, so unrelated records that merely share '${col}' are compared and can merge across many medium clusters${pct !== null ? `; the run merged ${pct} of records` : ""}. This is precision collapse spread thin enough that no single cluster looks oversized.`
      : `The matching groups records by '${col}', which only has about ${distinct} different values, and there's no more specific key to separate them. So unrelated records that happen to share '${col}' get compared and merged${pct !== null ? ` (about ${pct} of records ended up merged)` : ""}. The over-merging is spread across many groups, so no single group looks too big -- but precision still suffers.`;
  return [
    finding(
      "distributed_over_merge",
      "high",
      evidence,
      { action: "compound_blocking" },
      title,
      detail,
      `Add a more specific blocking key -- combine '${col}' with another field (or add a stronger key) so records that only share '${col}' aren't compared.`,
    ),
  ];
}

// -- Orchestration ------------------------------------------------------------

function safe(fn: () => CritiqueFinding[]): CritiqueFinding[] {
  try {
    return fn();
  } catch {
    return [];
  }
}

function templateSummary(findings: readonly CritiqueFinding[]): string {
  if (findings.length === 0) {
    return (
      "The auto-built matching rules look solid for this data; no risky signals " +
      "stood out. Zero-config nailed this one. Review the matches and adjust only " +
      "if something looks off."
    );
  }
  const n = findings.length;
  const highs = findings.filter((f) => f.severity === "high").length;
  const titles = findings
    .slice(0, 3)
    .map((f) => f.title_plain)
    .join("; ");
  const lead = `The auto-built rules ran, but ${n} thing(s) look risky${highs ? ` (${highs} serious)` : ""}. `;
  const tail = `Top issues: ${titles}. Each finding below says what to change in plain terms.`;
  return lead + tail;
}

export interface DiagnoseOptions {
  readonly maxFindings?: number;
  readonly phrasing?: Phrasing;
}

/**
 * Explain where an auto-built matching config is risky, in plain English.
 * Never throws on a valid `(rows, config, result)` -- every detector runs inside
 * `safe` and missing signals are skipped, not treated as errors.
 */
export function diagnoseConfig(
  rows: readonly Row[],
  config: GoldenMatchConfig,
  result: CritiqueRunLike,
  options: DiagnoseOptions = {},
): CritiqueResult {
  const phrasing: Phrasing = options.phrasing === "technical" ? "technical" : "plain";
  const maxFindings = options.maxFindings ?? 6;

  let referenced: Set<string>;
  try {
    referenced = collectReferenced(config);
  } catch {
    referenced = new Set();
  }
  // TS GoldenMatchConfig has no exclude_columns field; tolerate a Python-shaped
  // config carrying one (snake_case) via a defensive read.
  const excludedRaw = (config as unknown as { exclude_columns?: readonly string[] })
    .exclude_columns;
  const excluded = new Set<string>(Array.isArray(excludedRaw) ? excludedRaw : []);
  const matchkeyCols = new Set([...matchkeyColumns(config)].filter((c) => !excluded.has(c)));

  let profiles = new Map<string, ColumnProfile>();
  try {
    const ds = profileRows(rows);
    profiles = new Map(ds.columns.map((p) => [p.name, p]));
  } catch {
    profiles = new Map();
  }
  const signals = result.postflightReport?.signals;
  const clusters = result.clusters;
  const nRows = rows.length;

  let findings: CritiqueFinding[] = [];
  findings.push(...safe(() => detectSourceAdmitted(referenced, excluded, phrasing)));
  findings.push(...safe(() => detectIdAdmitted(referenced, excluded, profiles, phrasing)));
  findings.push(...safe(() => detectOversizedBlock(signals, phrasing)));
  findings.push(...safe(() => detectOverMerge(signals, clusters, phrasing)));
  findings.push(
    ...safe(() =>
      detectDistributedOverMerge(config, signals, clusters, profiles, nRows, phrasing),
    ),
  );
  findings.push(...safe(() => detectNullSink(matchkeyCols, profiles, phrasing)));
  findings.push(...safe(() => detectLowSignalKey(matchkeyCols, profiles, phrasing)));

  // null_sink and low_signal_key can both fire on the same (mostly-empty) column;
  // emptiness is the root cause, so drop the redundant low_signal_key.
  const nullSinkCols = new Set(
    findings
      .filter((f) => f.id === "null_sink" && typeof f.evidence["column"] === "string")
      .map((f) => f.evidence["column"] as string),
  );
  if (nullSinkCols.size > 0) {
    findings = findings.filter(
      (f) =>
        !(f.id === "low_signal_key" && nullSinkCols.has(f.evidence["column"] as string)),
    );
  }

  // Stable high->low severity sort (preserve detector order within a tier).
  const indexed = findings.map((f, i) => ({ f, i }));
  indexed.sort((a, b) => {
    const ra = SEVERITY_RANK[a.f.severity] ?? 99;
    const rb = SEVERITY_RANK[b.f.severity] ?? 99;
    return ra !== rb ? ra - rb : a.i - b.i;
  });
  findings = indexed.map((x) => x.f);
  if (maxFindings >= 0) findings = findings.slice(0, maxFindings);

  return { findings, summary_plain: templateSummary(findings) };
}
