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
}

const _registry = createBackendRegistry<AnalysisBackend>();

export function setAnalysisBackend(b: AnalysisBackend | null): void {
  _registry.set(b);
}

export function getAnalysisBackend(): AnalysisBackend | null {
  return _registry.get();
}
