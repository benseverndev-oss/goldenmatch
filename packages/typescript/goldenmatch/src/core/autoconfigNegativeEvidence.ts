/**
 * autoconfigNegativeEvidence.ts — v1.11 + v1.12 negative evidence port.
 *
 * Mirrors ``packages/python/goldenmatch/goldenmatch/core/autoconfig_negative_evidence.py``
 * plus the two helpers from ``core/scorer.py``:
 *
 * - ``applyNegativeEvidence`` — compute disagreement penalty for a pair on a
 *   matchkey with ``negativeEvidence`` set (v1.11).
 * - ``applyNegativeEvidenceToExactPairs`` — Path Y post-filter for exact
 *   matches (v1.12). Subtracts penalties from the binary 1.0 emit; pairs
 *   whose adjusted score falls below the matchkey threshold are dropped.
 * - ``promoteNegativeEvidence`` — eager rule that walks BOTH weighted and
 *   exact matchkeys (v1.12 change), populating NE fields from identity-prior
 *   columns. The v1.11 `_is_exact_matchkey_field` gate is selectively
 *   applied (only on the weighted branch — see Python note).
 *
 * Edge-safe: no `node:` imports.
 */

import type {
  GoldenMatchConfig,
  MatchkeyConfig,
  ExactMatchkey,
  WeightedMatchkey,
  ProbabilisticMatchkey,
  NegativeEvidenceField,
  Row,
} from "./types.js";
import { getMatchkeys } from "./types.js";
import type { ColumnPrior } from "./complexityProfile.js";
import { applyTransforms } from "./transforms.js";
import { scoreField, asString } from "./scorer.js";

// ---------------------------------------------------------------------------
// Constants — match Python autoconfig_negative_evidence.py
// ---------------------------------------------------------------------------

const IDENTITY_SCORE_THRESHOLD = 0.75;
const CARDINALITY_THRESHOLD = 0.5;
const DEFAULT_NE_THRESHOLD = 0.4;
const DEFAULT_NE_PENALTY = 0.3;

// ---------------------------------------------------------------------------
// Scorer picker (mirrors Python _pick_scorer_for_column — name-keyed, since
// Polars dtype names don't match the ColumnType vocabulary)
// ---------------------------------------------------------------------------

/**
 * Pick (transforms, scorer) tuple for negative-evidence on a column.
 * Name-keyed: substring-matches col name. The col_type branch is reserved
 * for future callers that pass a typed string ("email", "phone", etc.).
 * Default: ([], "ensemble").
 */
export function pickScorerForColumn(
  colName: string,
  colType = "",
): { transforms: string[]; scorer: string } {
  const nameLower = colName.toLowerCase();
  const typeLower = (colType ?? "").toLowerCase();

  if (nameLower.includes("phone") || typeLower === "phone") {
    return { transforms: ["digits_only"], scorer: "exact" };
  }
  if (nameLower.includes("email") || typeLower === "email") {
    return { transforms: [], scorer: "token_sort" };
  }
  if (
    nameLower.includes("address") ||
    nameLower.includes("addr") ||
    typeLower === "address"
  ) {
    return { transforms: [], scorer: "token_sort" };
  }
  if (typeLower === "date" || typeLower === "datetime") {
    return { transforms: [], scorer: "exact" };
  }
  return { transforms: [], scorer: "ensemble" };
}

// ---------------------------------------------------------------------------
// applyNegativeEvidence (per-pair penalty; weighted matchkey scoring loop)
// ---------------------------------------------------------------------------

/**
 * Compute the total negative-evidence penalty for a pair on a matchkey.
 * Returns the sum of penalties for NE fields whose similarity score falls
 * below the field's threshold. Returns 0 when matchkey has no NE list.
 *
 * The caller subtracts the result from the positive score:
 *     finalScore = max(0, positiveScore - penalty)
 */
export function applyNegativeEvidence(
  matchkey: MatchkeyConfig,
  rowA: Row,
  rowB: Row,
): number {
  const ne = getNegativeEvidence(matchkey);
  if (!ne || ne.length === 0) return 0;

  let total = 0;
  for (const f of ne) {
    const rawA = asString((rowA as Record<string, unknown>)[f.field]);
    const rawB = asString((rowB as Record<string, unknown>)[f.field]);
    const valA = applyTransforms(rawA, f.transforms);
    const valB = applyTransforms(rawB, f.transforms);
    let sim: number | null;
    try {
      sim = scoreField(valA, valB, f.scorer);
    } catch {
      // Unknown scorer → skip defensively (Python parity)
      continue;
    }
    if (sim === null) continue;
    if (sim < f.threshold) total += f.penalty;
  }
  return total;
}

// ---------------------------------------------------------------------------
// applyNegativeEvidenceToExactPairs (v1.12 Path Y post-filter)
// ---------------------------------------------------------------------------

export interface ExactPair {
  readonly idA: number;
  readonly idB: number;
  readonly score: number;
}

/**
 * v1.12 Path Y: filter exact-matchkey pairs by NE disagreement penalty.
 *
 * Input ``pairs`` is the output of ``findExactMatches`` (score 1.0 per pair,
 * pair shares the matchkey value). For each pair, subtract NE penalties.
 * Emit only pairs whose adjusted score meets the matchkey threshold
 * (default 0.5 when NE is set but ``threshold`` is undefined).
 *
 * Returns pairs unchanged when matchkey has no NE (preserves binary
 * 1.0/0.0 behavior).
 */
export function applyNegativeEvidenceToExactPairs<P extends ExactPair>(
  pairs: readonly P[],
  matchkey: MatchkeyConfig,
  allRows: readonly Row[],
): P[] {
  const ne = getNegativeEvidence(matchkey);
  if (!ne || ne.length === 0) return [...pairs];
  const threshold = exactThresholdForNe(matchkey);

  // Build row_id -> row lookup
  const lookup = new Map<number, Row>();
  for (const r of allRows) {
    const rid = (r as Record<string, unknown>)["__row_id__"] as number | undefined;
    if (rid !== undefined) lookup.set(rid, r);
  }

  const out: P[] = [];
  for (const p of pairs) {
    const rowA = lookup.get(p.idA);
    const rowB = lookup.get(p.idB);
    if (rowA === undefined || rowB === undefined) continue;
    const penalty = applyNegativeEvidence(matchkey, rowA, rowB);
    const finalScore = Math.max(0, 1.0 - penalty);
    if (finalScore >= threshold) {
      out.push({ ...p, score: finalScore });
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// promoteNegativeEvidence (eager rule, controller pre-iteration pass)
// ---------------------------------------------------------------------------

/**
 * Add NE fields to weighted AND exact matchkeys (v1.12) based on column
 * priors. Probabilistic matchkeys are skipped. Returns a new config; does
 * not mutate input. No-op when rows or column priors are empty.
 *
 * Eligibility (weighted branch):
 *   identityScore >= 0.75
 *   AND col is a field in some exact matchkey (anchor safety)
 *   AND cardinalityRatio >= 0.5
 *   AND col is not in this matchkey's fields
 *   AND col is not in any blocking key
 *
 * Eligibility (exact branch — v1.12):
 *   Same gates EXCEPT the `is_exact_matchkey_field` gate is skipped.
 *   When NE is added to an exact matchkey with no threshold, threshold
 *   defaults to 0.5 to activate the score-and-threshold path.
 */
export function promoteNegativeEvidence(
  config: GoldenMatchConfig,
  rows: readonly Row[],
  columnPriors: Readonly<Record<string, ColumnPrior>>,
): GoldenMatchConfig {
  if (rows.length === 0) return config;
  if (Object.keys(columnPriors).length === 0) return config;

  const allMatchkeys = getMatchkeys(config);
  if (allMatchkeys.length === 0) return config;

  const blockingFields = collectBlockingFields(config);
  const exactFieldSet = collectExactMatchkeyFields(allMatchkeys);
  const colSet = new Set<string>();
  if (rows.length > 0) {
    for (const k of Object.keys(rows[0] as object)) colSet.add(k);
  }

  const cardinalityCache = new Map<string, number>();

  const newMatchkeys: MatchkeyConfig[] = [];
  for (const mk of allMatchkeys) {
    if (mk.type !== "weighted" && mk.type !== "exact") {
      newMatchkeys.push(mk);
      continue;
    }
    const existingNe = mk.negativeEvidence ? [...mk.negativeEvidence] : [];
    const existingNeFields = new Set(existingNe.map((n) => n.field));
    const mkFieldSet = new Set(mk.fields.map((f) => f.field));
    const additions: NegativeEvidenceField[] = [];

    for (const [col, prior] of Object.entries(columnPriors)) {
      if (existingNeFields.has(col)) continue;
      if (prior.identityScore < IDENTITY_SCORE_THRESHOLD) continue;
      // v1.12: exact-matchkey gate ONLY on weighted branch.
      if (mk.type === "weighted") {
        if (!exactFieldSet.has(col)) continue;
      }
      if (mkFieldSet.has(col)) continue;
      if (blockingFields.has(col)) continue;
      if (!colSet.has(col)) continue;

      let cardRatio = cardinalityCache.get(col);
      if (cardRatio === undefined) {
        cardRatio = computeCardinalityRatio(rows, col);
        cardinalityCache.set(col, cardRatio);
      }
      if (cardRatio < CARDINALITY_THRESHOLD) continue;

      const { transforms, scorer } = pickScorerForColumn(col, "");
      additions.push({
        field: col,
        transforms,
        scorer,
        threshold: DEFAULT_NE_THRESHOLD,
        penalty: DEFAULT_NE_PENALTY,
      });
    }

    if (additions.length === 0) {
      newMatchkeys.push(mk);
      continue;
    }

    const merged: NegativeEvidenceField[] = [...existingNe, ...additions];
    if (mk.type === "weighted") {
      const updated: WeightedMatchkey = { ...mk, negativeEvidence: merged };
      newMatchkeys.push(updated);
    } else {
      // exact: set threshold default 0.5 when none
      const updated: ExactMatchkey = {
        ...mk,
        negativeEvidence: merged,
        ...(mk.threshold === undefined ? { threshold: 0.5 } : {}),
      };
      newMatchkeys.push(updated);
    }
  }

  // Mirror Python's `config.matchkeys` placement — write back via the same
  // key the caller used. Preserve match_settings vs matchkeys spelling.
  if (config.matchkeys !== undefined) {
    return { ...config, matchkeys: newMatchkeys };
  }
  if (config.matchSettings !== undefined) {
    return { ...config, matchSettings: newMatchkeys };
  }
  return { ...config, matchkeys: newMatchkeys };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getNegativeEvidence(
  mk: MatchkeyConfig,
): readonly NegativeEvidenceField[] | undefined {
  if (mk.type === "weighted") return (mk as WeightedMatchkey).negativeEvidence;
  if (mk.type === "exact") return (mk as ExactMatchkey).negativeEvidence;
  if (mk.type === "probabilistic")
    return (mk as ProbabilisticMatchkey).negativeEvidence;
  return undefined;
}

function exactThresholdForNe(mk: MatchkeyConfig): number {
  if (mk.type === "exact") {
    const t = (mk as ExactMatchkey).threshold;
    return t ?? 0.5;
  }
  if (mk.type === "weighted") return (mk as WeightedMatchkey).threshold;
  if (mk.type === "probabilistic") {
    return (mk as ProbabilisticMatchkey).threshold ?? 0.5;
  }
  return 0.5;
}

function collectBlockingFields(config: GoldenMatchConfig): Set<string> {
  const out = new Set<string>();
  const bk = config.blocking;
  if (!bk) return out;
  for (const k of bk.keys ?? []) for (const f of k.fields ?? []) out.add(f);
  for (const k of bk.passes ?? []) for (const f of k.fields ?? []) out.add(f);
  return out;
}

function collectExactMatchkeyFields(
  matchkeys: readonly MatchkeyConfig[],
): Set<string> {
  const out = new Set<string>();
  for (const mk of matchkeys) {
    if (mk.type === "exact") {
      for (const f of mk.fields) out.add(f.field);
    }
  }
  return out;
}

function computeCardinalityRatio(rows: readonly Row[], col: string): number {
  if (rows.length === 0) return 0;
  const distinct = new Set<string>();
  for (const r of rows) {
    const v = (r as Record<string, unknown>)[col];
    if (v === null || v === undefined) continue;
    distinct.add(String(v));
  }
  return distinct.size / rows.length;
}
