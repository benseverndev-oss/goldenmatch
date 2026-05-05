/**
 * api.ts — High-level API functions wrapping the pipeline.
 * Edge-safe: no `node:` imports, pure TypeScript only.
 *
 * Ports goldenmatch/_api.py convenience functions.
 */

import type {
  Row,
  GoldenMatchConfig,
  DedupeResult,
  MatchResult,
  MatchkeyConfig,
  MatchkeyField,
  BlockingKeyConfig,
  MemoryConfig,
} from "./types.js";
import type { MemoryStore } from "./memory/types.js";
import {
  makeConfig,
  makeMatchkeyConfig,
  makeMatchkeyField,
  makeBlockingConfig,
} from "./types.js";
import { runDedupePipeline, runMatchPipeline } from "./pipeline.js";
import { scoreField, scorePair, asString } from "./scorer.js";
import { applyTransforms } from "./transforms.js";

// ---------------------------------------------------------------------------
// Options
// ---------------------------------------------------------------------------

export interface DedupeOptions {
  /** Full config object -- takes precedence over shorthand options. */
  readonly config?: GoldenMatchConfig;
  /** Columns for exact matching (creates one exact matchkey per column). */
  readonly exact?: readonly string[];
  /** Columns for fuzzy matching with per-field thresholds. */
  readonly fuzzy?: Readonly<Record<string, number>>;
  /** Blocking key columns (lowercase transform applied). */
  readonly blocking?: readonly string[];
  /** Overall fuzzy threshold (default 0.85). */
  readonly threshold?: number;
  /** Enable LLM scorer for borderline pairs. Requires OPENAI_API_KEY or ANTHROPIC_API_KEY in env. */
  readonly llmScorer?: boolean;
  /** Optional memory store. Used only when `memoryConfig.enabled` is true. */
  readonly memoryStore?: MemoryStore | null;
  /** Memory configuration. When omitted or `enabled: false`, memory is no-op. */
  readonly memoryConfig?: Partial<MemoryConfig>;
  /** Dataset label forwarded to memory hooks. Defaults to "<DataFrame>". */
  readonly derivedDataset?: string;
}

// ---------------------------------------------------------------------------
// Build config from shorthand options
// ---------------------------------------------------------------------------

function buildConfigFromOptions(options?: DedupeOptions): GoldenMatchConfig {
  if (options?.config) {
    if (options.memoryConfig) {
      return mergeMemory(options.config, options.memoryConfig);
    }
    return options.config;
  }

  const matchkeys: MatchkeyConfig[] = [];
  const threshold = options?.threshold ?? 0.85;

  // Exact matchkeys: one per column
  if (options?.exact) {
    for (const col of options.exact) {
      matchkeys.push(
        makeMatchkeyConfig({
          name: `exact_${col}`,
          type: "exact",
          fields: [
            makeMatchkeyField({
              field: col,
              transforms: ["lowercase", "strip"],
              scorer: "exact",
            }),
          ],
        }),
      );
    }
  }

  // Fuzzy matchkey: all fuzzy columns combined into one weighted matchkey
  if (options?.fuzzy) {
    const fuzzyEntries = Object.entries(options.fuzzy);
    if (fuzzyEntries.length > 0) {
      const fields: MatchkeyField[] = fuzzyEntries.map(([col, weight]) =>
        makeMatchkeyField({
          field: col,
          transforms: ["lowercase", "strip"],
          scorer: "jaro_winkler",
          weight,
        }),
      );
      matchkeys.push(
        makeMatchkeyConfig({
          name: "fuzzy_combined",
          type: "weighted",
          fields,
          threshold,
        }),
      );
    }
  }

  // Blocking config
  let blocking = makeBlockingConfig();
  if (options?.blocking && options.blocking.length > 0) {
    const keys: BlockingKeyConfig[] = options.blocking.map((col) => ({
      fields: [col],
      transforms: ["lowercase", "strip"],
    }));
    blocking = makeBlockingConfig({ keys });
  }

  const partial: Partial<GoldenMatchConfig> = {
    blocking,
    threshold,
  };
  if (matchkeys.length > 0) {
    (partial as Record<string, unknown>).matchkeys = matchkeys;
  }
  if (options?.llmScorer) {
    (partial as Record<string, unknown>).llmScorer = {
      enabled: true,
      autoThreshold: 0.9,
      candidateLo: 0.6,
      candidateHi: 0.9,
      batchSize: 10,
      maxWorkers: 4,
      mode: "pairwise",
    };
  }
  if (options?.memoryConfig) {
    (partial as Record<string, unknown>).memory = buildMemoryConfig(
      options.memoryConfig,
    );
  }
  return makeConfig(partial);
}

function buildMemoryConfig(p: Partial<MemoryConfig>): MemoryConfig {
  return {
    enabled: p.enabled ?? false,
    backend: p.backend ?? "memory",
    ...(p.path !== undefined ? { path: p.path } : {}),
    ...(p.dataset !== undefined ? { dataset: p.dataset } : {}),
    ...(p.reanchor !== undefined ? { reanchor: p.reanchor } : {}),
    ...(p.trust !== undefined ? { trust: p.trust } : {}),
    learning: p.learning ?? {
      thresholdMinCorrections: 10,
      weightsMinCorrections: 50,
    },
  };
}

function mergeMemory(
  cfg: GoldenMatchConfig,
  override: Partial<MemoryConfig>,
): GoldenMatchConfig {
  const base = cfg.memory ?? buildMemoryConfig({});
  const merged: MemoryConfig = {
    enabled: override.enabled ?? base.enabled,
    backend: override.backend ?? base.backend,
    ...(override.path ?? base.path
      ? { path: (override.path ?? base.path) as string }
      : {}),
    ...(override.dataset !== undefined
      ? { dataset: override.dataset }
      : base.dataset !== undefined
        ? { dataset: base.dataset }
        : {}),
    ...(override.reanchor !== undefined
      ? { reanchor: override.reanchor }
      : base.reanchor !== undefined
        ? { reanchor: base.reanchor }
        : {}),
    ...(override.trust !== undefined
      ? { trust: override.trust }
      : base.trust !== undefined
        ? { trust: base.trust }
        : {}),
    learning: override.learning ?? base.learning,
  };
  return { ...cfg, memory: merged };
}

// ---------------------------------------------------------------------------
// Public API: dedupe
// ---------------------------------------------------------------------------

/**
 * Deduplicate an array of row objects.
 *
 * Shorthand usage:
 * ```ts
 * const result = dedupe(rows, {
 *   exact: ["email"],
 *   fuzzy: { name: 0.85, address: 0.7 },
 *   blocking: ["zip"],
 *   threshold: 0.85,
 * });
 * ```
 *
 * Or provide a full config:
 * ```ts
 * const result = dedupe(rows, { config: myConfig });
 * ```
 */
export async function dedupe(
  rows: readonly Row[],
  options?: DedupeOptions,
): Promise<DedupeResult> {
  const config = buildConfigFromOptions(options);
  return runDedupePipeline(rows, config, {
    ...(options?.memoryStore !== undefined
      ? { memoryStore: options.memoryStore }
      : {}),
    ...(options?.derivedDataset !== undefined
      ? { derivedDataset: options.derivedDataset }
      : {}),
  });
}

// ---------------------------------------------------------------------------
// Public API: match
// ---------------------------------------------------------------------------

/**
 * Match target rows against reference rows.
 *
 * Same options as `dedupe()`. Returns matched/unmatched target rows.
 */
export async function match(
  target: readonly Row[],
  reference: readonly Row[],
  options?: DedupeOptions,
): Promise<MatchResult> {
  const config = buildConfigFromOptions(options);
  return runMatchPipeline(target, reference, config, {
    ...(options?.memoryStore !== undefined
      ? { memoryStore: options.memoryStore }
      : {}),
    ...(options?.derivedDataset !== undefined
      ? { derivedDataset: options.derivedDataset }
      : {}),
  });
}

// ---------------------------------------------------------------------------
// Public API: scoreStrings
// ---------------------------------------------------------------------------

/**
 * Score two strings using the specified scorer algorithm.
 *
 * @param a - First string.
 * @param b - Second string.
 * @param scorer - Scorer name (default: "jaro_winkler").
 *   Valid scorers: exact, jaro_winkler, levenshtein, token_sort,
 *   soundex_match, dice, jaccard, ensemble.
 * @returns Similarity score between 0.0 and 1.0.
 */
export function scoreStrings(
  a: string,
  b: string,
  scorer: string = "jaro_winkler",
): number {
  const result = scoreField(a, b, scorer);
  return result ?? 0.0;
}

// ---------------------------------------------------------------------------
// Public API: scorePairRecord
// ---------------------------------------------------------------------------

/**
 * Score a pair of row objects across specified fields using weighted
 * aggregation.
 *
 * @param rowA - First row.
 * @param rowB - Second row.
 * @param fields - Field configs specifying which fields to compare,
 *   transforms to apply, scorer to use, and weight.
 * @returns Weighted similarity score between 0.0 and 1.0.
 */
export function scorePairRecord(
  rowA: Row,
  rowB: Row,
  fields: readonly MatchkeyField[],
): number {
  return scorePair(rowA, rowB, fields);
}
