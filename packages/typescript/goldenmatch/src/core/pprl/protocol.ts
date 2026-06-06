/**
 * pprl/protocol.ts — Privacy-preserving record linkage.
 * Edge-safe: no `node:` imports.
 *
 * Faithful port of goldenmatch/pprl/protocol.py (Wave 4 — replaces the prior
 * "API-parity stub" that scored string-dice over hex chars). Both datasets are
 * encoded as CLK bloom filters via the parameterized `bloom_filter` transform
 * (pure-TS SHA-256/HMAC, byte-parity with Python), then scored with BITWISE
 * dice/jaccard over the decoded filters.
 *
 * Protocol semantics (parity with Python):
 *  - trusted_third_party: the coordinator sees both parties' encoded filters
 *    and returns real similarity scores.
 *  - smc: simulated secret-sharing protocol structure — the SAME similarity
 *    is computed, but only match/no-match bits are revealed, so every match's
 *    reported score is the threshold itself. (Python's link_smc is likewise a
 *    simulation; a real garbled-circuit backend via mp-spdz is a Python-side
 *    future enhancement.)
 *
 * Matches are also clustered (Python composite-id scheme: a -> id*1e6,
 * b -> id*1e6 + 500000) and surfaced as cross-party member groups.
 */

import type { Row } from "../types.js";
import { applyTransform } from "../transforms.js";
import { buildClusters } from "../cluster.js";
import { profileRows, type ColumnProfile } from "../profiler.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface PPRLConfig {
  readonly fields: readonly string[];
  readonly securityLevel: "standard" | "high" | "paranoid";
  readonly protocol: "trusted_third_party" | "smc";
  readonly threshold: number;
  /** Shared HMAC key for the CLK encoding (both parties must use the same). */
  readonly salt?: string;
  /** Parity with Python PPRLConfig (defaults: 1024 / 30 / 2 / "dice"). */
  readonly bloomFilterSize?: number;
  readonly hashFunctions?: number;
  readonly ngramSize?: number;
  readonly scorer?: "dice" | "jaccard";
}

export interface PPRLMatch {
  readonly idA: number;
  readonly idB: number;
  readonly score: number;
}

export interface PPRLClusterMember {
  readonly party: string;
  readonly id: number;
}

export interface PPRLResult {
  readonly matches: readonly PPRLMatch[];
  /** Cross-party clusters (size >= 2), Python LinkageResult.clusters parity. */
  readonly clusters: ReadonlyArray<readonly PPRLClusterMember[]>;
  readonly matchCount: number;
  readonly totalComparisons: number;
  readonly stats: Readonly<Record<string, unknown>>;
}

// ---------------------------------------------------------------------------
// CLK encoding (Python compute_bloom_filters parity)
// ---------------------------------------------------------------------------

/**
 * Python: `" ".join(str(row.get(f, "") or "") for f in fields)` — null /
 * missing fields contribute an empty string (separators preserved); the
 * bloom transform itself lowercases + strips.
 */
function rowText(row: Row, fields: readonly string[]): string {
  return fields
    .map((f) => {
      const v = (row as Record<string, unknown>)[f];
      return v === null || v === undefined || v === "" ? "" : String(v);
    })
    .join(" ");
}

/**
 * Compute one CLK per row via the parameterized transform
 * `bloom_filter:<ngram>:<k>:<size>[:hmac_key]` (the form Python's protocol
 * path uses — NOT the security-level presets). Keyed by `__row_id__` when
 * present, else the row index. Every row gets a filter (an all-empty row
 * still encodes — Python keeps it).
 */
export function computeBloomFilters(
  rows: readonly Row[],
  fields: readonly string[],
  config: PPRLConfig,
  hmacKey?: string,
): Map<number, string> {
  const ngram = config.ngramSize ?? 2;
  const k = config.hashFunctions ?? 30;
  const size = config.bloomFilterSize ?? 1024;
  let transform = `bloom_filter:${ngram}:${k}:${size}`;
  if (hmacKey !== undefined && hmacKey.length > 0) {
    transform = `${transform}:${hmacKey}`;
  }

  const filters = new Map<number, string>();
  for (let i = 0; i < rows.length; i++) {
    const row = rows[i]!;
    const rawRid = (row as Record<string, unknown>)["__row_id__"];
    const rid = rawRid === null || rawRid === undefined ? i : Number(rawRid);
    const clk = applyTransform(rowText(row, fields), transform);
    if (clk !== null && clk.length > 0) filters.set(rid, clk);
  }
  return filters;
}

// ---------------------------------------------------------------------------
// Bitwise similarity
// ---------------------------------------------------------------------------

const POPCOUNT = new Uint8Array(256);
for (let i = 0; i < 256; i++) {
  POPCOUNT[i] = (POPCOUNT[i >> 1] ?? 0) + (i & 1);
}

function hexToBytes(hex: string): Uint8Array {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  }
  return out;
}

function popcount(bytes: Uint8Array): number {
  let n = 0;
  for (let i = 0; i < bytes.length; i++) n += POPCOUNT[bytes[i]!]!;
  return n;
}

function intersectionCount(a: Uint8Array, b: Uint8Array): number {
  const len = Math.min(a.length, b.length);
  let n = 0;
  for (let i = 0; i < len; i++) n += POPCOUNT[a[i]! & b[i]!]!;
  return n;
}

function pairScore(
  inter: number,
  popA: number,
  popB: number,
  scorer: "dice" | "jaccard",
): number {
  if (scorer === "dice") {
    const denom = popA + popB;
    return denom > 0 ? (2.0 * inter) / denom : 0.0;
  }
  const union = popA + popB - inter;
  return union > 0 ? inter / union : 0.0;
}

// ---------------------------------------------------------------------------
// Linkage core (Python link_trusted_third_party / link_smc parity)
// ---------------------------------------------------------------------------

interface PartyFilters {
  readonly partyId: string;
  readonly filters: ReadonlyMap<number, string>;
}

function linkParties(
  partyA: PartyFilters,
  partyB: PartyFilters,
  config: PPRLConfig,
  mode: "trusted_third_party" | "smc",
): PPRLResult {
  const scorer = config.scorer ?? "dice";
  const idsA = [...partyA.filters.keys()].sort((x, y) => x - y);
  const idsB = [...partyB.filters.keys()].sort((x, y) => x - y);
  const totalComparisons = idsA.length * idsB.length;

  const bytesA = idsA.map((rid) => hexToBytes(partyA.filters.get(rid)!));
  const bytesB = idsB.map((rid) => hexToBytes(partyB.filters.get(rid)!));
  const popA = bytesA.map(popcount);
  const popB = bytesB.map(popcount);

  const matches: PPRLMatch[] = [];
  const pairs: Array<readonly [number, number, number]> = [];
  for (let i = 0; i < idsA.length; i++) {
    for (let j = 0; j < idsB.length; j++) {
      const score = pairScore(
        intersectionCount(bytesA[i]!, bytesB[j]!),
        popA[i]!,
        popB[j]!,
        scorer,
      );
      if (score >= config.threshold) {
        // SMC reveals only the match bit — the reported score is the
        // threshold, never the true similarity.
        const reported = mode === "smc" ? config.threshold : score;
        matches.push({ idA: idsA[i]!, idB: idsB[j]!, score: reported });
        pairs.push([
          idsA[i]! * 1_000_000,
          idsB[j]! * 1_000_000 + 500_000,
          reported,
        ]);
      }
    }
  }

  // Cluster via the composite-id scheme; keep cross-party groups (size >= 2).
  const allComposite = [
    ...idsA.map((rid) => rid * 1_000_000),
    ...idsB.map((rid) => rid * 1_000_000 + 500_000),
  ];
  const clusters: PPRLClusterMember[][] = [];
  if (pairs.length > 0) {
    for (const info of buildClusters(pairs, allComposite).values()) {
      if (info.size < 2) continue;
      clusters.push(
        info.members.map((composite) =>
          composite % 1_000_000 >= 500_000
            ? { party: partyB.partyId, id: Math.floor((composite - 500_000) / 1_000_000) }
            : { party: partyA.partyId, id: Math.floor(composite / 1_000_000) },
        ),
      );
    }
  }

  return {
    matches,
    clusters,
    matchCount: matches.length,
    totalComparisons,
    stats: {
      protocol: mode,
      securityLevel: config.securityLevel,
      comparedPairs: totalComparisons,
      matchCount: matches.length,
      threshold: config.threshold,
      fields: config.fields,
      scorer,
    },
  };
}

// ---------------------------------------------------------------------------
// Public entry points
// ---------------------------------------------------------------------------

/**
 * Run the full PPRL pipeline: encode both row sets as CLKs (the optional
 * `salt` is the shared HMAC key) and link via the configured protocol.
 */
export function runPPRL(
  rowsA: readonly Row[],
  rowsB: readonly Row[],
  config: PPRLConfig,
  partyAId = "party_a",
  partyBId = "party_b",
): PPRLResult {
  const hmacKey = config.salt && config.salt.length > 0 ? config.salt : undefined;
  const partyA: PartyFilters = {
    partyId: partyAId,
    filters: computeBloomFilters(rowsA, config.fields, config, hmacKey),
  };
  const partyB: PartyFilters = {
    partyId: partyBId,
    filters: computeBloomFilters(rowsB, config.fields, config, hmacKey),
  };
  const mode = config.protocol === "smc" ? "smc" : "trusted_third_party";
  return linkParties(partyA, partyB, config, mode);
}

/**
 * Trusted-third-party linkage: both parties ship encoded CLKs to a trusted
 * coordinator that computes real similarity scores.
 */
export function linkTrustedThirdParty(
  rowsA: readonly Row[],
  rowsB: readonly Row[],
  config: PPRLConfig,
): PPRLResult {
  return runPPRL(rowsA, rowsB, { ...config, protocol: "trusted_third_party" });
}

/**
 * SMC linkage (simulated protocol structure, parity with Python's link_smc):
 * only match/no-match bits are revealed — every match's score is reported as
 * the threshold. TS-side safety guards (kept from the prior surface): a
 * shared `salt` (HMAC key) and a non-"standard" security level are required,
 * since SMC without keyed encoding leaks frequency structure.
 */
export function linkSMC(
  rowsA: readonly Row[],
  rowsB: readonly Row[],
  config: PPRLConfig,
): PPRLResult {
  if (!config.salt || config.salt.length === 0) {
    throw new Error("SMC protocol requires a non-empty `salt`");
  }
  if (config.securityLevel === "standard") {
    throw new Error("SMC protocol requires securityLevel of 'high' or 'paranoid'");
  }
  return runPPRL(rowsA, rowsB, { ...config, protocol: "smc" });
}

// ---------------------------------------------------------------------------
// Auto-config
// ---------------------------------------------------------------------------

const MIN_LENGTH = 3;
const MAX_LENGTH = 15;
const MAX_FIELDS = 4;
const MIN_THRESHOLD = 0.85;

/**
 * Auto-pick PPRL parameters for the given dataset pair. Penalizes
 * near-unique fields (IDs), over-long fields, and high-null fields.
 */
export function autoConfigurePPRL(
  rowsA: readonly Row[],
  rowsB: readonly Row[],
): PPRLConfig {
  const profileA = profileRows(rowsA);
  const profileB = profileRows(rowsB);

  const commonCols = new Set<string>();
  for (const c of profileA.columns) {
    if (profileB.byName[c.name]) commonCols.add(c.name);
  }

  interface Candidate {
    readonly name: string;
    readonly score: number;
  }

  const candidates: Candidate[] = [];
  for (const name of commonCols) {
    const pa = profileA.byName[name];
    const pb = profileB.byName[name];
    if (!pa || !pb) continue;

    const nullRate = Math.max(pa.nullRate, pb.nullRate);
    if (nullRate > 0.3) continue;

    const avgLen = (pa.avgLength + pb.avgLength) / 2;
    if (avgLen < MIN_LENGTH) continue;
    if (avgLen > MAX_LENGTH) continue;

    // Penalize near-unique fields (likely IDs)
    const card = Math.max(pa.cardinalityRatio, pb.cardinalityRatio);
    if (card > 0.95) continue;

    // Score: prefer moderate cardinality, low nulls, moderate length.
    const lenPenalty = Math.abs(avgLen - 8) / 8;
    const score = (1 - nullRate) * (1 - Math.abs(card - 0.5)) * (1 - lenPenalty);

    candidates.push({ name, score });
  }

  candidates.sort((a, b) => b.score - a.score);
  const fields = candidates.slice(0, MAX_FIELDS).map((c) => c.name);

  return {
    fields,
    securityLevel: "standard",
    protocol: "trusted_third_party",
    threshold: MIN_THRESHOLD,
  };
}

// Re-export profile type for consumers that want it alongside.
export type { ColumnProfile };
