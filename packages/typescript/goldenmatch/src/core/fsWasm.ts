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
 * host-side, exactly as they stay Python-side. This entry covers the zero-config
 * FS shape (no NE, no custom banding, no cross-batch exclude — what
 * `auto_configure_probabilistic_df` emits); NE / custom `level_thresholds` grow
 * from here, like the native kernel.
 */
import { score_block_pairs_fs, initSync } from "./_wasm/fsWasmBindings.js";
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
  );

  const parsed = JSON.parse(json) as Array<[number, number, number]>;
  return parsed.map(([a, b, s]) => [a, b, s] as FsScoredPair);
}
