/**
 * graphWasmBackend.ts — lean runtime registry for the OPT-IN graph
 * (connected-components) wasm backend. Edge-safe: no `node:` imports, and
 * (unlike the heavy `graphWasm` loader) it pulls ZERO wasm bytes into the
 * bundle — it owns only the registry singleton + the backend shape.
 *
 * The heavy `goldenmatch/core/graph-wasm` subpath registers a backend here via
 * `enableGraphWasm()`; until then `getGraphWasmBackend()` returns null and the
 * clustering step runs its pure-TS union-find (the faithful fallback). Mirrors
 * the `sketchWasmBackend` / `suggestWasmBackend` split and Python's default-OFF
 * native gate.
 */

/** The shared clustering primitive the wasm core implements. */
export interface GraphWasmBackend {
  /**
   * Connected components of the graph with vertices `allIds` and edges `pairs`
   * (the pair endpoints; scores are irrelevant to CC). Returns one array of
   * member ids per component (singletons included). Order is unspecified — the
   * partition is unique, so callers canonicalize.
   */
  connectedComponents(
    pairs: readonly (readonly [number, number])[],
    allIds: readonly number[],
  ): number[][];
}

let _backend: GraphWasmBackend | null = null;

/** Register the wasm backend (called by the opt-in subpath's enable fn). */
export function setGraphWasmBackend(backend: GraphWasmBackend): void {
  _backend = backend;
}

/** The registered backend, or null when wasm is not enabled (the default). */
export function getGraphWasmBackend(): GraphWasmBackend | null {
  return _backend;
}

/** Clear the backend — restores the pure-TS path (test isolation / opt-out). */
export function disableGraphWasm(): void {
  _backend = null;
}

/** True when the opt-in wasm backend is currently registered. */
export function isGraphWasmEnabled(): boolean {
  return _backend !== null;
}
