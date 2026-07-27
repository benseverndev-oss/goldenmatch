/**
 * backend.ts — opt-in WASM aggregate backend registry. Edge-safe: no node:* here.
 *
 * Consulted by aggregate.ts's histogram/quantile when a backend is registered;
 * otherwise pure-TS. Mirrors goldenmatch's wasm/backend.ts (the
 * setSyncEmbedder(null) module-singleton pattern) for test isolation.
 */
import { createBackendRegistry } from "goldenmatch-wasm-runtime";

/** A WASM-backed (or stub) aggregate kernel. Null filtering is the caller's. */
export interface AnalysisBackend {
  /**
   * Equal-width histogram as `[leftEdge, count]` pairs over the GENERAL path
   * (the caller has already filtered nulls and handled empty/all-equal/single).
   */
  histogram(values: Float64Array, bins: number): Array<[number, number]>;
  /** Linear-interpolation quantile (caller has filtered nulls). */
  quantile(values: Float64Array, q: number): number;
  /** Discrete cluster-size histogram: counts of sizes ==1/==2/==3/>=4 (4 buckets). */
  clusterSizeHistogram(sizes: Float64Array): number[];

  // ── Frame kernels (Wave 1b) — shared interning canon over plain buffers ──
  // The caller classifies each column + marshals it to typed buffers (numeric =>
  // Float64Array, string => Arrow utf8 offsets+bytes) with a `validity` byte per
  // row (0 = null). `internNumeric`/`internString` return dense value-ids (as
  // f64, exact ints); the caller feeds those to `distinctCountIds` /
  // `duplicateRowRatioIds`. Same canon (canon_f64_bits NaN/-0 fold; byte string
  // equality; null id 0) the Python/native path interns with.
  /** Intern a numeric column to dense value-ids. `validity[i]==0` => null. */
  internNumeric(values: Float64Array, validity: Uint8Array): Float64Array;
  /** Intern a UTF-8 column (Arrow utf8 `offsets`[n+1] + `bytes`) to dense value-ids. */
  internString(offsets: Uint32Array, bytes: Uint8Array, validity: Uint8Array): Float64Array;
  /** Distinct-value count over interned ids (null id counts). */
  distinctCountIds(ids: Float64Array): number;
  /** Exact-duplicate row ratio over `nCols` COLUMN-MAJOR interned id-columns. */
  duplicateRowRatioIds(idsFlat: Float64Array, nCols: number, nRows: number): number;
}

const _registry = createBackendRegistry<AnalysisBackend>();

export function setAnalysisBackend(b: AnalysisBackend | null): void {
  _registry.set(b);
}

export function getAnalysisBackend(): AnalysisBackend | null {
  return _registry.get();
}
