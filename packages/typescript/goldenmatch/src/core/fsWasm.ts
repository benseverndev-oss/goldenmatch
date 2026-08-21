/**
 * fsWasm.ts — synchronous, edge-safe Fellegi-Sunter block scoring backed by the
 * shared `goldenmatch-fs-core` Rust kernel compiled to wasm (`fs-wasm`).
 *
 * This is the SAME `fs_core::score_fs_pair` the Python `goldenmatch-native` wheel
 * runs, so FS block scoring is byte-identical across Python-native and TS-WASM by
 * construction (the 2026-07-17 fs-core cross-surface design). The parity gate
 * (`tests/parity/fs-wasm.parity.test.ts`) feeds the identical inputs the Python
 * NATIVE kernel scored (`scripts/emit_fs_wasm_fixture.py`, the oracle) and asserts
 * the same pairs.
 *
 * Same synchronous inlined-wasm pattern as `hnswWasm.ts` / `autoconfigWasm.ts`:
 * `initSync` over a base64-inlined wasm committed under `src/core/_wasm/`, so
 * `tsc`/`vitest`/`tsup` need no rust toolchain — only `scripts/build_fs_wasm.mjs`
 * (the regen step) does. Edge-safe: `atob`, no `node:*` / fetch / import.meta.url.
 *
 * Scope (mirrors the native/fs-wasm crate): scores an ALREADY-trained EMResult
 * over ALREADY-transformed field values — EM training and transforms stay
 * host-side, exactly as they stay Python-side. The zero-config FS shape (no NE,
 * no custom banding — what `auto_configure_probabilistic_df` emits) is the
 * default; the OPTIONAL `levelThresholds` (custom level-banding) + NE fields
 * (`neValues`/`neScorerIds`/`neThresholds`/`neWeights`) carry the negative
 * evidence + custom banding the shared `fs_core::score_fs_pair` kernel already
 * supports, marshaled exactly like the native pyo3 `score_block_pairs_fs`. When
 * they are absent the output is byte-identical to the zero-config path. (The
 * cross-batch exclude set stays host-side / unused here.)
 */
import {
  score_block_pairs_fs,
  train_em_from_counts,
  initSync,
} from "./_wasm/fsWasmBindings.js";
import { FS_WASM_BASE64 } from "./_wasm/fsWasmBytes.js";

// ---------------------------------------------------------------------------
// One-time synchronous wasm init (edge-safe: atob, no fs/fetch).
// ---------------------------------------------------------------------------

let initialized = false;

function base64ToBytes(b64: string): Uint8Array {
  // atob is available in browsers, Workers, and Node >= 18 — edge-safe.
  const bin = atob(b64);
  const len = bin.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

/** Force the one-time synchronous wasm init (idempotent). */
export function initFsWasm(): void {
  if (initialized) return;
  initSync({ module: base64ToBytes(FS_WASM_BASE64) });
  initialized = true;
}

// ---------------------------------------------------------------------------
// Scoring API
// ---------------------------------------------------------------------------

/**
 * A pre-trained, pre-transformed block ready for FS scoring — the JSON-boundary
 * shape the native `score_block_pairs_fs` kernel takes. `fieldValues[field][row]`
 * are the already-transformed values (`null` = missing/unobserved).
 */
export interface FsBlockScoringInput {
  /** Stable row ids (the `(a, b)` reported in output pairs). */
  readonly rowIds: readonly number[];
  /** Row counts per contiguous block; must sum to `rowIds.length`. */
  readonly blockSizes: readonly number[];
  /** `[field][row]` already-transformed values; `null` = unobserved. */
  readonly fieldValues: readonly (readonly (string | null)[])[];
  /** Per-field comparison scorer id (0=jaro_winkler … 3=exact, …). */
  readonly scorerIds: readonly number[];
  /** Per-field comparison level count (2 or 3). */
  readonly levels: readonly number[];
  /** Per-field partial-agreement similarity threshold. */
  readonly partialThresholds: readonly number[];
  /** `[field][level]` EM match weights (log2 Bayes factors). */
  readonly matchWeights: readonly (readonly number[])[];
  /** Posterior calibration on/off (else linear min-max normalize). */
  readonly calibrated: boolean;
  /** Prior log-odds weight (posterior calibration). */
  readonly priorW: number;
  /** Regular-weight normalization floor (min summed weight). */
  readonly minWeight: number;
  /** Regular-weight normalization span (max - min summed weight). */
  readonly weightRange: number;
  /** Emit pairs whose normalized score is at/above this. */
  readonly threshold: number;
  /**
   * ADDITIVE: per-field custom level-banding cutoffs (`fs_core`
   * `field_thresholds`), one entry per regular field. `null` (or a missing
   * entry) = use the default `levels`/`partialThreshold` banding for that field.
   * When present, a field's thresholds are the descending similarity cutoffs and
   * its `matchWeights` row must have `thresholds.length + 1` entries. Omit the
   * whole field (undefined/empty) to reproduce the zero-config behavior exactly.
   */
  readonly levelThresholds?: readonly (readonly number[] | null)[];
  /**
   * ADDITIVE: Fellegi-Sunter negative-evidence field values, `[neField][row]`
   * post-transform (`null` = unobserved), mirroring `fieldValues`. Must be
   * supplied together with `neScorerIds`/`neThresholds`/`neWeights` (all length
   * `neValues.length`). Omit (or pass empty) for no negative evidence.
   */
  readonly neValues?: readonly (readonly (string | null)[])[];
  /** ADDITIVE: per-NE-field comparison scorer id (0=jaro_winkler … 3=exact, 6=ensemble). */
  readonly neScorerIds?: readonly number[];
  /** ADDITIVE: per-NE-field firing threshold — the field fires when similarity is STRICTLY below this. */
  readonly neThresholds?: readonly number[];
  /** ADDITIVE: per-NE-field fired weight (normally negative) added to the pair's summed weight when it fires. */
  readonly neWeights?: readonly number[];
}

/** One scored within-block pair: `[a, b, score]` with `a < b`. */
export type FsScoredPair = [number, number, number];

/**
 * Score every within-block pair via the fs-core wasm kernel and return those
 * at/above `threshold` as `[a, b, score]` (`a < b`). Byte-identical to the
 * Python native `score_block_pairs_fs`.
 */
export function scoreBlockPairsFs(input: FsBlockScoringInput): FsScoredPair[] {
  initFsWasm();

  const nRows = input.rowIds.length;
  const nFields = input.fieldValues.length;

  const rowIds = BigInt64Array.from(input.rowIds, (v) => BigInt(v));
  const blockSizes = Uint32Array.from(input.blockSizes);

  // Column-major flat values + null flags (field 0 all rows, then field 1 …),
  // exactly the layout `reshape_columns` expects (`nulls[f * nRows + r]`).
  const flat: string[] = [];
  const nulls = new Uint8Array(nFields * nRows);
  for (let f = 0; f < nFields; f++) {
    const col = input.fieldValues[f]!;
    for (let r = 0; r < nRows; r++) {
      const v = col[r];
      if (v === null || v === undefined) {
        nulls[f * nRows + r] = 1;
        flat.push("");
      } else {
        flat.push(v);
      }
    }
  }

  const scorerIds = Uint8Array.from(input.scorerIds);
  const levels = Uint8Array.from(input.levels);
  const partialThresholds = Float64Array.from(input.partialThresholds);

  // Ragged per-field weight rows flattened + a lengths vector.
  const weightsFlat: number[] = [];
  const weightsLens = new Uint32Array(input.matchWeights.length);
  for (let f = 0; f < input.matchWeights.length; f++) {
    const row = input.matchWeights[f]!;
    weightsLens[f] = row.length;
    for (let k = 0; k < row.length; k++) weightsFlat.push(row[k]!);
  }

  // ADDITIVE: custom level-banding, ragged flat + a per-field lengths vector
  // with a -1 sentinel meaning "no custom thresholds for this field" (=> the
  // kernel uses the default levels/partialThreshold banding). An empty lens
  // vector (no levelThresholds supplied) reproduces the zero-config default.
  const levelThresholdsFlat: number[] = [];
  let levelThresholdsLens: Int32Array;
  if (input.levelThresholds !== undefined && input.levelThresholds.length > 0) {
    levelThresholdsLens = new Int32Array(nFields);
    for (let f = 0; f < nFields; f++) {
      const cuts = input.levelThresholds[f];
      if (cuts === null || cuts === undefined) {
        levelThresholdsLens[f] = -1;
      } else {
        levelThresholdsLens[f] = cuts.length;
        for (let k = 0; k < cuts.length; k++) levelThresholdsFlat.push(cuts[k]!);
      }
    }
  } else {
    levelThresholdsLens = new Int32Array(0);
  }

  // ADDITIVE: negative evidence, column-major values (like fieldValues) + the
  // parallel scorer/threshold/weight arrays. nNe = 0 => no negative evidence.
  const neValues = input.neValues ?? [];
  const nNe = neValues.length;
  const neFlat: string[] = [];
  const neNulls = new Uint8Array(nNe * nRows);
  for (let k = 0; k < nNe; k++) {
    const col = neValues[k]!;
    for (let r = 0; r < nRows; r++) {
      const v = col[r];
      if (v === null || v === undefined) {
        neNulls[k * nRows + r] = 1;
        neFlat.push("");
      } else {
        neFlat.push(v);
      }
    }
  }
  const neScorerIds = Uint8Array.from(input.neScorerIds ?? []);
  const neThresholds = Float64Array.from(input.neThresholds ?? []);
  const neWeights = Float64Array.from(input.neWeights ?? []);

  const json = score_block_pairs_fs(
    rowIds,
    blockSizes,
    flat,
    nulls,
    nFields,
    scorerIds,
    levels,
    partialThresholds,
    Float64Array.from(weightsFlat),
    weightsLens,
    input.calibrated,
    input.priorW,
    input.minWeight,
    input.weightRange,
    input.threshold,
    Float64Array.from(levelThresholdsFlat),
    levelThresholdsLens,
    neFlat,
    neNulls,
    nNe,
    neScorerIds,
    neThresholds,
    neWeights,
  );

  const parsed = JSON.parse(json) as Array<[number, number, number]>;
  return parsed.map(([a, b, s]) => [a, b, s] as FsScoredPair);
}

// ---------------------------------------------------------------------------
// Fellegi-Sunter training from COUNTED comparison vectors
// ---------------------------------------------------------------------------
//
// Phase 1b of docs/superpowers/specs/2026-08-13-fs-em-rust-single-source-design.md.
//
// `trainEM` in ./probabilistic.ts is a hand-port of the Python trainer -- the
// third copy of a loop that should have one. This is the shared kernel, reached
// from TS.
//
// It exposes the COUNTED shape only, and deliberately does NOT reroute
// `trainEM`: that function supports negative-evidence dimensions and the Rust
// `em_core` explicitly does not yet, so routing it through the kernel today
// would silently drop NE and return a model the config never asked for. The
// counted path has no such gap -- it refuses NE on every surface. So this ships
// the kernel + parity as an alternative surface first, exactly how this file's
// own scoring kernel shipped before the TS scorer was rerouted onto it.

/** One distinct comparison vector and how many pairs had it. */
export interface FsPatternCount {
  /** One level per field, ordered by the matchkey's fields; -1 = unobserved. */
  readonly levels: readonly number[];
  /** How many pairs carried this vector. A COUNT, not a proportion. */
  readonly count: number;
}

export interface FsTrainFromCountsInput {
  /** Number of levels per field, ordered by the matchkey's fields. */
  readonly nLevels: readonly number[];
  readonly patterns: readonly FsPatternCount[];
  /**
   * `u` per field, estimated from RANDOM (unblocked) pairs -- which this
   * function never sees, so it cannot derive them. Half the likelihood ratio.
   */
  readonly uProbs: readonly (readonly number[])[];
  /**
   * Fields this blocking pass makes uninformative. Constant within a pass,
   * which is why the counts need no pass column.
   */
  readonly conditioned?: readonly boolean[];
  readonly maxIterations?: number;
  readonly convergence?: number;
}

export interface FsTrainedModel {
  readonly m_probs: number[][];
  readonly u_probs: number[][];
  readonly match_weights: number[][];
  readonly converged: boolean;
  readonly iterations: number;
  readonly proportion_matched: number;
}

/**
 * Train a Fellegi-Sunter model from counted comparison vectors, in the shared
 * Rust kernel.
 *
 * The counts may come from anywhere that can group -- a SQL engine's `GROUP BY`
 * over the gamma columns is the intended source -- because the E-step reads only
 * the comparison vector, so pairs sharing one are interchangeable and every
 * M-step quantity is a sum linear in how many pairs share it. Collapsing is
 * EXACT, not a sample.
 *
 * The counts are COUNTS: their sum is the number of pairs represented. EM's
 * `1e-6` smoothing is additive, so rescaling them moves the model -- in the
 * low-probability cells, which is exactly where FS weights are largest.
 *
 * Throws on invalid input rather than returning a partial model: a wrong level
 * or a zero count yields a perfectly plausible set of probabilities, and nothing
 * downstream could tell.
 */
export function trainFsFromCounts(
  input: FsTrainFromCountsInput,
): FsTrainedModel {
  initFsWasm();

  const nFields = input.nLevels.length;
  if (nFields === 0) throw new Error("nLevels is empty; no fields to train");
  if (input.uProbs.length !== nFields) {
    throw new Error(
      `uProbs has ${input.uProbs.length} vectors but there are ${nFields} fields`,
    );
  }
  const conditioned = input.conditioned ?? new Array(nFields).fill(false);
  if (conditioned.length !== nFields) {
    throw new Error(
      `conditioned has ${conditioned.length} entries but there are ${nFields} fields`,
    );
  }

  const flat: number[] = [];
  for (const p of input.patterns) {
    if (p.levels.length !== nFields) {
      throw new Error(
        `a comparison vector has ${p.levels.length} entries but there are ` +
          `${nFields} fields -- vectors must be ordered by the matchkey's fields`,
      );
    }
    flat.push(...p.levels);
  }

  const uFlat: number[] = [];
  input.uProbs.forEach((u, j) => {
    if (u.length !== input.nLevels[j]) {
      throw new Error(
        `uProbs[${j}] has ${u.length} levels but the field has ${input.nLevels[j]}`,
      );
    }
    uFlat.push(...u);
  });

  const json = train_em_from_counts(
    Uint32Array.from(input.nLevels),
    Int32Array.from(flat),
    Float64Array.from(input.patterns.map((p) => p.count)),
    Float64Array.from(uFlat),
    Uint8Array.from(conditioned.map((b) => (b ? 1 : 0))),
    input.maxIterations ?? 20,
    input.convergence ?? 0.001,
  );

  const out = JSON.parse(json) as FsTrainedModel & { error?: string };
  // The kernel fails SOFT to an error envelope (every other surface reads it
  // that way); at a typed TS boundary a thrown Error is the honest shape.
  if (out.error) throw new Error(`FS training refused the input: ${out.error}`);
  return out;
}
