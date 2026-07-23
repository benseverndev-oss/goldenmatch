/**
 * Block analyzer -- auto-suggests optimal blocking keys.
 *
 * Edge-safe port of Python `goldenmatch/core/block_analyzer.py`
 * (`analyze_blocking`). No `node:*`, no fs/db -- operates on in-memory rows.
 * The MCP handler in `src/node/mcp/server.ts` reads the current run from
 * `RUN_STORE` and calls `analyzeBlocking`.
 *
 * Diagnoses blocking on a dataset: generates ranked blocking-key candidates,
 * scores each on block-size distribution + total candidate comparisons, and
 * estimates recall via JaroWinkler pair sampling. Same heuristics + thresholds
 * as the Python reference (these feed user-facing diagnostics).
 */
import type { Row } from "./types.js";
import { applyTransforms } from "./transforms.js";
import { jaroWinkler } from "./scorer.js";

// ---------------------------------------------------------------------------
// Column type detection
// ---------------------------------------------------------------------------

export type BlockColumnType =
  | "name"
  | "zip"
  | "email"
  | "phone"
  | "state"
  | "generic";

/** Heuristic name-based type detection for a column (Python `detect_column_type`). */
export function detectColumnType(columnName: string): BlockColumnType {
  const lower = columnName.toLowerCase();
  if (/(name|fname|lname)/.test(lower)) return "name";
  if (/(zip|postal)/.test(lower)) return "zip";
  if (/(email|mail)/.test(lower)) return "email";
  if (/(phone|tel|mobile)/.test(lower)) return "phone";
  if (/(state)/.test(lower)) return "state";
  return "generic";
}

// ---------------------------------------------------------------------------
// Candidate generation
// ---------------------------------------------------------------------------

/**
 * A blocking-key candidate. For a single-column candidate `transforms` is a
 * flat transform chain; for a compound (two-column) candidate it is a list of
 * per-field transform chains (Python's mixed-type `transforms`).
 */
export interface BlockingCandidate {
  readonly key_fields: readonly string[];
  readonly transforms: readonly string[] | readonly (readonly string[])[];
  readonly description: string;
}

/** Single-column blocking-key candidates based on detected type. */
function singleColumnCandidates(column: string): BlockingCandidate[] {
  const colType = detectColumnType(column);
  const candidates: BlockingCandidate[] = [];

  if (colType === "name") {
    for (const length of [3, 4, 5]) {
      candidates.push({
        key_fields: [column],
        transforms: ["lowercase", `substring:0:${length}`],
        description: `${column}[:${length}]`,
      });
    }
    candidates.push({
      key_fields: [column],
      transforms: ["lowercase", "soundex"],
      description: `soundex(${column})`,
    });
  } else if (colType === "zip") {
    for (const length of [3, 5]) {
      candidates.push({
        key_fields: [column],
        transforms: [`substring:0:${length}`],
        description: `${column}[:${length}]`,
      });
    }
    candidates.push({ key_fields: [column], transforms: [], description: column });
  } else if (colType === "state") {
    candidates.push({ key_fields: [column], transforms: [], description: column });
  } else if (colType === "email") {
    candidates.push({
      key_fields: [column],
      transforms: ["lowercase", "substring:0:5"],
      description: `${column}[:5]`,
    });
  } else if (colType === "phone") {
    for (const length of [3, 6]) {
      candidates.push({
        key_fields: [column],
        transforms: [`substring:0:${length}`],
        description: `${column}[:${length}]`,
      });
    }
  } else {
    // generic
    for (const length of [3, 4, 5]) {
      candidates.push({
        key_fields: [column],
        transforms: [`substring:0:${length}`],
        description: `${column}[:${length}]`,
      });
    }
  }

  return candidates;
}

/**
 * Generate blocking-key candidates from matchkey columns: single-column
 * candidates by column-type heuristic + compound (paired) candidates.
 */
export function generateCandidates(
  matchkeyColumns: readonly string[],
): BlockingCandidate[] {
  const singleCandidates: Record<string, BlockingCandidate[]> = {};
  const allCandidates: BlockingCandidate[] = [];

  for (const col of matchkeyColumns) {
    const colCandidates = singleColumnCandidates(col);
    singleCandidates[col] = colCandidates;
    allCandidates.push(...colCandidates);
  }

  // Compound candidates: combine pairs of columns (max 2).
  if (matchkeyColumns.length >= 2) {
    for (let i = 0; i < matchkeyColumns.length; i++) {
      for (let j = i + 1; j < matchkeyColumns.length; j++) {
        const colA = matchkeyColumns[i]!;
        const colB = matchkeyColumns[j]!;
        for (const candA of singleCandidates[colA]!) {
          for (const candB of singleCandidates[colB]!) {
            allCandidates.push({
              key_fields: [colA, colB],
              transforms: [
                candA.transforms as readonly string[],
                candB.transforms as readonly string[],
              ],
              description: `${candA.description} + ${candB.description}`,
            });
          }
        }
      }
    }
  }

  return allCandidates;
}

// ---------------------------------------------------------------------------
// Scoring
// ---------------------------------------------------------------------------

function cellToString(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  return String(value);
}

/** Apply a candidate's transforms to a row and return its block key (or null). */
function candidateBlockKey(row: Row, candidate: BlockingCandidate): string | null {
  const keyFields = candidate.key_fields;

  if (keyFields.length === 1) {
    const col = keyFields[0]!;
    const tfms = candidate.transforms as readonly string[];
    const raw = cellToString(row[col]);
    if (raw === null) return null;
    if (tfms.length > 0) return applyTransforms(raw, tfms);
    return raw;
  }

  // Compound: transforms is a list of per-field chains, joined with "||".
  const perField = candidate.transforms as readonly (readonly string[])[];
  const parts: string[] = [];
  for (let i = 0; i < keyFields.length; i++) {
    const col = keyFields[i]!;
    const tfms = perField[i] ?? [];
    const raw = cellToString(row[col]);
    // Polars `concat_str` returns null for the whole key if ANY part is null.
    if (raw === null) return null;
    const val = tfms.length > 0 ? applyTransforms(raw, tfms) : raw;
    if (val === null) return null;
    parts.push(val);
  }
  return parts.join("||");
}

export interface CandidateMetrics {
  group_count: number;
  max_group_size: number;
  mean_group_size: number;
  std_group_size: number;
  total_comparisons: number;
  score: number;
  estimated_recall?: number;
}

const ZERO_METRICS: CandidateMetrics = {
  group_count: 0,
  max_group_size: 0,
  mean_group_size: 0.0,
  std_group_size: 0.0,
  total_comparisons: 0,
  score: 0.0,
};

/**
 * Score a blocking-key candidate on the given rows. Mirrors Python
 * `score_candidate`: group-size distribution + total candidate comparisons +
 * a composite score rewarding many small, evenly-sized blocks.
 */
export function scoreCandidate(
  rows: readonly Row[],
  candidate: BlockingCandidate,
  targetBlockSize = 5000,
): CandidateMetrics {
  const cols = new Set<string>();
  for (const r of rows) for (const k of Object.keys(r)) cols.add(k);
  for (const f of candidate.key_fields) {
    if (!cols.has(f)) return { ...ZERO_METRICS };
  }

  // Block-size counts over non-null block keys.
  const sizes = new Map<string, number>();
  let totalRecords = 0;
  for (const row of rows) {
    const key = candidateBlockKey(row, candidate);
    if (key === null) continue;
    totalRecords += 1;
    sizes.set(key, (sizes.get(key) ?? 0) + 1);
  }

  const groupCount = sizes.size;
  if (groupCount === 0 || totalRecords === 0) return { ...ZERO_METRICS };

  const blockSizes = [...sizes.values()];
  // Loop-based max, not `Math.max(...blockSizes)`: spread overflows the call
  // stack (RangeError) on arrays larger than ~65K elements, and there is one
  // block size per group — a real crash on wide-blocking datasets.
  let maxGroupSize = 0;
  for (const s of blockSizes) if (s > maxGroupSize) maxGroupSize = s;
  const meanGroupSize = blockSizes.reduce((a, b) => a + b, 0) / groupCount;

  // Sample standard deviation (ddof=1), matching polars `.std()`; 0 for a
  // single group (Python returns 0.0 when group_count <= 1).
  let stdGroupSize = 0.0;
  if (groupCount > 1) {
    const variance =
      blockSizes.reduce((acc, s) => acc + (s - meanGroupSize) ** 2, 0) /
      (groupCount - 1);
    stdGroupSize = Math.sqrt(variance);
  }

  // total_comparisons = sum(n*(n-1)/2)
  let totalComparisons = 0;
  for (const s of blockSizes) totalComparisons += (s * (s - 1)) / 2;
  totalComparisons = Math.trunc(totalComparisons);

  let score = 0.0;
  if (meanGroupSize !== 0) {
    score =
      (groupCount / totalRecords) *
      (1 / (1 + maxGroupSize / targetBlockSize)) *
      (1 / (1 + stdGroupSize / meanGroupSize));
  }

  return {
    group_count: groupCount,
    max_group_size: Math.trunc(maxGroupSize),
    mean_group_size: meanGroupSize,
    std_group_size: stdGroupSize,
    total_comparisons: totalComparisons,
    score,
  };
}

// ---------------------------------------------------------------------------
// Coverage check
// ---------------------------------------------------------------------------

/** True when every key_field of the candidate is a matchkey column. */
export function checkCoverage(
  candidate: BlockingCandidate,
  matchkeyColumns: readonly string[],
): boolean {
  const set = new Set(matchkeyColumns);
  return candidate.key_fields.every((f) => set.has(f));
}

// ---------------------------------------------------------------------------
// Deterministic seeded sampling (only exercised at scale; small fixtures use
// all rows so tests are RNG-independent).
// ---------------------------------------------------------------------------

function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function sampleRows(rows: readonly Row[], n: number, seed = 42): readonly Row[] {
  if (rows.length <= n) return rows;
  const rng = mulberry32(seed);
  const idx = rows.map((_, i) => i);
  // Fisher-Yates partial shuffle for the first n picks.
  for (let i = 0; i < n; i++) {
    const j = i + Math.floor(rng() * (idx.length - i));
    const tmp = idx[i]!;
    idx[i] = idx[j]!;
    idx[j] = tmp;
  }
  const picked = idx.slice(0, n).sort((a, b) => a - b);
  return picked.map((i) => rows[i]!);
}

// ---------------------------------------------------------------------------
// Recall estimation
// ---------------------------------------------------------------------------

/**
 * Estimate recall for a blocking candidate via pair sampling. Takes a sample,
 * finds fuzzy-similar pairs (JaroWinkler >= 0.7) on the highest-cardinality
 * matchkey column, then checks the fraction that co-locate in the same block.
 * Mirrors Python `estimate_recall`.
 */
export function estimateBlockingRecall(
  rows: readonly Row[],
  candidate: BlockingCandidate,
  matchkeyColumns: readonly string[],
  sampleSize = 1000,
): number {
  const n = rows.length;
  if (n < 2) return 0.0;

  const actualSample = Math.min(sampleSize, n);
  const sample = sampleRows(rows, actualSample);

  const sampleCols = new Set<string>();
  for (const r of sample) for (const k of Object.keys(r)) sampleCols.add(k);
  const validCols = matchkeyColumns.filter((c) => sampleCols.has(c));
  if (validCols.length === 0) return 0.0;

  // Pick the highest-cardinality matchkey column.
  let bestCol = validCols[0]!;
  let bestCard = -1;
  for (const c of validCols) {
    const uniq = new Set<string>();
    for (const r of sample) uniq.add(cellToString(r[c]) ?? "");
    if (uniq.size > bestCard) {
      bestCard = uniq.size;
      bestCol = c;
    }
  }

  const values = sample.map((r) => {
    const v = cellToString(r[bestCol]) ?? "";
    return v.toLowerCase().trim();
  });

  const threshold = 0.7;
  const pairsAbove: Array<[number, number]> = [];
  for (let i = 0; i < actualSample; i++) {
    for (let j = i + 1; j < actualSample; j++) {
      if (jaroWinkler(values[i]!, values[j]!) >= threshold) {
        pairsAbove.push([i, j]);
      }
    }
  }

  if (pairsAbove.length === 0) return 1.0; // No pairs to miss.

  const blockKeys = sample.map((r) => candidateBlockKey(r, candidate));
  let pairsInSameBlock = 0;
  for (const [i, j] of pairsAbove) {
    if (blockKeys[i] !== null && blockKeys[i] === blockKeys[j]) {
      pairsInSameBlock += 1;
    }
  }

  return pairsInSameBlock / pairsAbove.length;
}

// ---------------------------------------------------------------------------
// Main analyzer
// ---------------------------------------------------------------------------

export interface BlockingSuggestion {
  keys: BlockingCandidate[];
  group_count: number;
  max_group_size: number;
  mean_group_size: number;
  total_comparisons: number;
  estimated_recall: number;
  score: number;
  description: string;
}

const SCORE_SAMPLE_THRESHOLD = 100_000;
const SCORE_SAMPLE_SIZE = 100_000;

/**
 * Analyze data and return ranked blocking-strategy suggestions.
 *
 * 1. Generate candidates from matchkey columns.
 * 2. Score each candidate (block-size distribution).
 * 3. Estimate recall for the top 10 by score.
 * 4. Demote non-covering candidates (recall_bonus 0.5).
 * 5. Sort by final score descending.
 */
export function analyzeBlocking(
  rows: readonly Row[],
  matchkeyColumns: readonly string[],
  sampleSize = 1000,
  targetBlockSize = 5000,
): BlockingSuggestion[] {
  const candidates = generateCandidates(matchkeyColumns);

  // At scale, per-candidate scoring is O(candidates * rows); sample for the
  // shape-only block-size distribution (mirrors Python's UDF-cost guard).
  const scoreRows =
    rows.length > SCORE_SAMPLE_THRESHOLD
      ? sampleRows(rows, SCORE_SAMPLE_SIZE)
      : rows;

  const scored: Array<[BlockingCandidate, CandidateMetrics]> = [];
  for (const cand of candidates) {
    const metrics = scoreCandidate(scoreRows, cand, targetBlockSize);
    if (metrics.group_count === 0) continue;
    scored.push([cand, metrics]);
  }

  if (scored.length === 0) return [];

  // Sort by score descending to pick the top candidates for recall estimation.
  scored.sort((a, b) => b[1].score - a[1].score);

  const topN = Math.min(10, scored.length);
  for (let i = 0; i < topN; i++) {
    const [cand, metrics] = scored[i]!;
    try {
      metrics.estimated_recall = estimateBlockingRecall(
        rows,
        cand,
        matchkeyColumns,
        sampleSize,
      );
    } catch {
      metrics.estimated_recall = 0.0;
    }
  }
  for (let i = topN; i < scored.length; i++) {
    scored[i]![1].estimated_recall = 0.0;
  }

  const suggestions: BlockingSuggestion[] = [];
  for (const [cand, metrics] of scored) {
    const covers = checkCoverage(cand, matchkeyColumns);
    const recallBonus = covers ? 1.0 : 0.5;
    const adjustedScore = metrics.score * recallBonus;
    suggestions.push({
      keys: [cand],
      group_count: metrics.group_count,
      max_group_size: metrics.max_group_size,
      mean_group_size: metrics.mean_group_size,
      total_comparisons: metrics.total_comparisons,
      estimated_recall: metrics.estimated_recall ?? 0.0,
      score: adjustedScore,
      description: cand.description,
    });
  }

  suggestions.sort((a, b) => b.score - a.score);
  return suggestions;
}
