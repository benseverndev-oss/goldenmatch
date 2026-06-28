/**
 * goldengraphWasmBackend.ts — lean runtime registry for the OPT-IN GoldenGraph
 * knowledge-graph wasm backend. Edge-safe: no `node:` imports, zero wasm bytes —
 * it owns only the registry singleton + the kernel I/O shape (the 4 graph+query
 * ops as raw JSON-string boundaries).
 *
 * The heavy `goldengraph/wasm` subpath registers a backend here via
 * `enableGoldengraphWasm()`; until then the query functions in `index.ts` throw
 * an actionable error (mirrors goldenprofile + Python's native-wheel requirement).
 */

/** The JSON boundaries the wasm kernel implements (one core). */
export interface GoldengraphWasmBackend {
  /** `(mentions, edges, resolution) -> graph` JSON. */
  buildGraph(mentionsJson: string, edgesJson: string, resolutionJson: string): string;
  /** `(graph, seeds, hops) -> subgraph` JSON. */
  neighborhood(graphJson: string, seedsJson: string, hops: number): string;
  /** `(graph, name) -> entity-ids` JSON. */
  seedsByName(graphJson: string, name: string): string;
  /** `(graph) -> communities` JSON. */
  communities(graphJson: string): string;
  /** `(snapshot|"" , batch) -> snapshot` JSON (bitemporal store append). */
  storeAppend(snapshotJson: string, batchJson: string): string;
  /** `(snapshot, valid_t, tx_t) -> graph` JSON (bitemporal slice). */
  storeAsOf(snapshotJson: string, validT: number, txT: number): string;
  /** `(snapshot, id) -> history-events` JSON. */
  storeHistory(snapshotJson: string, id: number): string;
}

let _backend: GoldengraphWasmBackend | null = null;

/** Register the wasm backend (called by the opt-in subpath's enable fn). */
export function setGoldengraphWasmBackend(backend: GoldengraphWasmBackend): void {
  _backend = backend;
}

/** The registered backend, or null when wasm is not enabled (the default). */
export function getGoldengraphWasmBackend(): GoldengraphWasmBackend | null {
  return _backend;
}

/** Clear the backend — restores the refusing state (test isolation / opt-out). */
export function disableGoldengraphWasm(): void {
  _backend = null;
}

/** True when the opt-in wasm backend is currently registered. */
export function isGoldengraphWasmEnabled(): boolean {
  return _backend !== null;
}

/** The registered backend, or throw the actionable "enable wasm" error. */
export function requireGoldengraphWasmBackend(): GoldengraphWasmBackend {
  if (_backend === null) {
    throw new Error(
      "GoldenGraph requires the wasm backend. " +
        'Import { enableGoldengraphWasm } from "goldengraph/wasm" and call it ' +
        "once before any query.",
    );
  }
  return _backend;
}
