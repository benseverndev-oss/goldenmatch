/**
 * graphWasm.ts — synchronous, edge-safe loader for the graph-core
 * connected-components kernel, compiled to wasm.
 *
 * This is the SAME kernel the Python native path and the DuckDB / Postgres
 * native UDFs run, so the cluster PARTITION is identical across surfaces —
 * proven by the shared golden vector (`tests/parity/fixtures/graph/
 * graph_golden.json`, the same file `graph-core/tests/golden.rs` checks).
 * Importing this module and calling `enableGraphWasm()` reroutes the clustering
 * step (`cluster.ts::buildClusters`) off its hand-written union-find onto this
 * one core.
 *
 * Edge-safe: no `node:*`. The wasm is inlined as base64 and instantiated
 * synchronously via `initSync`. Row ids are small (0-based positions), so edges
 * cross as `Int32Array`s; the ragged `number[][]` result crosses back as JSON.
 */
import {
  initSync,
  connected_components,
} from "./_wasm/graphWasmBindings.js";
import { GRAPH_WASM_BASE64 } from "./_wasm/graphWasmBytes.js";
import { setGraphWasmBackend, disableGraphWasm } from "./graphWasmBackend.js";

let initialized = false;

function base64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64); // browsers, Workers, Node >= 18 — edge-safe
  const len = bin.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

function ensureInit(): void {
  if (initialized) return;
  initSync({ module: base64ToBytes(GRAPH_WASM_BASE64) });
  initialized = true;
}

/**
 * Connected components of `allIds` under `pairs`, via the shared graph-core
 * kernel. Returns one member-id array per component (order unspecified; the
 * partition is unique).
 */
export function connectedComponents(
  pairs: readonly (readonly [number, number])[],
  allIds: readonly number[],
): number[][] {
  ensureInit();
  const n = pairs.length;
  const a = new Int32Array(n);
  const b = new Int32Array(n);
  for (let i = 0; i < n; i++) {
    a[i] = pairs[i]![0];
    b[i] = pairs[i]![1];
  }
  const ids = Int32Array.from(allIds);
  return JSON.parse(connected_components(a, b, ids));
}

/**
 * Route the clustering step (`cluster.ts::buildClusters`) off its pure-TS
 * union-find onto the shared graph-core kernel. Idempotent. Call
 * `disableGraphWasm()` to revert (test isolation / opt-out).
 */
export function enableGraphWasm(): void {
  ensureInit();
  setGraphWasmBackend({ connectedComponents });
}

export { disableGraphWasm };
