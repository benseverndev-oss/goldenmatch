/**
 * lshBlocker.ts — MinHash/LSH near-duplicate blocking (#1081).
 *
 * Edge-safe: no Node.js imports. Pure-TS port of the Python
 * `core/lsh_blocker.py::MinHashLSHBlocker`, over an in-memory `string[]` of
 * record texts (the Python sibling is Polars-coupled; this TS utility keeps the
 * same bucketing/pairing semantics without a DataFrame layer).
 *
 * Shingle each text, MinHash it, bucket the signature into bands, and group
 * records by `(band_idx, bucket)`. Records sharing >= 1 bucket are candidates; a
 * pair colliding in several bands is de-duplicated via a canonical `"min,max"`
 * pair key. Empty / whitespace-only texts have nothing to block on (their
 * signature is all-u64::MAX) and are dropped by comparing against a
 * deterministic sentinel.
 */

import { optimalBands, sketchBandHashes } from "./sketch.js";

/** Resolved MinHash/LSH parameters. Mirrors Python `LSHKeyConfig` (resolved). */
export interface MinHashLSHConfig {
  /** Shingle granularity: `"char"` (code points) or `"word"` (tokens). */
  readonly mode: string;
  /** Shingle size (k-gram). Must be >= 1. */
  readonly k: number;
  /** Number of MinHash permutations (signature length). */
  readonly numPerms: number;
  /**
   * Number of LSH bands. Provide this OR `threshold` (resolved via
   * `optimalBands`). `numPerms` must be divisible by `numBands`.
   */
  readonly numBands?: number;
  /** Similarity threshold that drives `optimalBands` when `numBands` is absent. */
  readonly threshold?: number;
  /** splitmix64 seed for the permutation coefficients. */
  readonly seed: bigint;
}

/** Canonical `(min, max)` pair key, matching the project-wide invariant. */
function canonicalPairKey(a: number, b: number): string {
  return a < b ? `${a},${b}` : `${b},${a}`;
}

export class MinHashLSHBlocker {
  readonly mode: string;
  readonly k: number;
  readonly numPerms: number;
  readonly numBands: number;
  readonly seed: bigint;

  constructor(
    mode: string,
    k: number,
    numPerms: number,
    numBands: number,
    seed: bigint,
  ) {
    this.mode = mode;
    this.k = k;
    this.numPerms = numPerms;
    this.numBands = numBands;
    this.seed = seed;
  }

  /**
   * Construct from a (possibly threshold-driven) config. When `numBands` is
   * given it is used directly; otherwise `optimalBands(numPerms, threshold)`
   * picks the `(b, r)` split.
   */
  static fromConfig(cfg: MinHashLSHConfig): MinHashLSHBlocker {
    let numBands: number;
    if (cfg.numBands !== undefined) {
      numBands = cfg.numBands;
    } else {
      if (cfg.threshold === undefined) {
        throw new Error(
          "MinHashLSHBlocker.fromConfig requires either numBands or threshold",
        );
      }
      [numBands] = optimalBands(cfg.numPerms, cfg.threshold);
    }
    return new MinHashLSHBlocker(cfg.mode, cfg.k, cfg.numPerms, numBands, cfg.seed);
  }

  /**
   * Band hashes of an empty record (all-u64::MAX signature). Empty /
   * whitespace-only texts all produce this deterministic sentinel; we detect and
   * drop them by comparison (a non-empty record cannot produce an all-MAX
   * signature, so the comparison is exact).
   */
  private emptySentinel(): bigint[] {
    return sketchBandHashes(
      "",
      this.mode,
      this.k,
      this.numPerms,
      this.numBands,
      this.seed,
    );
  }

  /**
   * Map `"band_idx,bucket"` -> row positions, skipping empty / whitespace-only
   * rows. The key encodes the band index so identical bucket hashes in
   * different bands stay separate groups.
   */
  buckets(texts: readonly string[]): Map<string, number[]> {
    const sentinel = this.emptySentinel();
    const sentinelKey = sentinel.join(",");
    const groups = new Map<string, number[]>();

    for (let rowIdx = 0; rowIdx < texts.length; rowIdx++) {
      const bands = sketchBandHashes(
        texts[rowIdx]!,
        this.mode,
        this.k,
        this.numPerms,
        this.numBands,
        this.seed,
      );
      // empty / whitespace-only: no content to block on.
      if (bands.join(",") === sentinelKey) continue;
      for (let bandIdx = 0; bandIdx < bands.length; bandIdx++) {
        const key = `${bandIdx},${bands[bandIdx]!}`;
        const existing = groups.get(key);
        if (existing !== undefined) existing.push(rowIdx);
        else groups.set(key, [rowIdx]);
      }
    }
    return groups;
  }

  /**
   * De-duplicated `(min, max)` candidate pairs across all bands, as a set of
   * canonical `"min,max"` string keys. A pair colliding in several bands is
   * offered once.
   */
  candidatePairs(texts: readonly string[]): Set<string> {
    const pairs = new Set<string>();
    for (const members of this.buckets(texts).values()) {
      if (members.length < 2) continue;
      for (let i = 0; i < members.length; i++) {
        for (let j = i + 1; j < members.length; j++) {
          pairs.add(canonicalPairKey(members[i]!, members[j]!));
        }
      }
    }
    return pairs;
  }
}
