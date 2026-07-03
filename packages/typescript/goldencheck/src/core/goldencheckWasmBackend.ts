/**
 * goldencheckWasmBackend.ts — lean runtime registry for the OPT-IN goldencheck
 * wasm backend. Edge-safe: no `node:` imports, and (critically) NO value import
 * of the heavy `goldencheckWasm` module — only `import type`, which tsc/esbuild
 * fully erase. So a relation importing this registry pulls ZERO wasm bytes into
 * the default bundle.
 *
 * The heavy `goldencheck/core/wasm` subpath registers a backend here via
 * `enableGoldencheckWasm()`; until then `getGoldencheckWasmBackend()` returns
 * null and callers run their pure-TS path. Mirrors goldenmatch's
 * `autoconfigWasmBackend` split and the Python default-OFF native gate.
 */

/** Nullable-string column (JSON null -> null), as the kernels consume it. */
export type WasmColumn = readonly (string | null)[];

/**
 * The shared deep-profiling surface the wasm core implements. Surface 1 wires
 * the two flagship combinatorial kernels; the others are available on the heavy
 * module for future reroutes.
 */
export interface GoldencheckWasmBackend {
  /** Minimal composite keys among `columns` (subsets of column indices). */
  compositeKeySearch(
    columns: readonly WasmColumn[],
    maxSize: number,
    singleUnique: readonly boolean[],
  ): number[][];
  /** Strict single-column FDs `(detIdx, depIdx)` among `columns`. */
  discoverFunctionalDependencies(columns: readonly WasmColumn[]): Array<[number, number]>;
  /** Approximate FDs `(detIdx, depIdx, nViolations)` at `minConfidence`. */
  discoverApproximateFds(
    columns: readonly WasmColumn[],
    minConfidence: number,
  ): Array<[number, number, number]>;
  /** Row indices where `dep` deviates from its per-`det`-group mode (ascending). */
  fdViolationRows(det: WasmColumn, dep: WasmColumn): number[];
  /** Edit-distance-close value clusters (indices into `values`), size >= 2. */
  nearDuplicateClusters(values: readonly string[], minSimilarity: number): number[][];
  /** Benford leading-digit histogram (9 bins, digits 1..9) over finite positives. */
  benfordLeadingDigits(values: readonly (number | null)[]): number[];
}

let _backend: GoldencheckWasmBackend | null = null;

/** Register the wasm backend (called by the opt-in subpath's enable fn). */
export function setGoldencheckWasmBackend(backend: GoldencheckWasmBackend): void {
  _backend = backend;
}

/** The registered backend, or null when wasm is not enabled (the default). */
export function getGoldencheckWasmBackend(): GoldencheckWasmBackend | null {
  return _backend;
}

/** Clear the backend — restores the pure-TS path (test isolation / opt-out). */
export function disableGoldencheckWasm(): void {
  _backend = null;
}
