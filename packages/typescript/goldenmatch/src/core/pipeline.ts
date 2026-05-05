/**
 * pipeline.ts — Core pipeline orchestrator for GoldenMatch-JS.
 * Edge-safe: no `node:` imports, pure TypeScript only.
 *
 * Ports goldenmatch/core/pipeline.py.
 * Chains: standardize -> matchkeys -> block -> score -> cluster -> golden.
 */

import type {
  Row,
  GoldenMatchConfig,
  MatchkeyConfig,
  DedupeResult,
  DedupeStats,
  PairKey,
  ScoredPair,
  MatchResult,
  GoldenRulesConfig,
  ClusterInfo,
} from "./types.js";
import { makeGoldenRulesConfig, getMatchkeys, makeBlockingConfig } from "./types.js";
import { computeMatchkeys, addRowIds, addSourceColumn } from "./matchkey.js";
import { applyStandardization } from "./standardize.js";
import { buildBlocks } from "./blocker.js";
import {
  findExactMatches,
  scoreBlocksSequential,
} from "./scorer.js";
import { buildClusters, pairKey } from "./cluster.js";
import { buildGoldenRecord } from "./golden.js";
import { postflight } from "./autoconfigVerify.js";
import type {
  PreflightReport,
  PostflightReport,
} from "./autoconfigVerify.js";
import type {
  CorrectionStats,
  MemoryStore,
} from "./memory/types.js";
import { applyCorrections } from "./memory/corrections.js";
import type { ScoredPair as ScoredPairTuple } from "./memory/corrections.js";
import { MemoryLearner } from "./memory/learner.js";

// ---------------------------------------------------------------------------
// Options
// ---------------------------------------------------------------------------

export interface DedupeOptions {
  readonly outputGolden?: boolean;
  readonly outputReport?: boolean;
  readonly acrossFilesOnly?: boolean;
  /** Optional memory store. Only consulted when `config.memory?.enabled`. */
  readonly memoryStore?: MemoryStore | null;
  /** Dataset label for memory operations. Defaults to "<DataFrame>". */
  readonly derivedDataset?: string;
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/** Build a source lookup map from rows (rowId -> source name). */
function buildSourceLookup(rows: readonly Row[]): Map<number, string> {
  const lookup = new Map<number, string>();
  for (const row of rows) {
    const id = row.__row_id__ as number;
    const src = row.__source__ as string | undefined;
    if (id !== undefined && src !== undefined) {
      lookup.set(id, src);
    }
  }
  return lookup;
}

/** Collect all row IDs from rows. */
function collectRowIds(rows: readonly Row[]): number[] {
  return rows.map((r) => r.__row_id__ as number);
}

/** Assign __cluster_id__ to rows based on cluster membership. */
function assignClusterIds(
  rows: readonly Row[],
  clusters: ReadonlyMap<number, ClusterInfo>,
): Row[] {
  // Build rowId -> clusterId lookup
  const rowToCluster = new Map<number, number>();
  for (const [cid, cinfo] of clusters) {
    for (const memberId of cinfo.members) {
      rowToCluster.set(memberId, cid);
    }
  }

  return rows.map((row) => {
    const rowId = row.__row_id__ as number;
    const cid = rowToCluster.get(rowId);
    return cid !== undefined ? { ...row, __cluster_id__: cid } : row;
  });
}

// ---------------------------------------------------------------------------
// Memory integration helpers (Phase 2.2).
// ---------------------------------------------------------------------------

/**
 * Pre-scoring hook: apply learned threshold adjustments to matchkeys.
 *
 * Mutates `matchkeys` in place when the learner has a threshold for a
 * matchkey name (or `_default`). No-op when memory disabled, store missing,
 * or no new corrections since the last learn pass.
 */
async function _applyMemoryPre(
  config: GoldenMatchConfig,
  matchkeys: MatchkeyConfig[],
  store: MemoryStore | null,
): Promise<void> {
  if (!store || !config.memory?.enabled) return;
  try {
    const learner = new MemoryLearner(store, config.memory.learning);
    if (!(await learner.hasNewCorrections())) return;
    const adjustments = await learner.learn();
    for (const adj of adjustments) {
      if (adj.threshold == null) continue;
      for (let i = 0; i < matchkeys.length; i++) {
        const mk = matchkeys[i]!;
        // Only weighted/probabilistic carry a threshold.
        if (mk.type === "exact") continue;
        // Match by matchkey name, "_default", or null/empty.
        if (
          !adj.matchkeyName ||
          adj.matchkeyName === mk.name ||
          adj.matchkeyName === "_default"
        ) {
          // Mutate in place via type-erased rebuild (interface is readonly).
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          (matchkeys[i] as any) = { ...mk, threshold: adj.threshold };
        }
      }
    }
  } catch (e) {
    // Swallow learner errors; never block scoring.
    console.warn(`Memory learner failed: ${String(e)}`);
  }
}

/**
 * Collect distinct field names referenced by matchkeys -- used for record
 * field-hash recomputation in `applyCorrections`.
 */
function collectMatchkeyFields(
  matchkeys: ReadonlyArray<MatchkeyConfig>,
): string[] {
  const seen = new Set<string>();
  for (const mk of matchkeys) {
    for (const f of mk.fields) {
      seen.add(f.field);
    }
  }
  return Array.from(seen);
}

/**
 * Post-scoring hook: apply stored corrections to scored pairs. Returns
 * `[pairs, stats]`. Stats is null when memory disabled. The translation
 * between the pipeline's object-shaped `ScoredPair` and the corrections
 * tuple `ScoredPairTuple` happens here so callers don't see two shapes.
 */
async function _applyMemoryPost(
  config: GoldenMatchConfig,
  scoredPairs: ReadonlyArray<ScoredPair>,
  df: ReadonlyArray<Row>,
  matchkeyFields: ReadonlyArray<string>,
  store: MemoryStore | null,
  derivedDataset: string,
): Promise<readonly [ScoredPair[], CorrectionStats | null]> {
  if (!store || !config.memory?.enabled) {
    return [scoredPairs.map((p) => ({ ...p })), null];
  }
  // Explicit null/string in config wins; undefined falls back to derived.
  const dataset =
    config.memory.dataset !== undefined ? config.memory.dataset : derivedDataset;
  // Translate pipeline object shape -> corrections tuple shape.
  const tuples: ScoredPairTuple[] = scoredPairs.map(
    (p) => [p.idA, p.idB, p.score] as ScoredPairTuple,
  );
  try {
    const [adjustedTuples, stats] = await applyCorrections(
      tuples,
      store,
      df,
      matchkeyFields,
      { dataset, reanchor: config.memory.reanchor ?? true },
    );
    const adjusted: ScoredPair[] = adjustedTuples.map((t) => ({
      idA: t[0],
      idB: t[1],
      score: t[2],
    }));
    return [adjusted, stats];
  } catch (e) {
    const msg = String(e);
    console.warn(`Memory applyCorrections failed: ${msg}`);
    return [
      scoredPairs.map((p) => ({ ...p })),
      {
        applied: 0,
        stale: 0,
        staleAmbiguous: 0,
        staleUnanchorable: 0,
        stalePairs: [],
        totalPairs: scoredPairs.length,
        failed: true,
        error: msg,
      },
    ];
  }
}

// ---------------------------------------------------------------------------
// Postflight integration (mirrors Python _apply_postflight).
// ---------------------------------------------------------------------------

/**
 * Lax guard: shape drift would slip through. Acceptable for v0.3 since only
 * autoConfigureRows sets this field. Tighten (e.g. brand check) if another
 * producer appears.
 */
function isPreflightReport(v: unknown): v is PreflightReport {
  return (
    typeof v === "object" &&
    v !== null &&
    "findings" in v &&
    Array.isArray((v as PreflightReport).findings)
  );
}

function applyPostflight(
  rows: readonly Row[],
  config: GoldenMatchConfig,
  pairScores: readonly ScoredPair[],
): {
  readonly pairScores: readonly ScoredPair[];
  readonly report: PostflightReport | undefined;
} {
  const pre = config._preflightReport;
  if (!isPreflightReport(pre)) {
    return { pairScores, report: undefined };
  }
  const report = postflight(rows, config, {
    pairScores: pairScores.map((p) => ({
      idA: p.idA,
      idB: p.idB,
      score: p.score,
    })),
  });

  let filtered: readonly ScoredPair[] = pairScores;
  if (config._strictAutoconfig !== true) {
    for (const adj of report.adjustments) {
      if (adj.field === "threshold") {
        const newThreshold = adj.toValue as number;
        const prev = filtered.length;
        filtered = filtered.filter((p) => p.score >= newThreshold);
        if (prev > 0 && filtered.length === 0) {
          (report.advisories as string[]).push(
            `threshold adjustment to ${newThreshold.toFixed(3)} dropped all ${prev} pairs`,
          );
        }
      }
    }
  }
  return { pairScores: filtered, report };
}

// ---------------------------------------------------------------------------
// runDedupePipeline
// ---------------------------------------------------------------------------

/**
 * Run the full deduplication pipeline.
 *
 * Steps:
 * 1. Add __row_id__ and __source__ if not present
 * 2. Apply standardization
 * 3. Compute matchkeys
 * 4. Phase 1: Exact matchkeys (hash-based grouping)
 * 5. Phase 2: Fuzzy matchkeys (block + score)
 * 6. Phase 3: Cluster (Union-Find with MST splitting)
 * 7. Phase 4: Build golden records for multi-member clusters
 * 8. Classify dupes vs unique
 * 9. Compute stats
 * 10. Return DedupeResult
 */
export async function runDedupePipeline(
  rows: readonly Row[],
  config: GoldenMatchConfig,
  options?: DedupeOptions,
): Promise<DedupeResult> {
  if (rows.length === 0) {
    return _emptyDedupeResult(config);
  }

  // Mutable copy: _applyMemoryPre may rewrite thresholds in place.
  const matchkeys: MatchkeyConfig[] = [...getMatchkeys(config)];
  const goldenRules = config.goldenRules ?? makeGoldenRulesConfig();
  const blockingConfig = config.blocking ?? makeBlockingConfig();
  const acrossFilesOnly = options?.acrossFilesOnly ?? false;
  const memoryStore = options?.memoryStore ?? null;
  const derivedDataset = options?.derivedDataset ?? "<DataFrame>";

  // ---- Memory pre-hook: learner adjustments ----
  await _applyMemoryPre(config, matchkeys, memoryStore);

  // ---- Step 1: Add __row_id__ and __source__ ----
  let processed: Row[] = rows.map((r, i) => {
    const extra: Record<string, unknown> = {};
    if (r.__row_id__ === undefined) extra.__row_id__ = i;
    if (r.__source__ === undefined) extra.__source__ = "default";
    return Object.keys(extra).length > 0 ? { ...r, ...extra } : (r as Row);
  });

  // ---- Step 2: Apply standardization ----
  if (config.standardization) {
    processed = applyStandardization(processed, config.standardization.rules);
  }

  // ---- Step 3: Compute matchkeys ----
  processed = computeMatchkeys(processed, matchkeys);

  // ---- Step 4 & 5: Score exact + fuzzy matchkeys ----
  const allPairs: ScoredPair[] = [];
  const matchedPairKeys = new Set<PairKey>();
  const sourceLookup = buildSourceLookup(processed);

  for (const mk of matchkeys) {
    if (mk.type === "exact") {
      // Phase 1: Exact matching via hash grouping
      let pairs = findExactMatches(processed, mk);

      // Cross-file filter
      if (acrossFilesOnly) {
        pairs = pairs.filter((p) => {
          const srcA = sourceLookup.get(p.idA);
          const srcB = sourceLookup.get(p.idB);
          return srcA !== srcB;
        });
      }

      for (const p of pairs) {
        const key = pairKey(p.idA, p.idB);
        if (!matchedPairKeys.has(key)) {
          matchedPairKeys.add(key);
          allPairs.push(p);
        }
      }
    } else {
      // Phase 2: Fuzzy (weighted/probabilistic) — block then score
      const blocks = buildBlocks(processed, blockingConfig);

      const pairs = scoreBlocksSequential(blocks, mk, matchedPairKeys, {
        acrossFilesOnly,
        sourceLookup,
      });

      for (const p of pairs) {
        allPairs.push(p);
      }
    }
  }

  // ---- Step 5.5: Postflight verification ----
  const { pairScores: postflightPairs, report: postflightReport } =
    applyPostflight(processed, config, allPairs);

  // ---- Step 5.7: Memory post-hook (apply stored corrections) ----
  const matchkeyFields = collectMatchkeyFields(matchkeys);
  const [memoryAdjustedPairs, memoryStats] = await _applyMemoryPost(
    config,
    postflightPairs,
    processed,
    matchkeyFields,
    memoryStore,
    derivedDataset,
  );
  const finalPairs: readonly ScoredPair[] = memoryAdjustedPairs;

  // ---- Step 6: Cluster ----
  const allIds = collectRowIds(processed);
  const pairTuples: [number, number, number][] = finalPairs.map((p) => [
    p.idA,
    p.idB,
    p.score,
  ]);

  const clusters = buildClusters(pairTuples, allIds, {
    maxClusterSize: goldenRules.maxClusterSize,
    weakClusterThreshold: goldenRules.weakClusterThreshold,
    autoSplit: goldenRules.autoSplit,
  });

  // ---- Step 7: Build golden records ----
  const rowsWithClusters = assignClusterIds(processed, clusters);
  const goldenRecords: Row[] = [];

  if (options?.outputGolden !== false) {
    for (const [cid, cinfo] of clusters) {
      if (cinfo.size < 2) continue; // Only build golden for multi-member clusters

      const clusterRows = rowsWithClusters.filter(
        (r) => (r.__cluster_id__ as number) === cid,
      );
      const golden = buildGoldenRecord(clusterRows, goldenRules);

      const goldenRow: Record<string, unknown> = {
        __cluster_id__: cid,
        __golden_confidence__: golden.goldenConfidence,
      };
      for (const [col, info] of Object.entries(golden.fields)) {
        goldenRow[col] = info.value;
      }
      goldenRecords.push(goldenRow as Row);
    }
  }

  // ---- Step 8: Classify dupes vs unique ----
  const multiMemberClusterIds = new Set<number>();
  for (const [cid, cinfo] of clusters) {
    if (cinfo.size >= 2) multiMemberClusterIds.add(cid);
  }

  const dupeRowIds = new Set<number>();
  for (const [, cinfo] of clusters) {
    if (cinfo.size >= 2) {
      for (const m of cinfo.members) {
        dupeRowIds.add(m);
      }
    }
  }

  const dupes: Row[] = [];
  const unique: Row[] = [];
  for (const row of rowsWithClusters) {
    const rowId = row.__row_id__ as number;
    if (dupeRowIds.has(rowId)) {
      dupes.push(row);
    } else {
      unique.push(row);
    }
  }

  // ---- Step 9: Compute stats ----
  const totalRecords = processed.length;
  const totalClusters = clusters.size;
  const matchedRecords = dupes.length;
  const uniqueRecords = unique.length;
  const matchRate = totalRecords > 0 ? matchedRecords / totalRecords : 0;

  const stats: DedupeStats = {
    totalRecords,
    totalClusters,
    matchRate,
    matchedRecords,
    uniqueRecords,
  };

  // ---- Step 10: Return result ----
  return {
    goldenRecords,
    clusters,
    dupes,
    unique,
    stats,
    scoredPairs: finalPairs,
    config,
    ...(postflightReport !== undefined ? { postflightReport } : {}),
    memoryStats,
  };
}

// ---------------------------------------------------------------------------
// runMatchPipeline
// ---------------------------------------------------------------------------

/**
 * Run the match pipeline: match target rows against reference rows.
 *
 * - Assigns __row_id__ with offset for reference rows
 * - Assigns __source__ ("target" / "reference")
 * - Runs same pipeline but filters to cross-source pairs only
 */
export async function runMatchPipeline(
  targetRows: readonly Row[],
  referenceRows: readonly Row[],
  config: GoldenMatchConfig,
  options?: DedupeOptions,
): Promise<MatchResult> {
  if (targetRows.length === 0 || referenceRows.length === 0) {
    return {
      matched: [],
      unmatched: [...targetRows],
      stats: {
        totalTarget: targetRows.length,
        totalReference: referenceRows.length,
        matchedCount: 0,
        unmatchedCount: targetRows.length,
        matchRate: 0,
      },
    };
  }

  // Add row IDs and source labels
  const target = addSourceColumn(addRowIds(targetRows, 0), "target");
  const reference = addSourceColumn(
    addRowIds(referenceRows, targetRows.length),
    "reference",
  );

  // Combine and run dedupe pipeline with cross-file filter
  const combined = [...target, ...reference];
  const result = await runDedupePipeline(combined, config, {
    acrossFilesOnly: true,
    outputGolden: false,
    ...(options?.memoryStore !== undefined
      ? { memoryStore: options.memoryStore }
      : {}),
    ...(options?.derivedDataset !== undefined
      ? { derivedDataset: options.derivedDataset }
      : {}),
  });

  // Track which target row IDs got matched
  const targetIds = new Set<number>(
    target.map((r) => r.__row_id__ as number),
  );
  const matchedTargetIds = new Set<number>();

  for (const pair of result.scoredPairs) {
    if (targetIds.has(pair.idA)) matchedTargetIds.add(pair.idA);
    if (targetIds.has(pair.idB)) matchedTargetIds.add(pair.idB);
  }

  // Build matched/unmatched from original target rows
  const matched: Row[] = [];
  const unmatched: Row[] = [];
  for (const row of target) {
    const rowId = row.__row_id__ as number;
    if (matchedTargetIds.has(rowId)) {
      matched.push(row);
    } else {
      unmatched.push(row);
    }
  }

  return {
    matched,
    unmatched,
    stats: {
      totalTarget: targetRows.length,
      totalReference: referenceRows.length,
      matchedCount: matched.length,
      unmatchedCount: unmatched.length,
      matchRate:
        targetRows.length > 0 ? matched.length / targetRows.length : 0,
    },
    ...(result.postflightReport !== undefined
      ? { postflightReport: result.postflightReport }
      : {}),
    memoryStats: result.memoryStats ?? null,
  };
}

// ---------------------------------------------------------------------------
// Internal: empty result
// ---------------------------------------------------------------------------

function _emptyDedupeResult(config: GoldenMatchConfig): DedupeResult {
  return {
    goldenRecords: [],
    clusters: new Map(),
    dupes: [],
    unique: [],
    stats: {
      totalRecords: 0,
      totalClusters: 0,
      matchRate: 0,
      matchedRecords: 0,
      uniqueRecords: 0,
    },
    scoredPairs: [],
    config,
  };
}
