/**
 * clusterWasmBackend.ts — lean runtime registry for the OPT-IN cluster
 * (MST-split + confidence) wasm backend. Edge-safe: no `node:` imports, and
 * (unlike the heavy `clusterWasm` loader) it pulls ZERO wasm bytes into the
 * bundle — it owns only the registry singleton + the backend shape.
 *
 * The heavy `goldenmatch/core/cluster-wasm` subpath registers a backend here via
 * `enableClusterWasm()`; until then `getClusterWasmBackend()` returns null and
 * `splitOversizedCluster` / `computeClusterConfidence` run their pure-TS impls
 * (the faithful fallback). Mirrors the `graphWasmBackend` split and Python's
 * default-OFF native gate.
 */

/** `[min_edge, avg_edge, connectivity, bottleneck_pair, confidence]`. */
export type ClusterConfidenceTuple = [
  number | null,
  number | null,
  number,
  readonly [number, number] | null,
  number,
];

/** The shared clustering primitives the wasm core implements. */
export interface ClusterWasmBackend {
  /**
   * Max-weight spanning tree, drop the single weakest MST edge, return the
   * resulting components — one array of member ids per component. `edges` MUST
   * arrive in pair_scores iteration order (stable-sort + first-min tie-breaks
   * depend on it). Returns `[]` when the MST is empty (unsplittable). The
   * partition is deterministic, so it matches the pure-TS impl exactly.
   */
  mstSplitComponents(
    members: readonly number[],
    edges: readonly (readonly [number, number, number])[],
  ): number[][];

  /**
   * Confidence metrics for one cluster of `size` members whose scored pairs are
   * `edges` (in pair_scores iteration order). Returns the
   * `[minEdge, avgEdge, connectivity, bottleneck, confidence]` tuple.
   */
  clusterConfidence(
    edges: readonly (readonly [number, number, number])[],
    size: number,
  ): ClusterConfidenceTuple;
}

let _backend: ClusterWasmBackend | null = null;

/** Register the wasm backend (called by the opt-in subpath's enable fn). */
export function setClusterWasmBackend(backend: ClusterWasmBackend): void {
  _backend = backend;
}

/** The registered backend, or null when wasm is not enabled (the default). */
export function getClusterWasmBackend(): ClusterWasmBackend | null {
  return _backend;
}

/** Clear the backend — restores the pure-TS path (test isolation / opt-out). */
export function disableClusterWasm(): void {
  _backend = null;
}

/** True when the opt-in wasm backend is currently registered. */
export function isClusterWasmEnabled(): boolean {
  return _backend !== null;
}
